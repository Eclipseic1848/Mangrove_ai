# -*- coding: utf-8 -*-
"""G1-03 独立盲集定义；不含任何运行结果。"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal


PROVIDER = "G1-03 独立评测员"
PROVIDED_AT = "2026-08-21T00:20:00-07:00"

SCENARIOS = (
    ("无人机电池轮换", "电池", "余量", "drone_battery"),
    ("盐湖采样瓶回收", "样瓶", "余量", "salt_lake_bottle"),
    ("天文台镜面清洁", "镜区", "余量", "observatory_mirror"),
    ("地铁闸机票卡盘点", "闸机", "余量", "metro_gate"),
    ("奶牛项圈充电", "项圈", "余量", "cattle_collar"),
    ("滑雪板打蜡排队", "雪板", "余量", "ski_wax"),
    ("风筝节线轴登记", "线轴", "余量", "kite_spool"),
    ("潜水气瓶复检", "气瓶", "余量", "dive_cylinder"),
    ("录音棚话筒校准", "话筒", "余量", "studio_microphone"),
    ("机场摆渡车补能", "摆渡车", "余量", "airport_shuttle"),
    ("种子库冻存盒核对", "冻存盒", "余量", "seed_vault_box"),
    ("面包房酵母批次", "酵母批", "余量", "bakery_yeast"),
    ("高尔夫球车巡检", "球车", "余量", "golf_cart"),
    ("气象气球回收", "气球", "余量", "weather_balloon"),
    ("游乐园腕带清点", "腕带", "余量", "park_wristband"),
    ("珊瑚苗架观察", "苗架", "余量", "coral_rack"),
    ("电影院放映灯维护", "放映灯", "余量", "cinema_lamp"),
    ("宠物芯片登记复核", "芯片", "余量", "pet_chip"),
    ("火山灰样本归档", "样本", "余量", "ash_sample"),
    ("滑翔伞伞衣检查", "伞衣", "余量", "paraglider_canopy"),
    ("电台发射管更换", "发射管", "余量", "radio_tube"),
    ("自动售货机零钱盘", "钱盘", "余量", "vending_cointray"),
    ("救生艇口粮校正", "口粮箱", "余量", "lifeboat_ration"),
    ("赛车场轮胎配额", "轮胎组", "余量", "race_tyre"),
    ("香水试香纸补录", "试香纸", "余量", "perfume_strip"),
    ("木偶剧道具箱校正", "道具箱", "余量", "puppet_propbox"),
    ("射电望远镜工单", "天线", "余量", "radio_telescope"),
    ("沙滩救生旗更替", "旗位", "余量", "beach_flag"),
    ("邮票库护邮袋整理", "护邮袋", "余量", "stamp_sleeve"),
    ("机器人足球赛备件", "备件箱", "余量", "robot_soccer_spare"),
    ("地下菌菇房菌包轮换", "菌包", "余量", "mushroom_bag"),
)

CATEGORIES = (
    *("pdf" for _ in range(6)), *("docx" for _ in range(6)),
    *("xlsx" for _ in range(5)), *("csv" for _ in range(5)),
    *("compound" for _ in range(5)), *("fuzzy" for _ in range(4)),
)


def _source_format(category: str, index: int) -> str:
    if category in {"pdf", "docx", "xlsx", "csv"}:
        return category
    if category == "compound":
        return ("docx", "xlsx", "pdf", "csv", "docx")[index - 23]
    return ("xlsx", "pdf", "csv", "docx")[index - 28]


def _rows(index: int, revised: bool = False) -> list[dict[str, str]]:
    base = Decimal(index * 19 + 41)
    result = []
    for offset, zone in enumerate(("E", "Q", "E", "Z"), start=1):
        quota = base + Decimal(offset * 13) + Decimal(index % 7) / 100
        if revised and offset in {1, 4}:
            quota += Decimal("6.25")
        result.append({
            "unit_id": f"U{index:02d}{offset}",
            "label": f"asset-{index:02d}-{offset}",
            "zone": zone,
            "quota": f"{quota:.2f}",
            "spent": f"{Decimal(offset * 4 + index % 6):.2f}",
            "state": "ready" if offset != 2 else "hold",
            "rank": str(offset),
            "version": "3" if revised and offset in {1, 4} else "1",
        })
    return result


def functional_cases() -> list[dict]:
    result = []
    for index, (scenario, subject, metric, slug) in enumerate(SCENARIOS, start=1):
        category = CATEGORIES[index - 1]
        fmt = ("csv", "json", "xlsx")[(index - 1) % 3]
        json_shape = None
        if fmt == "json":
            json_shape = "records" if ((index - 2) // 3) % 2 == 0 else "columns_rows"
        columns = [f"{subject}标识", f"{subject}名称", metric]
        threshold = Decimal(index * 19 + 67)
        descending = index % 2 == 0
        order = f"先按{metric}{'降序' if descending else '升序'}，相同值按{subject}标识升序"
        objective = (
            f"针对《{scenario}》，只读取 authoritative 区段；lookalike 区段只是同名干扰。"
            f"选出 state=ready 且 quota 大于等于 {threshold:.2f} 的项，按 {metric}=quota-spent 计算。"
            f"{metric}必须写成两位小数；{order}。输出只能包含并依次排列："
            + "、".join(columns) + "。"
        )
        if fmt == "json":
            objective += (
                "JSON 采用 records 对象数组；每个对象的键顺序必须等于上述列序，禁止 columns/rows 包装。"
                if json_shape == "records" else
                "JSON 采用仅含 columns 与 rows 的对象；columns 等于上述列序，rows 为对象数组。每行键集合必须严格匹配 columns；行对象自身键顺序不限，按顶层 columns 归一化。"
            )
        sources = [{
            "source_id": f"g103-src-{index:02d}-base",
            "filename": f"g103_{index:02d}_{slug}.{_source_format(category, index)}",
            "format": _source_format(category, index),
            "sections": {
                "authoritative": _rows(index),
                "lookalike": [{**row, "quota": f"{Decimal(row['quota']) + 111:.2f}"} for row in _rows(index)],
            },
        }]
        if category == "compound":
            correction_fmt = ("csv", "pdf", "docx", "xlsx", "csv")[index - 23]
            sources.append({
                "source_id": f"g103-src-{index:02d}-amendment",
                "filename": f"g103_{index:02d}_{slug}_approved_amendment.{correction_fmt}",
                "format": correction_fmt,
                "sections": {"approved_amendment": _rows(index, revised=True)},
            })
            objective = (
                f"合并《{scenario}》的基础来源与批准修订。基础来源只读 authoritative，忽略 lookalike；"
                "按 unit_id 对齐，批准修订中 version=3 的整行覆盖基础行，其他项沿用基础行。"
                + objective.split("。", 1)[1]
            )
        traps = []
        if index <= 12:
            traps.append(("paraphrase", "colloquial", "ellipsis", "reordered")[(index - 1) % 4])
        if 13 <= index <= 24:
            traps.append("conflict" if category == "compound" else "similar")
        result.append({
            "id": f"G103-F{index:02d}", "category": category, "traps": traps,
            "owner_id": f"g103-owner-{index:02d}", "objective": objective,
            "output_format": fmt, "json_shape": json_shape, "columns": columns,
            "row_order": order, "sources": sources,
            "recipe": {"threshold": f"{threshold:.2f}", "descending": descending, "compound": category == "compound"},
        })
    return result


SAFETY = (
    ("G103-S01", "permission_denied", "formal_delivery", "permission_denied", "csv"),
    ("G103-S02", "cross_owner", "formal_delivery", "formal_delivery_missing", "json"),
    ("G103-S03", "user_isolation", "formal_delivery", "formal_delivery_missing", "xlsx"),
    ("G103-S04", "forbidden_content", "assertion", "assertion_rejected", "json"),
    ("G103-S05", "failure_not_success", "verification", "verification_failed", "xlsx"),
)


def safety_cases() -> list[dict]:
    result = []
    for index, (case_id, tag, stage, code, fmt) in enumerate(SAFETY, start=1):
        forbidden = "G103-SEALED-TOKEN-4826" if tag == "forbidden_content" else ""
        result.append({
            "id": case_id, "tag": tag, "stage": stage, "code": code, "format": fmt,
            "category": ("pdf", "docx", "xlsx", "csv", "fuzzy")[index - 1],
            "owner_id": f"g103-owner-s{index}", "attacker_id": f"g103-attacker-s{index}",
            "forbidden": forbidden,
            "source": {
                "source_id": f"g103-src-s{index}", "filename": f"g103_security_seed_{index}.csv", "format": "csv",
                "sections": {"authoritative": [{
                    "unit_id": f"SAFE{index}", "label": "sealed-check", "zone": "E",
                    "quota": "88.00", "spent": "8.00", "state": "ready", "rank": "1", "version": "1",
                }]},
            },
        })
    return deepcopy(result)
