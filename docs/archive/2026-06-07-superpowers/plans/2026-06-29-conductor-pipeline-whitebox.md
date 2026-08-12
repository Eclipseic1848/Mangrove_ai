# Conductor 主链路准确性重构 + 节点白盒流式 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现。步骤用 `- [ ]` 复选框跟踪。

**Goal:** 让自然语言→理解→规划→路由→清洗这条主链路更准确（planner 升级为真 LLM 规划器、intent 瘦身管理解、router 容错归一、clean 噪声剥离），并把每个流水线节点的产出以节点级流式白盒化呈现到聊天界面（完成自动折叠、可点击展开）。

**Architecture:** intent(LLM·理解+追问) 产出松散 `understanding` → planner(LLM·真规划) 拿 `understanding`+已知平台词表产出完整策略，再走 `TaskSpec.from_draft` 校验，失败降级不阻断 → router 用统一归一容错匹配专用采集器 → clean 增加噪声剥离。每节点完成时 `build_node_view` 生成结构化摘要，经 SSE `node` 事件带 `view` 推前端，前端渲染可折叠节点卡片。

**Tech Stack:** Python 3.13 / LangGraph / LangChain LLM 层 / FastAPI + sse_starlette / React 18 + Vite + TS + Tailwind。测试为脚本式（`scripts/test_*.py`，含 `main()` 跑批 + `sys.exit`，`python scripts/test_X.py` 运行）。

> **重要约定（本仓库特性）**
> - **本仓库非 git**：无 `commit` 步骤；每个任务以「运行该任务测试并确认 PASS」作为 checkpoint。
> - **编码**：所有新建/修改的 `.py`、`.ts/.tsx` 用 UTF-8；注释与中文字符串确保无乱码。
> - **Python 解释器**：本地为 `E:/python3.13`，命令里写 `python` 即可（已在 PATH）。运行测试在项目根目录。
> - **LLM mock**：节点用 `from src.llm import achat` 导入，测试中 monkeypatch 节点模块的 `achat`（如 `src.conductor.nodes.intent.achat`）为 async 桩。

---

## 文件结构（先锁定边界）

**后端新增**
- `src/collectors/platforms.py` —— 平台词表 `known_platforms()` + 归一 `normalize_platform()`。
- `src/conductor/node_views.py` —— `build_node_view(node, delta, values)`：把节点产出转成前端展示摘要。

**后端修改**
- `src/collectors/__init__.py` —— 导出 `known_platforms`, `normalize_platform`。
- `src/collectors/social_media_collector.py` —— `_resolve_platform` 复用归一 + 包含匹配。
- `src/conductor/nodes/clean.py` —— 增加噪声剥离。
- `src/conductor/state.py` —— 新增 `understanding`、`plan_reasoning`。
- `src/conductor/prompts.py` —— 重写 `INTENT_SYSTEM`（理解+few-shot），新增 `PLANNER_SYSTEM`。
- `src/conductor/nodes/intent.py` —— 产出 `understanding`、解析失败重试 1 次。
- `src/conductor/nodes/planner.py` —— LLM 规划 + 降级兜底 + 告警。
- `src/conductor/graph.py` —— `astream_conductor` 的 node 事件携带 `view`。
- `src/api/routes/chat.py` —— SSE `node` 事件数据加 `view`。

**前端修改**
- `frontend/src/lib/api.ts` —— `onNode` 回调类型加 `view`。
- `frontend/src/components/NodeStream.tsx`（新增） —— 节点白盒卡片序列。
- `frontend/src/pages/Chat.tsx` —— 收集 node view 并渲染 `NodeStream`。

**测试新增**
- `scripts/test_platforms.py`、`scripts/test_clean.py`、`scripts/test_intent.py`、`scripts/test_planner.py`、`scripts/test_node_views.py`。

---

## Task 1: 平台词表与归一

**Files:**
- Create: `src/collectors/platforms.py`
- Modify: `src/collectors/__init__.py`
- Test: `scripts/test_platforms.py`

- [ ] **Step 1: 写失败测试**

`scripts/test_platforms.py`：
```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""平台词表与归一单测。运行：python scripts/test_platforms.py"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors.platforms import known_platforms, normalize_platform


def test_known_platforms_nonempty():
    ps = known_platforms()
    assert isinstance(ps, list) and "抖音" in ps and "小红书" in ps


def test_normalize_alias():
    assert normalize_platform("douyin") == "抖音"
    assert normalize_platform("XHS") == "小红书"      # 大小写
    assert normalize_platform(" 哔哩哔哩 ") == "B站"   # 空格 + 中文别名


def test_normalize_contains():
    # 包含规范名子串也能命中
    assert normalize_platform("小红书App") == "小红书"


def test_normalize_unknown_returns_stripped():
    assert normalize_platform("  汽车之家 ") == "汽车之家"
    assert normalize_platform("") == ""


def main():
    tests = [test_known_platforms_nonempty, test_normalize_alias,
             test_normalize_contains, test_normalize_unknown_returns_stripped]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1; print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50); print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python scripts/test_platforms.py`
Expected: FAIL（`ModuleNotFoundError: src.collectors.platforms`）。

- [ ] **Step 3: 实现 platforms.py**

`src/collectors/platforms.py`：
```python
# -*- coding: utf-8 -*-
"""平台名词表与归一。

known_platforms()：展示给 planner 的「规范平台名」清单，让规划输出的平台名
从源头可被路由命中。
normalize_platform()：把任意别名/大小写/含噪声的平台名归一为规范名（router 容错）。
"""
from __future__ import annotations

from typing import List

# 专用采集器支持的「规范名」（社媒由 MediaCrawler 承接，京东为电商专用）
KNOWN_PLATFORMS: List[str] = [
    "抖音", "小红书", "微博", "B站", "快手", "知乎", "贴吧", "京东",
]

# 别名（小写）-> 规范名
_ALIASES = {
    "douyin": "抖音", "dy": "抖音",
    "xiaohongshu": "小红书", "红书": "小红书", "xhs": "小红书",
    "weibo": "微博", "wb": "微博",
    "哔哩哔哩": "B站", "bilibili": "B站", "bili": "B站", "b站": "B站",
    "kuaishou": "快手", "ks": "快手",
    "zhihu": "知乎",
    "tieba": "贴吧",
    "jd": "京东", "jingdong": "京东", "京东商城": "京东",
}


def known_platforms() -> List[str]:
    """规范平台名清单（副本）。"""
    return list(KNOWN_PLATFORMS)


def normalize_platform(name: str) -> str:
    """归一平台名为规范名；无法识别时返回去空格后的原值。"""
    if not name:
        return ""
    key = name.strip()
    if key in KNOWN_PLATFORMS:
        return key
    low = key.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    if key in _ALIASES:
        return _ALIASES[key]
    # 包含匹配：规范名作为子串出现（如「小红书App」）
    for canon in KNOWN_PLATFORMS:
        if canon in key:
            return canon
    return key
```

- [ ] **Step 4: 导出**

`src/collectors/__init__.py` 在导入采集器后追加（与现有 `select_collectors` 导出风格一致）：
```python
from .platforms import known_platforms, normalize_platform  # noqa: F401  平台词表/归一
```

- [ ] **Step 5: 运行确认通过**

Run: `python scripts/test_platforms.py`
Expected: PASS（4/4 通过）。

- [ ] **Step 6: Checkpoint** —— 上面 PASS 即完成。

---

## Task 2: router 容错归一（社媒平台解析）

**Files:**
- Modify: `src/collectors/social_media_collector.py:44-50`
- Test: `scripts/test_platforms.py`（追加用例）

- [ ] **Step 1: 追加失败测试**

在 `scripts/test_platforms.py` 顶部 import 追加：
```python
from src.collectors.social_media_collector import _resolve_platform
from src.conductor.task_spec import TaskSpec
```

在 `main()` 的 `tests` 列表前新增函数：
```python
def test_resolve_platform_variants():
    # 别名 / 大小写 / 含噪声都应解析到 MediaCrawler 代码
    assert _resolve_platform(TaskSpec(intent="x", platforms=["douyin"])) == "dy"
    assert _resolve_platform(TaskSpec(intent="x", platforms=["小红书App"])) == "xhs"
    assert _resolve_platform(TaskSpec(intent="x", platforms=["B站"])) == "bili"
    # 未支持平台返回空串（交给通用引擎）
    assert _resolve_platform(TaskSpec(intent="x", platforms=["汽车之家"])) == ""
```
并把 `test_resolve_platform_variants` 加进 `tests` 列表。

- [ ] **Step 2: 运行确认失败**

Run: `python scripts/test_platforms.py`
Expected: FAIL（`小红书App` 现走不到 `_PLATFORM_MAP` 精确键，返回空串）。

- [ ] **Step 3: 实现归一匹配**

`src/collectors/social_media_collector.py` 修改 `_resolve_platform`（保留 `_PLATFORM_MAP` 不动）：
```python
def _resolve_platform(spec: TaskSpec) -> str:
    """从 TaskSpec 的平台名解析出 MediaCrawler 平台代码，无法识别返回空串。

    先用归一（别名/大小写/包含匹配）得到规范名，再映射到 MediaCrawler 代码，
    避免「小红书App」「Douyin」等变体漏匹配专用采集器。
    """
    from .platforms import normalize_platform
    for p in spec.platforms:
        canon = normalize_platform(p)
        code = (
            _PLATFORM_MAP.get(p.strip())
            or _PLATFORM_MAP.get(p.strip().lower())
            or _PLATFORM_MAP.get(canon)
        )
        if code:
            return code
    return ""
```

- [ ] **Step 4: 运行确认通过**

Run: `python scripts/test_platforms.py`
Expected: PASS（5/5 通过）。

- [ ] **Step 5: Checkpoint** —— PASS 即完成。

---

## Task 3: clean 噪声剥离

**Files:**
- Modify: `src/conductor/nodes/clean.py`
- Test: `scripts/test_clean.py`

- [ ] **Step 1: 写失败测试**

`scripts/test_clean.py`：
```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清洗节点单测。运行：python scripts/test_clean.py"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.conductor.nodes.clean import clean_node, _denoise
from src.conductor.task_spec import TaskSpec


def test_denoise_strips_html_and_whitespace():
    raw = "<p>你好<br/>  世界</p>\n\n\n正文"
    out = _denoise(raw)
    assert "<" not in out and ">" not in out
    assert "你好" in out and "世界" in out and "正文" in out
    assert "\n\n\n" not in out  # 连续空白被折叠


def test_clean_drops_short_boilerplate():
    spec = TaskSpec(intent="x", max_items=10)
    raw = [
        {"url": "u1", "content": "首页 登录 注册"},          # 超短导航样板 → 丢弃
        {"url": "u2", "content": "这是一段足够长的真实正文内容，应当保留下来。"},
    ]
    out = asyncio.run(clean_node({"raw_dataset": raw, "task_spec": spec}))
    contents = [i["content"] for i in out["cleaned_dataset"]]
    assert any("真实正文" in c for c in contents)
    assert not any("首页 登录 注册" in c for c in contents)


def test_clean_dedup_and_cap():
    spec = TaskSpec(intent="x", max_items=1)
    raw = [
        {"url": "u", "content": "重复内容重复内容重复内容重复内容"},
        {"url": "u", "content": "重复内容重复内容重复内容重复内容"},  # 同 url+前缀 → 去重
        {"url": "v", "content": "另一条足够长的正文另一条足够长的正文"},
    ]
    out = asyncio.run(clean_node({"raw_dataset": raw, "task_spec": spec}))
    assert len(out["cleaned_dataset"]) == 1  # max_items 封顶


def main():
    tests = [test_denoise_strips_html_and_whitespace,
             test_clean_drops_short_boilerplate, test_clean_dedup_and_cap]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1; print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50); print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python scripts/test_clean.py`
Expected: FAIL（`cannot import name '_denoise'`）。

- [ ] **Step 3: 实现噪声剥离**

`src/conductor/nodes/clean.py` 改为（保留原 4 条规则，截断前先 `_denoise` + 过滤超短样板）：
```python
"""清洗节点：噪声剥离 + 去重 + 丢空 + 单条截断（控 token），保留至多 max_items 条。"""
from __future__ import annotations

import re
from typing import Any, Dict

from src.config.settings import settings

from ..state import ConductorState

# 超短片段（疑似导航/页脚样板）阈值：短于此且不含句读的片段丢弃
_MIN_CONTENT_LEN = 12

_TAG_RE = re.compile(r"<[^>]+>")          # HTML 标签
_WS_RE = re.compile(r"[ \t ]+")      # 行内连续空白
_BLANK_RE = re.compile(r"\n{3,}")         # 3+ 连续空行 → 2


def _denoise(text: str) -> str:
    """去 HTML 标签、折叠空白、清理 markdown 残留。"""
    if not text:
        return ""
    t = _TAG_RE.sub(" ", text)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    t = _WS_RE.sub(" ", t)
    t = _BLANK_RE.sub("\n\n", t)
    # 去掉每行首尾空白后再拼回，丢弃纯空行造成的前后空白
    t = "\n".join(line.strip() for line in t.splitlines())
    return t.strip()


def _is_boilerplate(text: str) -> bool:
    """超短且无句读的片段视为导航/页脚样板。"""
    if len(text) >= _MIN_CONTENT_LEN:
        return False
    return not any(p in text for p in "。！？.!?，,")


async def clean_node(state: ConductorState) -> Dict[str, Any]:
    raw = state.get("raw_dataset", [])
    spec = state["task_spec"]
    max_chars = settings.clean_max_item_chars  # 单条正文上限，防止 token 爆炸

    seen = set()
    cleaned: list[Dict[str, Any]] = []
    for item in raw:
        content = _denoise((item.get("content") or "").strip())
        if not content or _is_boilerplate(content):
            continue
        # 以 url + 正文前缀 去重
        key = (item.get("url", ""), content[:200])
        if key in seen:
            continue
        seen.add(key)
        if len(content) > max_chars:
            content = content[:max_chars] + " …(已截断)"
        cleaned.append({**item, "content": content})
        if len(cleaned) >= spec.max_items:
            break

    return {"cleaned_dataset": cleaned}
```

- [ ] **Step 4: 运行确认通过**

Run: `python scripts/test_clean.py`
Expected: PASS（3/3 通过）。

- [ ] **Step 5: Checkpoint** —— PASS 即完成。

---

## Task 4: state 契约扩展

**Files:**
- Modify: `src/conductor/state.py:24-28`

- [ ] **Step 1: 加字段**

`src/conductor/state.py` 在「意图阶段」段内，把 `spec_draft` 一行替换为（保留 `spec_draft` 以兼容旧引用，但新增两个键）：
```python
    needs_clarification: bool           # 是否需要向用户追问
    clarification_question: Optional[str]
    understanding: Optional[Dict[str, Any]]  # 意图节点产出的松散理解（供 Planner 规划）
    spec_draft: Optional[Dict[str, Any]]     # 兼容旧引用（新链路不再产出）
    plan_reasoning: Optional[str]            # Planner 的规划理由（白盒回显/日志）
    task_spec: Optional[TaskSpec]       # 结构化任务规格
```

- [ ] **Step 2: 验证可导入**

Run: `python -c "from src.conductor.state import ConductorState; print('ok')"`
Expected: 输出 `ok`。

- [ ] **Step 3: Checkpoint** —— 导入成功即完成（无独立测试，后续任务覆盖）。

---

## Task 5: intent 瘦身为理解 + 追问闸门

**Files:**
- Modify: `src/conductor/prompts.py:4-47`（`INTENT_SYSTEM`）
- Modify: `src/conductor/nodes/intent.py`
- Test: `scripts/test_intent.py`

- [ ] **Step 1: 写失败测试**

`scripts/test_intent.py`：
```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""意图节点单测：产 understanding / 追问 / 解析失败重试。运行：python scripts/test_intent.py"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import src.conductor.nodes.intent as intent_mod


def _patch_achat(responses):
    """用预设回复序列替换节点内的 achat（每次调用弹出一个）。"""
    seq = list(responses)

    async def fake(*args, **kwargs):
        return seq.pop(0)

    intent_mod.achat = fake


def test_understanding_ok():
    _patch_achat(['{"need_clarification": false, "understanding": {"intent": "分析SU7口碑", "what": "评论", "where": "汽车之家", "output": "报告"}}'])
    out = asyncio.run(intent_mod.intent_node({"user_input": "分析小米SU7在汽车之家口碑", "messages": []}))
    assert out["needs_clarification"] is False
    assert out["understanding"]["intent"] == "分析SU7口碑"


def test_clarification():
    _patch_achat(['{"need_clarification": true, "question": "你想采哪个平台？"}'])
    out = asyncio.run(intent_mod.intent_node({"user_input": "帮我搞点数据", "messages": []}))
    assert out["needs_clarification"] is True
    assert "平台" in out["clarification_question"]


def test_parse_retry_then_ok():
    # 第一次返回非 JSON（解析失败），重试第二次成功
    _patch_achat(["抱歉我不会输出JSON", '{"need_clarification": false, "understanding": {"intent": "x"}}'])
    out = asyncio.run(intent_mod.intent_node({"user_input": "抓取某网址正文", "messages": []}))
    assert out["needs_clarification"] is False
    assert out["understanding"]["intent"] == "x"


def test_parse_fail_twice_falls_to_clarify():
    _patch_achat(["乱码1", "乱码2"])
    out = asyncio.run(intent_mod.intent_node({"user_input": "??", "messages": []}))
    assert out["needs_clarification"] is True


def main():
    tests = [test_understanding_ok, test_clarification,
             test_parse_retry_then_ok, test_parse_fail_twice_falls_to_clarify]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1; print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50); print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python scripts/test_intent.py`
Expected: FAIL（现有 intent 产 `spec_draft` 不产 `understanding`；且无重试）。

- [ ] **Step 3: 重写 INTENT_SYSTEM**

`src/conductor/prompts.py` 把 `INTENT_SYSTEM` 整段替换为（聚焦理解 + few-shot）：
```python
# 意图理解：只听懂 + 决定要不要追问，产出松散「理解」（不填满任务字段，规划交给 Planner）
INTENT_SYSTEM = """你是一个"全网数据采集分析"智能体的意图理解模块。
用户用自然语言描述想采集/分析的数据，你的职责是**听懂**它，产出一段松散的「理解」；
当关键信息缺失、无法确定要做什么时，主动提出**一个**最关键的澄清问题。

你只需理解三件事，不要规划具体执行参数（平台代码、采集条数、模板等由后续规划模块决定）：
1) what：要采什么数据（评论/帖子/标讯/商品/文章/通用网页，自然语言描述即可）；
2) where：从哪里采（平台名 / 具体网址 / 仅关键词做全网搜索）；
3) output：想要什么产出（分析报告 / JSON 数据 / 入库 / 邮件 / Slack）。

请**只输出一个 JSON 对象**，不要任何额外文字：
{
  "need_clarification": true/false,
  "question": "need_clarification 为 true 时的一个澄清问题；否则空字符串",
  "understanding": {
    "intent": "对用户意图的一句话归纳",
    "what": "要采什么数据（自然语言）",
    "where": "从哪采（平台/网址/关键词）",
    "output": "想要的产出（自然语言）"
  }
}

判断规则：
- 只要「采什么 + 从哪采」能确定，即可 need_clarification=false（产出形式没说就先不追问，后续默认报告）。
- 信息严重不足（连采什么或从哪采都不清楚）时才追问，且只问最关键的一个问题。

示例1（信息充分）：
用户：分析小米SU7在汽车之家的用户口碑，生成markdown报告
输出：{"need_clarification": false, "question": "", "understanding": {"intent": "分析小米SU7在汽车之家的用户口碑", "what": "用户评论/口碑", "where": "汽车之家", "output": "Markdown分析报告"}}

示例2（信息不足）：
用户：帮我搞点数据
输出：{"need_clarification": true, "question": "你想采集什么数据、从哪个平台或网址？", "understanding": {}}

示例3（仅关键词全网）：
用户：每周一三五9:30抓某招投标网最新标讯并提炼摘要
输出：{"need_clarification": false, "question": "", "understanding": {"intent": "周期性采集招投标标讯并提炼摘要", "what": "招投标标讯", "where": "招投标网（全网搜索）", "output": "摘要报告（定时）"}}"""
```

- [ ] **Step 4: 重写 intent_node**

`src/conductor/nodes/intent.py` 整体替换为：
```python
"""意图节点：理解用户意图（产出松散 understanding），要么追问澄清，要么交给规划节点。"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from src.llm import achat
from src.memory import preferences_context

from ..prompts import INTENT_SYSTEM
from ..state import ConductorState
from ..utils import parse_json_obj

logger = logging.getLogger(__name__)


async def _understand(llm_messages: list, provider, model) -> Optional[dict]:
    """调 LLM 理解意图，JSON 解析失败重试 1 次；最终失败返回 None。"""
    for attempt in range(2):
        text = await achat(llm_messages, provider=provider, model=model, temperature=0)
        obj = parse_json_obj(text)
        if obj:
            return obj
        logger.warning("意图理解 JSON 解析失败（第 %d 次）", attempt + 1)
    return None


async def intent_node(state: ConductorState) -> Dict[str, Any]:
    provider = state.get("provider")
    history = state.get("messages", [])
    user_input = state.get("user_input", "")

    system = INTENT_SYSTEM + preferences_context()
    llm_messages: list = [{"role": "system", "content": system}]
    llm_messages.extend(history)
    if not history and user_input:
        llm_messages.append({"role": "user", "content": user_input})

    try:
        obj = await _understand(llm_messages, provider, state.get("model"))
    except Exception as e:
        logger.exception("意图理解 LLM 调用失败")
        return {
            "needs_clarification": False,
            "error": f"模型调用失败：{e}。请检查 .env 中所选供应商的 API Key 与地址。",
        }

    if not obj:
        return {
            "needs_clarification": True,
            "clarification_question": (
                "我没太理解你的需求。能再具体说明一下：想采集什么数据、"
                "从哪个平台或网址、以及想要什么形式的产出（报告/数据/入库）？"
            ),
        }

    if obj.get("need_clarification"):
        return {
            "needs_clarification": True,
            "clarification_question": obj.get("question") or "请补充更多细节。",
        }

    return {
        "needs_clarification": False,
        "clarification_question": None,
        "understanding": obj.get("understanding") or {},
    }
```

- [ ] **Step 5: 运行确认通过**

Run: `python scripts/test_intent.py`
Expected: PASS（4/4 通过）。

- [ ] **Step 6: Checkpoint** —— PASS 即完成。

---

## Task 6: planner 升级为真 LLM 规划器

**Files:**
- Modify: `src/conductor/prompts.py`（新增 `PLANNER_SYSTEM`）
- Modify: `src/conductor/nodes/planner.py`
- Test: `scripts/test_planner.py`

- [ ] **Step 1: 写失败测试**

`scripts/test_planner.py`：
```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""规划节点单测：LLM 产合法 TaskSpec / 平台归一 / LLM 失败降级。运行：python scripts/test_planner.py"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import src.conductor.nodes.planner as planner_mod
from src.conductor.task_spec import DataType


def _patch_achat_return(text):
    async def fake(*args, **kwargs):
        return text
    planner_mod.achat = fake


def _patch_achat_raise():
    async def fake(*args, **kwargs):
        raise RuntimeError("LLM down")
    planner_mod.achat = fake


def test_plan_ok_and_normalize_platform():
    _patch_achat_return(
        '{"intent": "分析SU7口碑", "platforms": ["douyin"], "keywords": ["小米SU7"], '
        '"data_type": "comment", "analysis_type": "voc", "outputs": ["report_md"], '
        '"reasoning": "口碑→VOC；抖音社媒"}'
    )
    state = {"understanding": {"intent": "分析SU7口碑", "where": "抖音"}, "user_input": "分析小米SU7口碑"}
    out = asyncio.run(planner_mod.planner_node(state))
    spec = out["task_spec"]
    assert spec.data_type == DataType.COMMENT
    assert "抖音" in spec.platforms  # douyin → 归一为规范名
    assert out["plan_reasoning"]


def test_plan_llm_fail_falls_back():
    _patch_achat_raise()
    state = {"understanding": {"intent": "x"}, "user_input": "分析小米SU7口碑"}
    out = asyncio.run(planner_mod.planner_node(state))
    spec = out["task_spec"]
    # 降级仍产出合法 TaskSpec（关键词回落到用户输入）
    assert spec is not None and spec.keywords


def main():
    tests = [test_plan_ok_and_normalize_platform, test_plan_llm_fail_falls_back]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1; print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50); print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python scripts/test_planner.py`
Expected: FAIL（现 planner 不调 LLM、无 `plan_reasoning`、不归一平台）。

- [ ] **Step 3: 新增 PLANNER_SYSTEM**

`src/conductor/prompts.py` 末尾追加：
```python
# 规划：把意图理解转成完整执行策略（草稿 JSON）。{platforms} 由运行时注入已知平台词表。
PLANNER_SYSTEM = """你是"全网数据采集分析"智能体的任务规划模块。
给你用户原始诉求与上游的「理解」，请规划出一份完整的执行策略草稿。

**平台命名**：若目标属于以下已支持平台，platforms 必须使用其**规范名**（便于路由命中专用采集器）：
{platforms}
不属于上述平台的（如垂直站点、新闻站、招投标网），按用户原话填平台名或留空走全网搜索。

请**只输出一个 JSON 对象**，不要任何额外文字：
{{
  "intent": "意图一句话",
  "platforms": ["平台规范名或站点名，可空"],
  "urls": ["用户给出的URL，可空"],
  "keywords": ["检索关键词，可空"],
  "data_type": "comment|post|bid|product|article|generic",
  "time_range": "时间范围自然语言，可为null",
  "max_items": 50,
  "login_strategy": "none|cookie|session",
  "analysis_type": "voc|summary|custom|none",
  "analysis_instruction": "对输出内容/结构的具体要求，没有则null",
  "outputs": ["report_md","json","db","email","slack"],
  "db_target": "入库表名，可为null",
  "email_to": "收件人邮箱，可为null",
  "schedule": "cron@<分 时 日 月 周> 或 once@<ISO时间>，非定时为null",
  "reasoning": "用一两句话说明关键决策理由（为何选该平台/数据类型/分析方式）"
}}

规划规则：
- 槽点/口碑/吐槽类 → analysis_type=voc；其余 → summary（系统按 data_type 自动套专业模板）。
- analysis_instruction：把用户对"输出内容或结构"的具体要求原样提炼（如"只要负面""按时间线"）。
- 要求发邮件/Slack 时在 outputs 加 "email"/"slack"，邮箱提炼进 email_to（没给就 null）。
- schedule：仅当用户表达"每天/每周X/某时刻执行"时填，否则 null。
- 不臆造用户没提的约束；不确定的字段用合理默认。"""
```

- [ ] **Step 4: 重写 planner_node**

`src/conductor/nodes/planner.py` 整体替换为：
```python
"""规划节点：LLM 把意图理解转成完整执行策略草稿，再确定性校验为 TaskSpec。"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

from src.collectors import known_platforms, normalize_platform
from src.llm import achat

from ..prompts import PLANNER_SYSTEM
from ..state import ConductorState
from ..task_spec import TaskSpec
from ..utils import parse_json_obj

logger = logging.getLogger(__name__)


async def _plan(understanding: dict, user_input: str, provider, model) -> Tuple[dict, Optional[str]]:
    """调 LLM 产策略草稿；解析失败重试 1 次；最终失败返回 ({}, None) 触发兜底。"""
    platforms = "、".join(known_platforms())
    system = PLANNER_SYSTEM.format(platforms=platforms)
    user = (
        f"用户原始诉求：{user_input}\n"
        f"上游理解：{json.dumps(understanding, ensure_ascii=False)}"
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(2):
        text = await achat(messages, provider=provider, model=model, temperature=0)
        obj = parse_json_obj(text)
        if obj:
            return obj, obj.get("reasoning")
        logger.warning("规划 JSON 解析失败（第 %d 次）", attempt + 1)
    return {}, None


async def planner_node(state: ConductorState) -> Dict[str, Any]:
    understanding = state.get("understanding") or {}
    user_input = state.get("user_input", "")

    try:
        draft, reasoning = await _plan(understanding, user_input, state.get("provider"), state.get("model"))
    except Exception:
        logger.warning("规划 LLM 调用失败，降级为确定性兜底", exc_info=True)
        draft, reasoning = {}, None

    if not draft:
        logger.warning("规划草稿为空，使用 understanding/用户输入兜底构造 TaskSpec")

    # 平台名归一（即便 LLM 没用规范名，也尽量让路由命中专用采集器）
    if draft.get("platforms"):
        draft["platforms"] = [normalize_platform(p) for p in draft["platforms"] if str(p).strip()]

    spec = TaskSpec.from_draft(draft, fallback_text=user_input)
    out: Dict[str, Any] = {"task_spec": spec}
    if reasoning:
        out["plan_reasoning"] = reasoning
    return out
```

- [ ] **Step 5: 运行确认通过**

Run: `python scripts/test_planner.py`
Expected: PASS（2/2 通过）。

- [ ] **Step 6: Checkpoint** —— PASS 即完成。

---

## Task 7: 节点白盒视图 build_node_view

**Files:**
- Create: `src/conductor/node_views.py`
- Test: `scripts/test_node_views.py`

- [ ] **Step 1: 写失败测试**

`scripts/test_node_views.py`：
```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""节点白盒视图单测。运行：python scripts/test_node_views.py"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.conductor.node_views import build_node_view
from src.conductor.task_spec import TaskSpec, DataType


def test_intent_view():
    v = build_node_view("intent", {"understanding": {"intent": "分析SU7口碑"}}, {})
    assert v["understanding"]["intent"] == "分析SU7口碑"


def test_planner_view():
    spec = TaskSpec(intent="分析SU7口碑", platforms=["抖音"], data_type=DataType.COMMENT, keywords=["SU7"])
    v = build_node_view("planner", {"task_spec": spec, "plan_reasoning": "口碑→VOC"}, {})
    assert v["platforms"] == ["抖音"] and v["data_type"] == "comment"
    assert v["reasoning"] == "口碑→VOC"


def test_router_view():
    v = build_node_view("router", {"collector_candidates": ["mediacrawler", "search"]}, {})
    assert v["candidates"] == ["mediacrawler", "search"]


def test_clean_view_counts():
    v = build_node_view("clean", {"cleaned_dataset": [1, 2]}, {"raw_dataset": [1, 2, 3, 4]})
    assert v["cleaned_count"] == 2 and v["raw_count"] == 4


def test_unknown_node_empty():
    assert build_node_view("nope", {}, {}) == {}


def main():
    tests = [test_intent_view, test_planner_view, test_router_view,
             test_clean_view_counts, test_unknown_node_empty]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1; print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50); print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python scripts/test_node_views.py`
Expected: FAIL（`ModuleNotFoundError: src.conductor.node_views`）。

- [ ] **Step 3: 实现 node_views.py**

`src/conductor/node_views.py`：
```python
# -*- coding: utf-8 -*-
"""节点白盒视图：把每个节点的产出（delta）转成前端可直接展示的结构化摘要。

delta 是该节点本次返回的状态增量（首选数据源）；values 是累计状态（用于跨节点计数兜底）。
返回值必须是可 JSON 序列化的纯结构（不含 TaskSpec 等对象）。
"""
from __future__ import annotations

from typing import Any, Dict


def build_node_view(node: str, delta: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    delta = delta or {}
    values = values or {}

    if node == "intent":
        if delta.get("needs_clarification"):
            return {"clarification": delta.get("clarification_question")}
        return {"understanding": delta.get("understanding") or {}}

    if node == "planner":
        spec = delta.get("task_spec") or values.get("task_spec")
        view: Dict[str, Any] = {}
        if spec is not None:
            view = {
                "intent": spec.intent,
                "platforms": list(spec.platforms),
                "keywords": list(spec.keywords),
                "data_type": spec.data_type.value,
                "analysis_type": spec.analysis_type.value,
                "outputs": [o.value for o in spec.outputs],
                "max_items": spec.max_items,
            }
        if delta.get("plan_reasoning"):
            view["reasoning"] = delta["plan_reasoning"]
        return view

    if node == "router":
        return {"candidates": list(delta.get("collector_candidates") or [])}

    if node == "collect":
        raw = delta.get("raw_dataset") or values.get("raw_dataset") or []
        return {"collector": delta.get("collector_used") or values.get("collector_used"),
                "raw_count": len(raw)}

    if node == "clean":
        raw = values.get("raw_dataset") or []
        return {"raw_count": len(raw), "cleaned_count": len(delta.get("cleaned_dataset") or [])}

    if node == "analyze":
        return {"source": delta.get("analysis_source") or values.get("analysis_source"),
                "analysis": delta.get("analysis") or values.get("analysis")}

    if node == "checker":
        return {"quality": delta.get("quality") or values.get("quality")}

    if node == "output":
        outs = delta.get("outputs") or values.get("outputs") or {}
        return {"outputs": list(outs.keys())}

    if node == "schedule":
        return {"schedule": delta.get("schedule_request") or values.get("schedule_request")}

    return {}
```

- [ ] **Step 4: 运行确认通过**

Run: `python scripts/test_node_views.py`
Expected: PASS（5/5 通过）。

- [ ] **Step 5: Checkpoint** —— PASS 即完成。

---

## Task 8: SSE 携带节点 view（graph + chat 路由）

**Files:**
- Modify: `src/conductor/graph.py:247-254`
- Modify: `src/api/routes/chat.py:181-186`

- [ ] **Step 1: graph 的 node 事件带 view**

`src/conductor/graph.py` 在文件顶部 import 区加：
```python
from .node_views import build_node_view
```
把 `astream_conductor` 的事件循环（约 247-254 行）替换为：
```python
    final_state: Dict[str, Any] = {}
    async for mode, chunk in stream:
        if mode == "values":
            final_state = chunk if isinstance(chunk, dict) else final_state
        elif mode == "updates" and isinstance(chunk, dict):
            for node_name, delta in chunk.items():
                view = build_node_view(node_name, delta or {}, final_state)
                yield ("node", {"node": node_name, "view": view})
    yield ("final", dict(final_state))
```

> 说明：node 事件 payload 由原来的字符串改为 `{"node": ..., "view": ...}`。`astream_conductor` 的唯一消费者是 `src/api/routes/chat.py`（`run_conductor`/调度器走非流式路径，不受影响）。下一步同步改 chat 路由。

- [ ] **Step 2: 确认无其他消费者**

Run: `grep -rn "astream_conductor" src --include=*.py`
Expected: 仅 `src/conductor/graph.py`（定义）与 `src/api/routes/chat.py`（调用）。若有其它调用点，需同步适配新 payload。

- [ ] **Step 3: chat 路由透传 view**

`src/api/routes/chat.py` 把节点事件处理（约 181-186 行）替换为：
```python
                if kind == "node":
                    node_name = payload.get("node") if isinstance(payload, dict) else payload
                    view = payload.get("view") if isinstance(payload, dict) else None
                    label = _NODE_LABELS.get(node_name)
                    if label and node_name not in seen:
                        seen.add(node_name)
                        yield {"event": "node",
                               "data": json.dumps({"node": node_name, "label": label, "view": view},
                                                  ensure_ascii=False, default=str)}
```

- [ ] **Step 4: 后端导入冒烟**

Run: `python -c "import src.api.main; import src.conductor.graph; print('ok')"`
Expected: 输出 `ok`（无 import 错误）。

- [ ] **Step 5: Checkpoint** —— 导入成功 + 上游 `test_node_views.py` 仍 PASS。

---

## Task 9: 前端节点白盒卡片

**Files:**
- Modify: `frontend/src/lib/api.ts:69`
- Create: `frontend/src/components/NodeStream.tsx`
- Modify: `frontend/src/pages/Chat.tsx`

- [ ] **Step 1: 扩展 onNode 类型**

`frontend/src/lib/api.ts` 第 69 行改为：
```typescript
  onNode?: (d: { node: string; label: string; view?: any }) => void;
```

- [ ] **Step 2: 新增 NodeStream 组件**

`frontend/src/components/NodeStream.tsx`：
```tsx
import { useEffect, useState } from "react";
import { Check, Loader2, ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { PIPELINE_NODES } from "@/components/PipelineTracker";

export interface NodeEntry { node: string; label: string; view?: any }

const LABEL: Record<string, string> = Object.fromEntries(
  PIPELINE_NODES.map((n) => [n.key, n.label]),
);

/** 渲染单个节点的 view 内容（按节点类型挑关键字段，纯展示）。 */
function ViewBody({ node, view }: { node: string; view: any }) {
  if (!view || Object.keys(view).length === 0)
    return <p className="text-xs text-muted-foreground">（无更多详情）</p>;
  if (node === "intent" && view.understanding)
    return <Pre obj={view.understanding} />;
  if (node === "analyze")
    return (
      <div className="space-y-1 text-xs">
        {view.source && <div className="text-muted-foreground">模板来源：{view.source}</div>}
        {view.analysis && <div className="line-clamp-6 whitespace-pre-wrap text-foreground/80">{view.analysis}</div>}
      </div>
    );
  return <Pre obj={view} />;
}

function Pre({ obj }: { obj: any }) {
  return (
    <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-muted/50 p-2 text-[11px] leading-relaxed text-foreground/80">
      {JSON.stringify(obj, null, 2)}
    </pre>
  );
}

/** 单张可折叠节点卡片：运行中自动展开，完成后自动折叠，可点击切换。 */
function NodeCard({ entry, active }: { entry: NodeEntry; active: boolean }) {
  const [open, setOpen] = useState(active);
  useEffect(() => { setOpen(active); }, [active]); // active 切换：进入则展开，离开则自动折叠（用户点击仍可覆盖）
  const expanded = open;
  return (
    <div className="rounded-lg border border-border bg-card/60">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm"
      >
        {active ? (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
        ) : (
          <Check className="h-3.5 w-3.5 shrink-0 text-primary" strokeWidth={3} />
        )}
        <span className="flex-1 font-medium">{entry.label || LABEL[entry.node] || entry.node}</span>
        {expanded ? <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
                  : <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />}
      </button>
      {expanded && <div className="border-t border-border px-3 py-2"><ViewBody node={entry.node} view={entry.view} /></div>}
    </div>
  );
}

/** 节点白盒卡片序列；activeNode 为当前进行中的节点 key（null 表示已结束）。 */
export function NodeStream({ entries, activeNode }: { entries: NodeEntry[]; activeNode: string | null }) {
  if (!entries.length) return null;
  return (
    <div className={cn("space-y-2")}>
      {entries.map((e) => (
        <NodeCard key={e.node} entry={e} active={activeNode === e.node} />
      ))}
    </div>
  );
}
```

> 折叠行为：`NodeCard` 用 `useEffect` 跟随 `active`——进入 active（进行中）展开，离开 active（完成）自动折叠；用户点击 chevron 可手动覆盖。运行结束时 Chat 把 `activeNode` 置 null，全部卡片折叠成已完成态。

- [ ] **Step 3: Chat 收集 node view 并渲染**

`frontend/src/pages/Chat.tsx` 改动：

(a) import 增加：
```tsx
import { NodeStream, type NodeEntry } from "@/components/NodeStream";
```

(b) 新增状态（在 `doneNodes` 附近）：
```tsx
  const [nodeEntries, setNodeEntries] = useState<NodeEntry[]>([]);
  const [activeNode, setActiveNode] = useState<string | null>(null);
```

(c) `send()` 里 `setDoneNodes(new Set());` 后追加：
```tsx
    setNodeEntries([]);
    setActiveNode(null);
```

(d) `onNode` 回调替换为（累积卡片 + 标记当前 active）：
```tsx
        onNode: (d) => {
          setDoneNodes((prev) => new Set(prev).add(d.node));
          setNodeEntries((prev) =>
            prev.some((e) => e.node === d.node)
              ? prev.map((e) => (e.node === d.node ? { ...e, view: d.view, label: d.label } : e))
              : [...prev, { node: d.node, label: d.label, view: d.view }],
          );
          setActiveNode(d.node);
        },
```

(e) `onDone` 回调改为：
```tsx
        onDone: () => { setRunning(false); setActiveNode(null); },
```

(f) 在对话区消息列表内、`{running && (...正在处理…)}` 之前插入节点卡片渲染：
```tsx
            {nodeEntries.length > 0 && (
              <div className="rounded-2xl border border-border bg-card/40 p-3">
                <div className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  运行过程
                </div>
                <NodeStream entries={nodeEntries} activeNode={activeNode} />
              </div>
            )}
```

> 注意：`onNode` 在节点完成时触发，故 `activeNode` 实为「最近完成的节点」；运行结束 `onDone` 把 activeNode 置 null，则全部卡片折叠成已完成态，符合「完成后自动折叠、可点击展开」。

- [ ] **Step 4: 构建前端**

Run: `cd frontend && npm run build`
Expected: 构建成功（无 TS 报错），产物在 `frontend/dist`。完成后 `cd ..` 回到项目根。

- [ ] **Step 5: Checkpoint** —— `npm run build` 通过即完成。

---

## Task 10: 回归 + 文档刷新

**Files:**
- Modify: `AGENTS.md`、`README_AGENT.md`（链路说明段）

- [ ] **Step 1: 跑新链路全部新增单测**

Run（项目根，逐个）：
```bash
python scripts/test_platforms.py
python scripts/test_clean.py
python scripts/test_intent.py
python scripts/test_planner.py
python scripts/test_node_views.py
```
Expected: 每个均 PASS。

- [ ] **Step 2: 离线回归（不依赖网络/真实 LLM 的既有测试）**

Run（项目根，逐个）：
```bash
python scripts/test_analyze_routing.py
python scripts/test_anomaly.py
python scripts/test_checkpoint.py
python scripts/test_memory.py
python scripts/test_scheduler.py
python scripts/test_template_learning.py
python scripts/test_hardening.py
```
Expected: 每个均 PASS。若某测试断言旧的 `spec_draft` 行为（intent 不再产 spec_draft），按新链路把断言改为 `understanding`/`task_spec` 后再跑通。

- [ ] **Step 3: 后端导入冒烟**

Run: `python -c "import src.api.main; print('ok')"`
Expected: `ok`。

- [ ] **Step 4: 刷新文档**

在 `AGENTS.md` 与 `README_AGENT.md` 的流水线/节点说明处，更新两点：
1. intent 节点职责改为「理解 + 追问闸门，产出 understanding」；planner 改为「LLM 真规划器，产出完整 TaskSpec + reasoning，失败降级不阻断」。
2. 增补「SSE node 事件携带 view，前端按节点白盒展示，完成自动折叠可展开」。
（仅改动相关段落，保持其余文字不动。）

- [ ] **Step 5: Checkpoint** —— 全部测试 PASS + 文档已更新。

---

## 完成定义（Definition of Done）

- 新增 5 个测试脚本全部 PASS；离线回归 7 个既有测试全部 PASS。
- `npm run build` 通过。
- planner 真正调用 LLM 规划并能在 LLM 失败时降级；平台名归一生效；clean 噪声剥离生效。
- 前端聊天界面每个节点以白盒卡片呈现，运行中展开、完成折叠、可点击展开。
- `AGENTS.md` / `README_AGENT.md` 链路说明已更新。
