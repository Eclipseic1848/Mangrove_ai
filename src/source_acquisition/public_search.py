"""固定公开搜索端点；只发现链接，不执行网页指令或抓取正文。"""
import asyncio
from urllib.parse import parse_qs, urlsplit
import httpx
from bs4 import BeautifulSoup
from src.connectors.http_security import HttpSecurityGuard, SsrfError
from src.model_connections.pinned_transport import PinnedAsyncHTTPTransport

PROVIDER = 'duckduckgo-html-v1'


class PublicSearchError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class PublicSearchClient:
    provider = PROVIDER

    def __init__(self, *, security_guard=None, transport=None):
        self.guard = security_guard or HttpSecurityGuard()
        self.transport = transport

    async def search(self, query, *, time_range='any', domains=(), limit=10):
        from .service import AnonymousWebFetcher, _check_read_authorization
        cleanup = AnonymousWebFetcher(security_guard=self.guard)
        try:
            async with asyncio.timeout(20):
                target = await asyncio.to_thread(self.guard.validate, 'https://html.duckduckgo.com/html/')
                transport = PinnedAsyncHTTPTransport(target=target, transport=self.transport)
                async with cleanup._client(transport, dict(trust_env=False, follow_redirects=False, timeout=10)) as client:
                    terms = query + (' (' + ' OR '.join('site:' + domain for domain in domains) + ')' if domains else '')
                    data = {'q': terms, 'kl': 'wt-wt'}
                    if time_range != 'any':
                        data['df'] = {'day':'d','week':'w','month':'m','year':'y'}[time_range]
                    # DNS 等待期间可能撤销授权，发送前重新核对持久门。
                    _check_read_authorization()
                    response = await client.send(client.build_request('POST', 'https://html.duckduckgo.com/html/', data=data), stream=True)
                    try:
                        _check_read_authorization()
                        if response.status_code != 200:
                            raise PublicSearchError('search_blocked', '搜索服务拒绝或跳转，未扩大访问范围')
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            _check_read_authorization()
                            content.extend(chunk)
                            if len(content) > 1024 * 1024:
                                raise PublicSearchError('search_response_limit', '搜索响应超过读取上限')
                    finally:
                        await cleanup._close_safely(response.stream.aclose)
                soup = BeautifulSoup(bytes(content), 'lxml')
                if soup.select('form[action*="anomaly"], .anomaly-modal, #challenge-form'):
                    raise PublicSearchError('search_blocked', '搜索服务要求验证，未绕过')
                candidates, seen = [], set()
                for anchor in soup.select('a.result__a'):
                    url = anchor.get('href', '')
                    parts = urlsplit(url)
                    if parts.hostname in {'duckduckgo.com','html.duckduckgo.com'} or url.startswith('/l/?'):
                        url = parse_qs(parts.query).get('uddg', [''])[0]
                    from .service import normalize_public_url
                    try:
                        url = normalize_public_url(url)
                    except ValueError:
                        continue
                    if url in seen:
                        continue
                    seen.add(url)
                    candidates.append({'url':url,'title':anchor.get_text(' ',strip=True)[:300],'status':'discovered'})
                    if len(candidates) >= limit:
                        break
                if not candidates and not soup.select('.no-results, .no-results__message, .result--no-result'):
                    raise PublicSearchError('search_unrecognized', '搜索页未包含可核结果或明确零结果标记')
                return candidates
        except SsrfError as exc:
            raise PublicSearchError('search_dns_error' if 'DNS' in str(exc) else 'search_network_denied', '搜索端点网络解析未通过安全检查，已拒绝发送请求') from exc
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise PublicSearchError('search_timeout','搜索请求超时，无法确定完整结果') from exc
        except httpx.HTTPError as exc:
            raise PublicSearchError('search_failed','搜索服务连接失败') from exc


def normalize_domains(values):
    import re
    if len(values) > 10:
        raise ValueError('查询域名最多10个')
    result = []
    for value in values:
        if not isinstance(value, str) or not value or any(char in value for char in '/:@?#\\'):
            raise ValueError('查询范围须为无路径或凭证的精确域名')
        try:
            domain = value.encode('idna').decode('ascii').lower()
        except UnicodeError as exc:
            raise ValueError('查询域名无效') from exc
        if len(domain) > 253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in domain.split('.')):
            raise ValueError('查询域名无效')
        if domain not in result:
            result.append(domain)
    return tuple(result)
