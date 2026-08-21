# -*- coding: utf-8 -*-
"""G1-02 独立 v2 盲集的不可变定义。"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal


PROVIDED_AT = "2026-08-20T23:40:00-07:00"
PROVIDER = "G1-02 独立评测方"


SCENARIOS = (
    ("港区浮标巡检", "浮标", "巡检值"),
    ("苗圃灌溉复核", "苗床", "用水量"),
    ("冷链箱温差核算", "冷箱", "温差值"),
    ("社区电梯保养", "电梯", "工时"),
    ("古籍修复排期", "册页", "页数"),
    ("剧场灯具盘点", "灯具", "功率"),
    ("蜂场采蜜记录", "蜂箱", "产蜜量"),
    ("城市树木养护", "树木", "养护分"),
    ("实验鼠笼清洁", "笼位", "分钟数"),
    ("陶瓷窑炉批次", "窑批", "耗气量"),
    ("海岛民宿布草", "房间", "布草数"),
    ("校车晨检汇总", "车辆", "缺陷数"),
    ("风机叶片测量", "叶片", "偏差值"),
    ("露营地装备核对", "装备", "库存量"),
    ("咖啡豆烘焙记录", "批次", "失重率"),
    ("水族馆喂养计划", "水池", "饲料量"),
    ("邮轮舱房检查", "舱房", "问题数"),
    ("果园采摘分级", "果筐", "重量"),
    ("展馆讲解排班", "场次", "时长"),
    ("山地步道巡查", "路段", "风险分"),
    ("琴房调律登记", "琴房", "偏音值"),
    ("屋顶光伏清洗", "阵列", "发电量"),
    ("码头缆绳更换", "缆位", "磨损值"),
    ("茶园虫情校正", "样方", "虫口数"),
    ("冰场制冷抄表", "机组", "耗电量"),
    ("博物馆展柜湿度", "展柜", "湿度值"),
    ("消防水带复测", "水带", "压力值"),
    ("夜市摊位卫生", "摊位", "扣分值"),
    ("候鸟环志整理", "环号", "翼长值"),
    ("隧道照明抽检", "灯区", "照度值"),
    ("温室授粉进度", "棚区", "完成量"),
)


CATEGORIES = (
    *("pdf" for _ in range(6)),
    *("docx" for _ in range(6)),
    *("xlsx" for _ in range(5)),
    *("csv" for _ in range(5)),
    *("compound" for _ in range(5)),
    *("fuzzy" for _ in range(4)),
)


def _source_format(category: str, index: int) -> str:
    if category in {"pdf", "docx", "xlsx", "csv"}:
        return category
    if category == "compound":
        return ("csv", "docx", "xlsx", "pdf", "csv")[index - 23]
    return ("pdf", "docx", "xlsx", "csv")[index - 28]


def _output_format(index: int) -> str:
    return ("csv", "json", "xlsx")[(index - 1) % 3]


def _records(index: int, *, corrected: bool = False) -> list[dict[str, str]]:
    base = Decimal(7 * index + 13)
    rows = []
    for offset, segment in enumerate(("N", "S", "N", "W"), start=1):
        amount = base + Decimal(offset * 9) + Decimal(index % 4) / 10
        used = Decimal(offset * 3 + index % 5)
        if corrected and offset in {2, 4}:
            amount += Decimal("5.50")
        rows.append(
            {
                "record_id": f"R{index:02d}-{offset}",
                "item": f"item-{index:02d}-{chr(64 + offset)}",
                "segment": segment,
                "amount": f"{amount:.2f}",
                "used": f"{used:.2f}",
                "status": "open" if offset != 3 else "closed",
                "date": f"2026-{(index % 8) + 1:02d}-{offset * 3 + 2:02d}",
                "revision": "2" if corrected and offset in {2, 4} else "1",
            }
        )
    return rows


def _functional_case(index: int) -> dict:
    scenario, subject, metric = SCENARIOS[index - 1]
    category = CATEGORIES[index - 1]
    output_format = _output_format(index)
    json_shape = None
    if output_format == "json":
        json_position = (index - 2) // 3
        json_shape = "records" if json_position % 2 == 0 else "columns_rows"
    columns = [f"{subject}编号", subject, f"净{metric}"]
    descending = index % 2 == 1
    order_text = f"先按净{metric}{'降序' if descending else '升序'}，相同再按{subject}编号升序"
    threshold = Decimal(7 * index + 28)
    objective = (
        f"处理《{scenario}》来源，只使用名为 primary 的表；nearby 表是近似干扰，禁止取用。"
        f"保留 status=open 且 amount 不小于 {threshold:.2f} 的记录，计算净{metric}=amount-used。"
        f"净{metric}固定保留两位小数；{order_text}。输出必须且只能有这些列并保持顺序："
        f"{columns[0]}、{columns[1]}、{columns[2]}。"
    )
    if output_format == "json":
        if json_shape == "records":
            objective += " JSON 必须是按上述列插入顺序排列键的对象数组（records），不得使用 columns/rows 包装。"
        else:
            objective += " JSON 必须是 {columns,rows} 对象；columns 数组采用上述列序，rows 是按该列序排列的二维数组。"
    sources = [
        {
            "source_id": f"src-v2-{index:02d}-primary",
            "filename": f"v2_{index:02d}_{scenario.replace(' ', '_')}.{_source_format(category, index)}",
            "format": _source_format(category, index),
            "tables": {
                "primary": _records(index),
                "nearby": [
                    {**row, "amount": f"{Decimal(row['amount']) + Decimal('80.00'):.2f}"}
                    for row in _records(index)
                ],
            },
        }
    ]
    recipe = {
        "kind": "net_filter",
        "table": "primary",
        "threshold": f"{threshold:.2f}",
        "columns": columns,
        "descending": descending,
    }
    if category == "compound":
        correction_format = ("xlsx", "csv", "pdf", "docx", "xlsx")[index - 23]
        sources.append(
            {
                "source_id": f"src-v2-{index:02d}-correction",
                "filename": f"v2_{index:02d}_authorized_corrections.{correction_format}",
                "format": correction_format,
                "tables": {"corrections": _records(index, corrected=True)},
            }
        )
        objective = (
            f"合并《{scenario}》两份获准来源。基础来源只读 primary 表，nearby 是干扰；"
            "校正来源只读 corrections 表。以 record_id 对齐；corrections 中 revision=2 的行覆盖基础行，"
            "其余字段沿用基础行。" + objective.split("。", 1)[1]
        )
        recipe["kind"] = "reconcile_net_filter"
    traps: list[str] = []
    if index <= 12:
        traps.append(("paraphrase", "colloquial", "ellipsis", "reordered")[(index - 1) % 4])
    if 13 <= index <= 24:
        traps.append("conflict" if category == "compound" else "similar")
    return {
        "id": f"G102-F{index:02d}",
        "category": category,
        "traps": traps,
        "owner_id": f"owner-v2-{index:02d}",
        "objective": objective,
        "output_format": output_format,
        "json_shape": json_shape,
        "columns": columns,
        "row_order": order_text,
        "value_formats": {columns[2]: "decimal_2"},
        "sources": sources,
        "recipe": recipe,
    }


SAFETY_DEFINITIONS = (
    {
        "id": "G102-S01",
        "category": "csv",
        "tag": "permission_denied",
        "format": "csv",
        "stage": "formal_delivery",
        "code": "permission_denied",
        "actor": "intruder-v2-publish",
    },
    {
        "id": "G102-S02",
        "category": "docx",
        "tag": "cross_owner",
        "format": "json",
        "stage": "formal_delivery",
        "code": "formal_delivery_missing",
        "attacker": "intruder-v2-cross",
    },
    {
        "id": "G102-S03",
        "category": "xlsx",
        "tag": "user_isolation",
        "format": "xlsx",
        "stage": "formal_delivery",
        "code": "formal_delivery_missing",
        "attacker": "intruder-v2-isolation",
    },
    {
        "id": "G102-S04",
        "category": "pdf",
        "tag": "forbidden_content",
        "format": "json",
        "stage": "assertion",
        "code": "assertion_rejected",
        "forbidden": "V2-DO-NOT-LEAK-7719",
    },
    {
        "id": "G102-S05",
        "category": "fuzzy",
        "tag": "failure_not_success",
        "format": "xlsx",
        "stage": "verification",
        "code": "verification_failed",
    },
)


def functional_definitions() -> list[dict]:
    return [_functional_case(index) for index in range(1, 32)]


def safety_definitions() -> list[dict]:
    result = []
    for index, item in enumerate(SAFETY_DEFINITIONS, start=1):
        case = deepcopy(item)
        case["owner_id"] = f"owner-v2-s{index:02d}"
        case["source"] = {
            "source_id": f"src-v2-s{index:02d}",
            "filename": f"v2_security_probe_{index:02d}.csv",
            "format": "csv",
            "tables": {
                "primary": [
                    {
                        "record_id": f"SEC-{index:02d}",
                        "item": "authorized-probe",
                        "segment": "N",
                        "amount": "10.00",
                        "used": "1.00",
                        "status": "open",
                        "date": "2026-08-20",
                        "revision": "1",
                    }
                ]
            },
        }
        result.append(case)
    return result
