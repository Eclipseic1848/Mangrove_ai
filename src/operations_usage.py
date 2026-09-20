"""可信调用记录的分用户统计；不混加任务累计用量。"""
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit
import json
import os
import sqlite3
import time
import csv
import io
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator
from fastapi import HTTPException
from src.model_connections.catalog import PRESETS_BY_ID


class UsageFilters(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: date
    end: date
    search: str = Field(default='', max_length=80)
    page: int = Field(default=1, ge=1, le=10000)
    page_size: Literal[10, 20, 50, 100] = 10
    currency: Literal['CNY', 'USD'] = 'CNY'
    model: str = Field(default='', max_length=200)
    api_format: str = Field(default='', max_length=80)
    sort: Literal['total_tokens', 'cost', 'requests', 'last_used'] = 'total_tokens'
    direction: Literal['asc', 'desc'] = 'desc'

    @model_validator(mode='after')
    def check_dates(self):
        if not 0 <= (self.end - self.start).days <= 365:
            raise ValueError('时间范围须为1至366天')
        return self


def prices():
    try:
        path = Path(os.environ.get('MANGROVE_TOKEN_PRICES_FILE') or Path(__file__).with_name('operations_prices.json'))
        if path.stat().st_size > 256000:
            raise ValueError('配置过大')
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or not isinstance(value.get('models'), list) or not isinstance(value.get('paths'), dict):
            raise ValueError('配置结构无效')
        for key in ('version', 'notice', 'exchange_note', 'unit'):
            if not isinstance(value.get(key), str) or not value[key].strip():
                raise ValueError('配置说明缺失')
        fx = Decimal(value['usd_to_cny'])
        if not fx.is_finite() or not Decimal('0.0001') <= fx < 1000:
            raise ValueError('汇率无效')
        seen = set()
        for item in value['models']:
            pair = (item['model'], item['host'])
            if pair in seen or not all(isinstance(key, str) and key for key in pair):
                raise ValueError('型号重复或为空')
            seen.add(pair)
            if 'max_input' in item and (type(item['max_input']) is not int or not 1 <= item['max_input'] <= 10000000):
                raise ValueError('输入范围无效')
            allowed_paths = value['paths'][item['host']]
            if not isinstance(allowed_paths, list) or not allowed_paths or not all(isinstance(p, str) and (p == '' or p.startswith('/')) for p in allowed_paths):
                raise ValueError('服务路径无效')
            if item['currency'] not in ('CNY', 'USD') or not isinstance(item['source'], str) or not item['source'].startswith('https://'):
                raise ValueError('价格来源无效')
            for field in ('input', 'output'):
                amount = Decimal(item[field])
                if not amount.is_finite() or not (amount == 0 or Decimal('0.000001') <= amount <= 1000000):
                    raise ValueError('单价无效')
        supported = {(model, urlsplit(preset.base_url).hostname)
                     for preset in PRESETS_BY_ID.values() for model in preset.models}
        # 计价目录跟随平台支持范围；历史调用仍保留，不能借旧价格扩大支持范围。
        value['models'] = [item for item in value['models'] if (item['model'], item['host']) in supported]
        priced = {item['model'] for item in value['models']}
        value['unpriced_models'] = [model for preset in PRESETS_BY_ID.values() for model in preset.models if model not in priced]
        return value
    except (OSError, ValueError, KeyError, TypeError, InvalidOperation):
        # 配置损坏不能静默变成免费或继续使用未确认的价格。
        raise HTTPException(503, '参考价格配置暂不可用，请联系管理员检查') from None


def query(conn, actor, filters, *, export=False):
    start = datetime.combine(filters.start, datetime.min.time(), timezone(timedelta(hours=8)))
    end = datetime.combine(filters.end + timedelta(days=1), datetime.min.time(), start.tzinfo)
    # 复用用户元数据可见范围；不能通过请求参数扩大角色范围。
    scope, params = ('1=1', []) if actor['role'] == 'super_admin' else (
        "(u.role='user' OR u.user_id=?)", [actor['user_id']])
    catalog = prices()
    deadline = time.monotonic() + 5
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
    try:
        rows = conn.execute(f"""
        SELECT v.owner_user_id AS user_id, COALESCE(u.username,'已删除用户') AS username,
            COALESCE(u.display_name,u.username,'已删除用户') AS name,
            g.model, g.api_format, g.base_url, g.locality,
            MAX(v.input_tokens) AS max_input,
            SUM(v.request_count) AS requests,
            SUM(CASE WHEN v.input_tokens>=0 THEN v.input_tokens END) AS input_tokens,
            SUM(CASE WHEN v.output_tokens>=0 THEN v.output_tokens END) AS output_tokens,
            SUM(CASE WHEN v.total_tokens>=0 THEN v.total_tokens END) AS total_tokens,
            SUM(CASE WHEN v.total_tokens>=0 AND v.input_tokens>=0 AND v.output_tokens>=0 THEN 0 ELSE v.request_count END) AS unknown_requests,
            SUM(CASE WHEN v.input_tokens>=0 AND v.output_tokens>=0 THEN v.input_tokens ELSE 0 END) AS priced_input,
            SUM(CASE WHEN v.input_tokens>=0 AND v.output_tokens>=0 THEN v.output_tokens ELSE 0 END) AS priced_output,
            SUM(CASE WHEN v.input_tokens>=0 AND v.output_tokens>=0 THEN v.request_count ELSE 0 END) AS complete_requests,
            MAX(julianday(v.created_at)) AS last_used_jd
        FROM model_provider_usage v LEFT JOIN users u ON u.user_id=v.owner_user_id
        LEFT JOIN model_connection_grants g ON g.grant_id=v.grant_id AND g.owner_user_id=v.owner_user_id
            AND g.task_id=v.task_id AND g.revision=v.revision AND g.run_id=v.run_id AND g.connection_id=v.connection_id
        WHERE {scope} AND julianday(v.created_at)>=julianday(?) AND julianday(v.created_at)<julianday(?)
            AND (substr(v.created_at,-1)='Z' OR substr(v.created_at,-6,1) IN ('+','-'))
            AND (instr(lower(COALESCE(u.username,'')),lower(?))>0 OR instr(lower(COALESCE(u.display_name,'')),lower(?))>0 OR instr(v.owner_user_id,?)>0)
            AND (?='' OR g.model=?) AND (?='' OR g.api_format=?)
        GROUP BY v.owner_user_id,g.model,g.api_format,g.base_url,g.locality,v.input_tokens
        LIMIT 10001
        """, [*params, start.isoformat(), end.isoformat(), *[filters.search.strip()]*3,
            filters.model, filters.model, filters.api_format, filters.api_format]).fetchall()
    except sqlite3.OperationalError as error:
        if 'interrupted' in str(error):
            raise HTTPException(503, '统计范围较大，请缩短时间范围后重试') from None
        raise
    finally:
        conn.set_progress_handler(None, 0)
    if len(rows) > 10000:
        raise HTTPException(422, '统计分组超过10000条，请缩小筛选范围')
    users = {}
    fx = Decimal(catalog['usd_to_cny'])
    price_map = {(p['model'], p['host']): p for p in catalog['models']}
    for row in rows:
        record = dict(row)
        user = users.setdefault(record['user_id'], {
            'user_id': record['user_id'], 'username': record['username'], 'name': record['name'],
            'requests': 0, 'input_tokens': None, 'output_tokens': None, 'total_tokens': None,
            'unknown_requests': 0, 'local_requests': 0, 'priced_requests': 0, 'cost': None, 'last_used': '', 'models': []})
        try:
            address = urlsplit(record['base_url'] or '')
            price_host = address.hostname
            # 仅北京业务空间映射到北京报价，国际地域和伪装后缀不能套用中国区价。
            qwen = PRESETS_BY_ID['qwen']
            for region, _label, template in qwen.regions:
                if region == 'cn-beijing':
                    host_pattern = re.escape(urlsplit(template).hostname).replace(r'\{workspace\}', r'[a-z0-9][a-z0-9-]{0,62}')
                    if re.fullmatch(host_pattern, price_host or ''):
                        price_host = urlsplit(qwen.base_url).hostname
            price = price_map.get((record['model'], price_host)) if address.scheme == 'https' and address.port in (None, 443) and record['locality'] not in ('local', 'managed_private') else None
            if address.username or address.password or address.query or address.fragment or address.path.rstrip('/') not in catalog['paths'].get(price_host, []):
                price = None
        except ValueError:
            price = None
        if price and price.get('max_input') is not None and (record['max_input'] or 0) > price['max_input']:
            price = None
        cost = None
        if price and record['complete_requests']:
            cost = (Decimal(record['priced_input']) * Decimal(price['input']) + Decimal(record['priced_output']) * Decimal(price['output'])) / Decimal(1000000)
            if price['currency'] != filters.currency:
                cost = cost * fx if filters.currency == 'CNY' else cost / fx
        last = datetime.fromtimestamp(round((record['last_used_jd'] - 2440587.5) * 86400), start.tzinfo).isoformat(timespec='seconds')
        detail = {key: record[key] for key in ('requests', 'input_tokens', 'output_tokens', 'total_tokens', 'unknown_requests')}
        detail.update(model=record['model'] or '未记录型号', api_format=record['api_format'] or '未记录接口',
            local_requests=record['requests'] if record['locality'] in ('local', 'managed_private') else 0,
            cost=cost, priced_requests=record['complete_requests'] if cost is not None else 0, last_used=last)
        user['models'].append(detail)
        for field in ('requests', 'local_requests', 'unknown_requests', 'priced_requests', 'input_tokens', 'output_tokens', 'total_tokens', 'cost'):
            if detail[field] is not None:
                user[field] = (user[field] or 0) + detail[field]
        user['last_used'] = max(user['last_used'], last)
    for user in users.values():
        grouped = {}
        for detail in user['models']:
            key = (detail['model'], detail['api_format'], bool(detail['local_requests']))
            if key not in grouped:
                grouped[key] = detail.copy()
                continue
            target = grouped[key]
            for field in ('requests', 'local_requests', 'unknown_requests', 'priced_requests', 'input_tokens', 'output_tokens', 'total_tokens', 'cost'):
                if detail[field] is not None:
                    target[field] = (target[field] or 0) + detail[field]
            target['last_used'] = max(target['last_used'], detail['last_used'])
        user['models'] = list(grouped.values())
    items = sorted(users.values(), key=lambda row: row['user_id'])
    known = [row for row in items if row[filters.sort] is not None]
    unknown = [row for row in items if row[filters.sort] is None]
    items = sorted(known, key=lambda row: row[filters.sort], reverse=filters.direction == 'desc') + unknown
    summary = {'users': len(items)}
    for field in ('requests', 'input_tokens', 'output_tokens', 'total_tokens', 'local_requests', 'unknown_requests', 'priced_requests', 'cost'):
        known = [row[field] for row in items if row[field] is not None]
        summary[field] = sum(known) if known else (0 if not items else None)
    for row in [summary, *items, *(detail for item in items for detail in item['models'])]:
        if row['cost'] is not None:
            row['cost'] = format(Decimal(row['cost']), '.6f')
    page = min(filters.page, max(1, (len(items)+filters.page_size-1)//filters.page_size))
    return {'items': items if export else items[(page-1)*filters.page_size:page*filters.page_size], 'total': len(items),
        'page': page, 'page_size': filters.page_size,
        'summary': summary, 'currency': filters.currency, 'pricing': catalog,
        'coverage_note': '仅统计已保存且带时区的调用记录；未采集的历史用量不回填。'}


def export_data(conn, actor, filters, format):
    result = query(conn, actor, filters, export=True)
    table = [['用户ID', '用户名', '昵称', '请求次数', '输入Token', '输出Token', '总Token',
        f'参考成本({filters.currency})', '已估价请求', '本地请求（不计价）', '用量未知请求', '最后使用时间', '价格版本']]
    for row in result['items']:
        cells = [row[key] for key in ('user_id', 'username', 'name', 'requests', 'input_tokens',
            'output_tokens', 'total_tokens', 'cost', 'priced_requests', 'local_requests', 'unknown_requests', 'last_used')]
        if row['requests'] > 0 and row['local_requests'] == row['requests']:
            cells[7] = '无需计价'
        cells.append(result['pricing']['version'])
        # 用户名与昵称可能以公式符号开头，两种格式均按纯文本导出。
        table.append(['未知' if value is None else "'" + str(value) if str(value).lstrip().startswith(('=', '+', '-', '@')) else str(value) for value in cells])
    table.append([result['pricing']['notice']])
    table.append([result['coverage_note']])
    table.append([f"1 USD = {result['pricing']['usd_to_cny']} CNY；{result['pricing']['exchange_note']}"])
    if format == 'csv':
        stream = io.StringIO(newline='')
        csv.writer(stream).writerows(table)
        return stream.getvalue().encode('utf-8-sig'), 'text/csv; charset=utf-8', result['total']
    from openpyxl import Workbook
    book = Workbook(write_only=True)
    sheet = book.create_sheet('Token用量')
    for row in table:
        sheet.append(row)
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', result['total']
