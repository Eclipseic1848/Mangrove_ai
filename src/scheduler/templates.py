"""自动化任务模板：任务中心「自动化任务模板」区展示的场景化预设（任务中心重构）。

纯静态配置，不涉及用户数据。prompt 里用「」标出用户创建时需要替换的占位内容
（如「品牌名」「平台」），前端预填到提示词输入框后由用户自行修改。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

TASK_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "daily_voc_report",
        "name": "每日竞品口碑日报",
        "description": "每天固定时间采集指定平台上某品牌/车型的最新评论并输出口碑分析",
        "prompt": "采集「平台」上「品牌/车型」的最新评论并输出口碑（VOC）分析报告",
        "trigger_type": "cron",
        "cron_expr": "0 9 * * *",
    },
    {
        "id": "weekly_sentiment_report",
        "name": "每周舆情周报",
        "description": "每周固定时间汇总关键词相关的新闻与社媒讨论，输出舆情周报",
        "prompt": "汇总上周「关键词」相关的新闻与社媒讨论，输出舆情周报",
        "trigger_type": "cron",
        "cron_expr": "0 9 * * 1",
    },
    {
        "id": "bidding_monitor",
        "name": "招投标信息监控",
        "description": "每天固定时间采集指定关键词相关的最新招投标公告并汇总",
        "prompt": "采集「关键词」相关的最新招投标公告并汇总要点",
        "trigger_type": "cron",
        "cron_expr": "0 8 * * *",
    },
    {
        "id": "industry_news_digest",
        "name": "行业新闻早报",
        "description": "每天固定时间采集某行业当日重点新闻并生成摘要早报",
        "prompt": "采集「行业」当日重点新闻并生成摘要早报",
        "trigger_type": "cron",
        "cron_expr": "30 8 * * *",
    },
    {
        "id": "new_product_buzz",
        "name": "新品声量追踪",
        "description": "每天固定时间追踪某新品在新闻与社媒上的最新讨论与评价",
        "prompt": "追踪「新品名」在新闻与社媒上的最新讨论与评价",
        "trigger_type": "cron",
        "cron_expr": "0 10 * * *",
    },
    {
        "id": "ecommerce_review_monitor",
        "name": "电商评价监控",
        "description": "每天固定时间采集京东某商品的最新评价并分析变化趋势",
        "prompt": "采集京东「商品」的最新评价并分析变化趋势",
        "trigger_type": "cron",
        "cron_expr": "0 9 * * *",
    },
    {
        "id": "xiaohongshu_weekly",
        "name": "小红书种草周报",
        "description": "每周固定时间采集小红书某品牌/品类种草笔记并总结内容风向",
        "prompt": "采集小红书「品牌/品类」种草笔记并总结内容风向",
        "trigger_type": "cron",
        "cron_expr": "0 17 * * 5",
    },
    {
        "id": "one_time_collection",
        "name": "单次定时采集",
        "description": "在你指定的时间采集一次某主题相关内容并出报告，不重复执行",
        "prompt": "采集「主题」相关内容并生成报告",
        "trigger_type": "once",
    },
]


def get_template(template_id: str) -> Optional[Dict[str, Any]]:
    """按 id 取模板，找不到返回 None。"""
    for t in TASK_TEMPLATES:
        if t["id"] == template_id:
            return t
    return None
