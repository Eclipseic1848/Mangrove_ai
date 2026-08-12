# Mangrove 技能体系重构（skills 声明式化）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `skills/*.md` 从"禁止 frontmatter、硬编码选择逻辑"改为"必须 frontmatter、声明式触发匹配"，删除从未生效的死代码 `select_skills()`，把从未被注入任何 prompt 的 `scrape-social-media.md` 归档为纯文档，并新增 3 个真正有价值的技能（对比分析、趋势分析、平台选型经验）。

**Architecture:** 新增一个共享的 frontmatter 解析函数（`src/memory/_frontmatter.py`），`data/templates/` 与 `skills/` 都改用它解析各自的 YAML frontmatter。`src/memory/loader.py` 的 `load_skills()` 按 frontmatter 里的 `inject`/`trigger` 字段返回结构化技能信息；`skill_for_analysis(spec)`（函数名/调用点不变）与新增的 `skills_for_planner()` 分别按各自节点可用的信息（`TaskSpec` / 无参数只支持恒注入）匹配并拼接技能正文。

**Tech Stack:** Python 3.13、PyYAML（已是依赖）、项目自有的无 pytest 测试约定（`scripts/test_*.py`，`def test_x(): assert ...`，`main()` 收集 PASS/FAIL）。

## Global Constraints

- Frontmatter 范式（写进每个技能文件顶部）：
  ```yaml
  ---
  title: <技能标题>
  inject: analyze | planner    # 必填
  trigger:
    analysis_type: voc         # 可选，匹配 TaskSpec.analysis_type
    data_type: [comment, post] # 可选，列表内 OR，匹配 TaskSpec.data_type
    intent_keywords: [对比, 比较]  # 可选，列表内任一词命中 intent+keywords 即算命中
    time_range_required: true  # 可选，要求 TaskSpec.time_range 非空
    always: true                # 与其余键互斥独占：出现即恒命中，忽略同级其它键
  ---
  <正文>
  ```
  各 trigger 键之间是 AND 关系（同一技能可以要求"analysis_type=voc 且 data_type=comment"同时成立）。
- **`inject: planner` 目前只支持 `trigger.always: true` 一种触发方式**——planner 运行时 `TaskSpec` 尚未生成，只有松散的 `understanding` 字典，不支持关键词式精确匹配。不要给 planner 侧技能加 `analysis_type`/`intent_keywords` 之类字段，加了也不会被读取。
- **无 frontmatter 的文件按文件名排除 `readme`**（不依赖"无 frontmatter 自动跳过"兜底）——`README.md` 是每次调用都会遇到的预期情况，不应该在每个请求里都打一条日志。除 README 外，无 frontmatter / YAML 解析失败 / 解析成功但正文为空，这三种情况统一打 `logger.info`（不是 `warning`——技能是手写的，忘加 frontmatter 是真实失误路径，需要留痕迹但不需要惊动运维）。
- `skill_for_analysis(spec: TaskSpec) -> str` **函数名与调用点必须保留**（`src/conductor/nodes/analyze.py:209` 的 `system += skill_for_analysis(spec)` 不改）。
- 只有专用采集器支持的 10 个平台可以出现在"平台选型经验"技能的推荐表里（已核实 `src/collectors/platforms.py:17-19` 的 `KNOWN_PLATFORMS`）：**抖音、小红书、微博、B站、快手、知乎、贴吧、京东、YouTube、V2EX**。懂车帝/汽车之家等站点不在此列，不得出现在该技能建议"填入 platforms 字段"的示例里。
- 所有中文内容用 UTF-8 保存；写完文件后用 `iconv -f utf-8 -t utf-8 <file> > /dev/null` 校验编码完整性（项目既有习惯）。
- 每个任务跑完对应测试全绿才算完成；不要求跑全量回归，但 Task 1/2 完成后需跑 `test_template_learning.py`/`test_embeddings.py` 确认模板侧无回归（这两个文件间接依赖 `templates.py` 的 frontmatter 解析路径）。

---

## Task 1: 共享 frontmatter 解析器 + templates.py 迁移

**Files:**
- Create: `src/memory/_frontmatter.py`
- Create: `scripts/test_frontmatter.py`
- Modify: `src/memory/templates.py:16-34`（import 与 `_FRONTMATTER_RE` 定义）、`src/memory/templates.py:46-81`（`load_templates()`）、`src/memory/templates.py:338-383`（`record_template_use()`）

**Interfaces:**
- Produces: `parse_frontmatter(raw: str) -> Optional[Tuple[dict, str]]`——无 frontmatter 返回 `None`；有 frontmatter 但 YAML 解析失败抛 `FrontmatterError`；成功返回 `(meta_dict, body_str)`（`body` 已 `.strip()`）。`FrontmatterError` 同模块导出。

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_frontmatter.py`：

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""共享 frontmatter 解析器单元测试（skills/ 与 data/templates/ 共用）。

运行：python scripts/test_frontmatter.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.memory._frontmatter import FrontmatterError, parse_frontmatter


def test_no_frontmatter_returns_none():
    assert parse_frontmatter("# 纯文档\n没有 frontmatter") is None


def test_valid_frontmatter_parses():
    raw = "---\ntitle: 示例\nkeywords: [a, b]\n---\n正文内容"
    result = parse_frontmatter(raw)
    assert result is not None
    meta, body = result
    assert meta["title"] == "示例"
    assert meta["keywords"] == ["a", "b"]
    assert body == "正文内容"


def test_malformed_yaml_raises():
    raw = "---\ntitle: {unbalanced\n---\n正文"
    try:
        parse_frontmatter(raw)
        assert False, "应抛出 FrontmatterError"
    except FrontmatterError:
        pass


def test_empty_body_after_frontmatter():
    raw = "---\ntitle: 示例\n---\n   \n"
    meta, body = parse_frontmatter(raw)
    assert meta["title"] == "示例"
    assert body == ""  # 调用方负责判断空正文是否跳过


def main():
    tests = [
        test_no_frontmatter_returns_none,
        test_valid_frontmatter_parses,
        test_malformed_yaml_raises,
        test_empty_body_after_frontmatter,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50)
    print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python scripts/test_frontmatter.py`
Expected: `ModuleNotFoundError: No module named 'src.memory._frontmatter'`（文件还不存在）

- [ ] **Step 3: 实现 `src/memory/_frontmatter.py`**

```python
"""
共享 frontmatter 解析：`skills/*.md`（手写技能）与 `data/templates/*.md`（自学习模板）
都用 `---\nYAML\n---\n正文` 这套格式，抽出来避免两处各自维护一份正则+解析逻辑。
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


class FrontmatterError(Exception):
    """有 frontmatter 分隔符但 YAML 解析失败（区别于"根本没有 frontmatter"，
    调用方按各自的日志级别处理这两种情况）。"""


def parse_frontmatter(raw: str) -> Optional[Tuple[dict, str]]:
    """解析 `---\nYAML\n---\n正文` 格式。

    返回 (meta, body)（body 已 strip）；完全没有 frontmatter（如纯文档）返回 None；
    有 frontmatter 但 YAML 解析失败抛 FrontmatterError。
    """
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return None
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except Exception as e:
        raise FrontmatterError(str(e)) from e
    return meta, m.group(2).strip()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python scripts/test_frontmatter.py`
Expected: `4/4 通过`

- [ ] **Step 5: 迁移 `src/memory/templates.py` 改用共享解析器**

修改顶部 import 区（第 16-34 行附近），删掉 `_FRONTMATTER_RE` 常量定义，加入：

```python
from src.memory._frontmatter import FrontmatterError, parse_frontmatter
```

（`import re` 保留——`_slugify()` 还在用；只删 `_FRONTMATTER_RE = re.compile(...)` 这一行。）

把 `load_templates()`（原第 46-81 行）里的这段：

```python
        m = _FRONTMATTER_RE.match(raw)
        if not m:
            continue  # 无 frontmatter（如 README.md）跳过
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except Exception:
            logger.warning("模板 frontmatter 解析失败：%s", p.name)
            continue
        body = m.group(2).strip()
        if not body:
            continue
```

改为：

```python
        try:
            parsed = parse_frontmatter(raw)
        except FrontmatterError:
            logger.warning("模板 frontmatter 解析失败：%s", p.name)
            continue
        if parsed is None:
            continue  # 无 frontmatter（如 README.md）跳过
        meta, body = parsed
        if not body:
            continue
```

把 `record_template_use()`（原第 338-383 行）里的这段：

```python
    try:
        raw = path.read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(raw)
        if not m:
            return None
        meta = yaml.safe_load(m.group(1)) or {}
        body = m.group(2).strip()
    except Exception:
        logger.warning("读取模板失败，跳过统计回写：%s", slug)
        return None
```

改为：

```python
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        logger.warning("读取模板失败，跳过统计回写：%s", slug)
        return None
    try:
        parsed = parse_frontmatter(raw)
    except FrontmatterError:
        logger.warning("模板 frontmatter 解析失败，跳过统计回写：%s", slug)
        return None
    if parsed is None:
        return None
    meta, body = parsed
```

- [ ] **Step 6: 回归验证模板侧无破坏**

Run: `python scripts/test_template_learning.py`
Expected: `6/6 通过`

Run: `python scripts/test_embeddings.py`
Expected: `4/4 通过`

- [ ] **Step 7: 校验编码 + 提交**

```bash
iconv -f utf-8 -t utf-8 src/memory/_frontmatter.py > /dev/null
iconv -f utf-8 -t utf-8 src/memory/templates.py > /dev/null
git add src/memory/_frontmatter.py src/memory/templates.py scripts/test_frontmatter.py
git commit -m "feat: 抽出共享 frontmatter 解析器，templates.py 改用之"
```

---

## Task 2: loader.py 声明式技能匹配引擎重写

**Files:**
- Modify: `src/memory/loader.py`（全文重写 `load_skills`/`skill_for_analysis`，新增 `skills_for_planner`，删除 `select_skills`/`get_skill`）
- Modify: `src/memory/__init__.py`（导出增删）
- Modify: `scripts/test_memory.py`（`test_load_skills`/`test_select_and_inject_skills` 整体替换；`test_preferences_context_and_add` 不动）

**Interfaces:**
- Consumes: `parse_frontmatter`/`FrontmatterError`（Task 1 产出，`from src.memory._frontmatter import ...`）
- Produces:
  - `load_skills() -> Dict[str, Dict[str, Any]]`，每项含 `{name, title, body, inject, trigger}`
  - `skill_for_analysis(spec: TaskSpec) -> str`（签名不变）
  - `skills_for_planner() -> str`（新函数，无参数——理由见 Global Constraints）

- [ ] **Step 1: 写失败测试**

用下面内容**整体替换** `scripts/test_memory.py`（`test_preferences_context_and_add` 原样保留，只是位置挪到文件里；下面给出完整文件内容）：

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""记忆与技能加载单元测试。

运行：python scripts/test_memory.py
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.memory import loader
from src.conductor.task_spec import AnalysisType, DataType, TaskSpec


def _tmp_skills_dir() -> Path:
    """把 loader.SKILLS_DIR 重定向到临时目录，隔离测试、不依赖真实 skills/ 的文件内容。"""
    d = Path(tempfile.mkdtemp(prefix="mg_skills_"))
    loader.SKILLS_DIR = d
    return d


def _write_skill(d: Path, filename: str, content: str) -> None:
    (d / filename).write_text(content, encoding="utf-8")


def test_load_skills_parses_frontmatter():
    d = _tmp_skills_dir()
    _write_skill(d, "demo.md", "---\ntitle: 示例技能\ninject: analyze\ntrigger:\n  always: true\n---\n正文内容")
    skills = loader.load_skills()
    assert "demo" in skills
    assert skills["demo"]["title"] == "示例技能"
    assert skills["demo"]["inject"] == "analyze"
    assert skills["demo"]["body"] == "正文内容"


def test_load_skills_skips_readme_silently():
    d = _tmp_skills_dir()
    _write_skill(d, "README.md", "# 说明文档，没有 frontmatter")
    assert loader.load_skills() == {}


def test_load_skills_skips_no_frontmatter_with_info_log():
    d = _tmp_skills_dir()
    _write_skill(d, "draft.md", "# 忘了加 frontmatter 的技能草稿")
    with patch.object(loader.logger, "info") as mock_info:
        skills = loader.load_skills()
    assert "draft" not in skills
    assert mock_info.called


def test_load_skills_skips_malformed_yaml_with_info_log():
    d = _tmp_skills_dir()
    _write_skill(d, "broken.md", "---\ntitle: {unbalanced\n---\n正文")
    with patch.object(loader.logger, "info") as mock_info:
        skills = loader.load_skills()
    assert "broken" not in skills
    assert mock_info.called


def test_load_skills_skips_empty_body():
    d = _tmp_skills_dir()
    _write_skill(d, "empty.md", "---\ntitle: 空技能\ninject: analyze\n---\n   \n")
    with patch.object(loader.logger, "info") as mock_info:
        skills = loader.load_skills()
    assert "empty" not in skills
    assert mock_info.called


def test_skill_for_analysis_matches_analysis_type():
    d = _tmp_skills_dir()
    _write_skill(d, "voc-demo.md",
        "---\ntitle: VOC做法\ninject: analyze\ntrigger:\n  analysis_type: voc\n---\nVOC正文标记ABC")
    spec = TaskSpec(intent="口碑", analysis_type=AnalysisType.VOC)
    injected = loader.skill_for_analysis(spec)
    assert "VOC正文标记ABC" in injected
    assert "VOC做法" in injected  # 标题也应出现在注入的小标题里


def test_skill_for_analysis_no_match_returns_empty():
    d = _tmp_skills_dir()
    _write_skill(d, "voc-demo.md",
        "---\ntitle: VOC做法\ninject: analyze\ntrigger:\n  analysis_type: voc\n---\nVOC正文")
    spec = TaskSpec(intent="摘要", analysis_type=AnalysisType.SUMMARY)
    assert loader.skill_for_analysis(spec) == ""


def test_skill_for_analysis_intent_keywords_or_semantics():
    d = _tmp_skills_dir()
    _write_skill(d, "cmp.md",
        "---\ntitle: 对比做法\ninject: analyze\ntrigger:\n  intent_keywords: [对比, 比较]\n---\n对比正文")
    spec = TaskSpec(intent="比较一下两款产品", analysis_type=AnalysisType.SUMMARY)
    assert "对比正文" in loader.skill_for_analysis(spec)


def test_skill_for_analysis_data_type_list_or_semantics():
    d = _tmp_skills_dir()
    _write_skill(d, "cd.md",
        "---\ntitle: 评论帖子做法\ninject: analyze\ntrigger:\n  data_type: [comment, post]\n---\n评论帖子正文")
    spec_comment = TaskSpec(intent="x", data_type=DataType.COMMENT)
    spec_bid = TaskSpec(intent="x", data_type=DataType.BID)
    assert "评论帖子正文" in loader.skill_for_analysis(spec_comment)
    assert loader.skill_for_analysis(spec_bid) == ""


def test_skill_for_analysis_and_across_trigger_keys():
    d = _tmp_skills_dir()
    _write_skill(d, "strict.md",
        "---\ntitle: 严格触发\ninject: analyze\ntrigger:\n  analysis_type: voc\n  data_type: [comment]\n---\n严格正文")
    matches = TaskSpec(intent="x", analysis_type=AnalysisType.VOC, data_type=DataType.COMMENT)
    only_voc = TaskSpec(intent="x", analysis_type=AnalysisType.VOC, data_type=DataType.ARTICLE)
    assert "严格正文" in loader.skill_for_analysis(matches)
    assert loader.skill_for_analysis(only_voc) == ""  # data_type 不满足，AND 语义应拒绝


def test_skill_for_analysis_time_range_required():
    d = _tmp_skills_dir()
    _write_skill(d, "trend.md",
        "---\ntitle: 趋势做法\ninject: analyze\ntrigger:\n  time_range_required: true\n  intent_keywords: [趋势]\n---\n趋势正文")
    spec_no_range = TaskSpec(intent="趋势变化", time_range=None)
    spec_with_range = TaskSpec(intent="趋势变化", time_range="最近30天")
    assert loader.skill_for_analysis(spec_no_range) == ""
    assert "趋势正文" in loader.skill_for_analysis(spec_with_range)


def test_skill_for_analysis_ignores_planner_inject():
    d = _tmp_skills_dir()
    _write_skill(d, "plan-demo.md",
        "---\ntitle: 平台做法\ninject: planner\ntrigger:\n  always: true\n---\n平台正文")
    spec = TaskSpec(intent="x")
    assert loader.skill_for_analysis(spec) == ""  # inject:planner 的技能不进 analyze


def test_skills_for_planner_always_trigger():
    d = _tmp_skills_dir()
    _write_skill(d, "plan-demo.md",
        "---\ntitle: 平台做法\ninject: planner\ntrigger:\n  always: true\n---\n平台正文")
    assert "平台正文" in loader.skills_for_planner()


def test_skills_for_planner_ignores_analyze_inject():
    d = _tmp_skills_dir()
    _write_skill(d, "voc-demo.md",
        "---\ntitle: VOC做法\ninject: analyze\ntrigger:\n  analysis_type: voc\n---\nVOC正文")
    assert loader.skills_for_planner() == ""


def test_select_skills_removed():
    assert not hasattr(loader, "select_skills")


def test_preferences_context_and_add():
    # 用临时目录，避免污染真实 memory/
    with tempfile.TemporaryDirectory() as d:
        old_dir = loader.MEMORY_DIR
        try:
            loader.MEMORY_DIR = Path(d)
            # 初始为空
            assert loader.load_preferences() == ""
            assert loader.preferences_context() == ""
            # 追加偏好 → 落盘 + 注入上下文包含该偏好
            assert loader.add_preference("默认产出 JSON")
            pref = loader.load_preferences()
            assert "默认产出 JSON" in pref
            ctx = loader.preferences_context()
            assert "用户偏好" in ctx and "默认产出 JSON" in ctx
            # 再加一条，两条都在
            loader.add_preference("- 语言：英文")  # 前缀 - 应被规整
            pref2 = loader.load_preferences()
            assert "默认产出 JSON" in pref2 and "语言：英文" in pref2
            # 空输入不写入
            assert loader.add_preference("   ") is False
        finally:
            loader.MEMORY_DIR = old_dir


def main():
    tests = [
        test_load_skills_parses_frontmatter,
        test_load_skills_skips_readme_silently,
        test_load_skills_skips_no_frontmatter_with_info_log,
        test_load_skills_skips_malformed_yaml_with_info_log,
        test_load_skills_skips_empty_body,
        test_skill_for_analysis_matches_analysis_type,
        test_skill_for_analysis_no_match_returns_empty,
        test_skill_for_analysis_intent_keywords_or_semantics,
        test_skill_for_analysis_data_type_list_or_semantics,
        test_skill_for_analysis_and_across_trigger_keys,
        test_skill_for_analysis_time_range_required,
        test_skill_for_analysis_ignores_planner_inject,
        test_skills_for_planner_always_trigger,
        test_skills_for_planner_ignores_analyze_inject,
        test_select_skills_removed,
        test_preferences_context_and_add,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50)
    print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python scripts/test_memory.py`
Expected: `AttributeError`（`loader.load_skills()` 还是旧版，不认识新 frontmatter；`skills_for_planner` 不存在）

- [ ] **Step 3: 重写 `src/memory/loader.py`**

用下面内容**整体替换**全文（保留文件头部关于"记忆"的说明与 `load_preferences`/`preferences_context`/`add_preference` 三个函数不变，只重写"技能"部分）：

```python
"""
记忆与技能加载（loops Engineering 的 Memory/Skills 层）。

- 记忆：读取 memory/user-preferences.md，把跨会话的用户偏好注入意图理解提示词；
  并支持在会话中追加新偏好（持久化到该文件）。
- 技能：读取 skills/*.md（每个文件必须带 YAML frontmatter 声明 inject/trigger），
  按 TaskSpec（analyze 侧）或恒触发（planner 侧）匹配后注入对应节点提示词。

纯文件读写、无第三方依赖（除 PyYAML）。文件较小，按需读取（不缓存），
保证 /remember 后即时生效，技能文件改完立即生效。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from src.config.settings import PROJECT_ROOT
from src.conductor.task_spec import TaskSpec
from src.memory._frontmatter import FrontmatterError, parse_frontmatter

logger = logging.getLogger(__name__)

MEMORY_DIR = PROJECT_ROOT / "memory"
SKILLS_DIR = PROJECT_ROOT / "skills"
_PREF_FILE = "user-preferences.md"
_PREF_SECTION = "## 用户偏好"


# ---- 记忆：用户偏好 ----
def load_preferences() -> str:
    """读取用户偏好记忆全文；不存在或失败返回空串。"""
    f = MEMORY_DIR / _PREF_FILE
    try:
        return f.read_text(encoding="utf-8").strip() if f.exists() else ""
    except Exception:
        logger.warning("读取用户偏好失败", exc_info=True)
        return ""


def preferences_context() -> str:
    """构造注入意图提示词的偏好上下文（无偏好则空串）。"""
    pref = load_preferences()
    if not pref:
        return ""
    return (
        "\n\n# 用户偏好（记忆，跨会话）\n"
        f"{pref}\n"
        "在不与用户本轮明确指令冲突时，请遵循上述偏好。"
    )


def personal_context() -> str:
    """构造注入意图提示词的个人记忆上下文（当前用户自己写的，按用户隔离，见 src/config/user_ctx.py）。
    无个人记忆则返回空串。"""
    from src.config.user_ctx import get_user_memories

    items = get_user_memories()
    if not items:
        return ""
    lines = "\n".join(f"- {t}" for t in items)
    return (
        "\n\n# 我的偏好（当前用户的个人记忆）\n"
        f"{lines}\n"
        "个人偏好与全局偏好冲突时，以个人偏好为准；与用户本轮明确指令冲突时以指令为准。"
    )


def add_preference(text: str) -> bool:
    """把一条偏好追加到 user-preferences.md 的「## 用户偏好」小节；成功返回 True。"""
    text = (text or "").strip().lstrip("-").strip()
    if not text:
        return False
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    f = MEMORY_DIR / _PREF_FILE
    content = f.read_text(encoding="utf-8") if f.exists() else (
        "# 记忆：用户偏好与任务模板\n\n" + _PREF_SECTION + "\n"
    )
    line = f"- {text}\n"
    if _PREF_SECTION in content:
        idx = content.index(_PREF_SECTION)
        insert_at = content.index("\n", idx) + 1  # 小节标题行之后
        content = content[:insert_at] + line + content[insert_at:]
    else:
        content += f"\n{_PREF_SECTION}\n{line}"
    f.write_text(content, encoding="utf-8")
    return True


# ---- 技能：声明式加载与匹配 ----
def load_skills() -> Dict[str, Dict[str, Any]]:
    """加载 skills/*.md，返回 {name: {name, title, body, inject, trigger}}。

    README.md 按文件名排除（每次调用都会遇到、预期内的"无 frontmatter"，
    不参与下面的跳过留痕逻辑，避免每个请求都打一条无意义日志）。
    其余文件若无 frontmatter / YAML 解析失败 / 解析成功但正文为空，视为
    "本该是技能却没写对"的真实失误信号，跳过并打 info 日志留痕——这点和
    data/templates/（机器生成，frontmatter 必然存在）不同，那边同类情况静默跳过即可。
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not SKILLS_DIR.exists():
        return out
    for p in sorted(SKILLS_DIR.glob("*.md")):
        if p.stem.lower() == "readme":
            continue
        try:
            raw = p.read_text(encoding="utf-8")
        except Exception:
            continue
        try:
            parsed = parse_frontmatter(raw)
        except FrontmatterError as e:
            logger.info("技能文件 frontmatter 解析失败，跳过：%s（%s）", p.name, e)
            continue
        if parsed is None:
            logger.info("技能文件无 frontmatter，跳过：%s", p.name)
            continue
        meta, body = parsed
        if not body:
            logger.info("技能文件正文为空，跳过：%s", p.name)
            continue
        out[p.stem] = {
            "name": p.stem,
            "title": str(meta.get("title") or p.stem),
            "body": body,
            "inject": str(meta.get("inject") or "").strip().lower(),
            "trigger": meta.get("trigger") or {},
        }
    return out


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _skill_matches(trigger: Dict[str, Any], spec: TaskSpec) -> bool:
    """按 trigger 字段判断某技能是否命中当前 TaskSpec。各键之间 AND，
    列表型键内部 OR。trigger 为空视为永不命中（防止空 trigger 误伤）。"""
    if not trigger:
        return False
    if trigger.get("always"):
        return True
    if "analysis_type" in trigger:
        if spec.analysis_type.value != str(trigger["analysis_type"]).strip().lower():
            return False
    if "data_type" in trigger:
        allowed = {str(a).strip().lower() for a in _as_list(trigger["data_type"])}
        if spec.data_type.value not in allowed:
            return False
    if trigger.get("time_range_required") and not (spec.time_range or "").strip():
        return False
    if "intent_keywords" in trigger:
        haystack = ((spec.intent or "") + " " + " ".join(spec.keywords or [])).lower()
        kws = [str(k).strip().lower() for k in _as_list(trigger["intent_keywords"]) if str(k).strip()]
        if not any(k in haystack for k in kws):
            return False
    return True


def skill_for_analysis(spec: TaskSpec) -> str:
    """分析节点用：把 frontmatter 声明 inject: analyze 且 trigger 命中的技能正文
    依次追加进分析 system prompt；都不命中返回空串。"""
    parts = []
    for sk in load_skills().values():
        if sk["inject"] != "analyze":
            continue
        if _skill_matches(sk["trigger"], spec):
            parts.append(f"\n\n# 参考技能：{sk['title']}\n{sk['body']}")
    return "".join(parts)


def skills_for_planner() -> str:
    """规划节点用：把 frontmatter 声明 inject: planner 且 trigger.always 的技能正文
    追加进规划 system prompt。规划阶段 TaskSpec 尚未生成，暂只支持恒触发（见 Global Constraints）。"""
    parts = []
    for sk in load_skills().values():
        if sk["inject"] != "planner":
            continue
        if sk["trigger"].get("always"):
            parts.append(f"\n\n# 参考技能：{sk['title']}\n{sk['body']}")
    return "".join(parts)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python scripts/test_memory.py`
Expected: `16/16 通过`

- [ ] **Step 5: 更新 `src/memory/__init__.py` 导出**

Read current file first, then replace its content with:

```python
"""记忆与技能层：跨会话用户偏好 + 个人记忆 + 任务技能复用 + 分析模板自学习。"""
from .loader import (
    add_preference,
    load_preferences,
    load_skills,
    personal_context,
    preferences_context,
    skill_for_analysis,
    skills_for_planner,
)
from .templates import (
    delete_template,
    distill_template,
    find_duplicate,
    load_templates,
    match_template,
    record_template_use,
    save_template,
)

__all__ = [
    "load_preferences",
    "preferences_context",
    "personal_context",
    "add_preference",
    "load_skills",
    "skill_for_analysis",
    "skills_for_planner",
    "load_templates",
    "match_template",
    "save_template",
    "distill_template",
    "record_template_use",
    "find_duplicate",
    "delete_template",
]
```

（去掉了 `get_skill`/`select_skills` 两个导出——`get_skill` 也随死代码一起删，全仓已确认无其它调用方。）

- [ ] **Step 6: 全量确认**

Run: `python scripts/test_memory.py`
Expected: `16/16 通过`

Run: `python -c "import src.api.main"`（确认 `__init__.py` 导出改动不破坏应用启动）
Expected: 无 `ImportError`

- [ ] **Step 7: 校验编码 + 提交**

```bash
iconv -f utf-8 -t utf-8 src/memory/loader.py > /dev/null
iconv -f utf-8 -t utf-8 src/memory/__init__.py > /dev/null
iconv -f utf-8 -t utf-8 scripts/test_memory.py > /dev/null
git add src/memory/loader.py src/memory/__init__.py scripts/test_memory.py
git commit -m "feat: 技能加载改为声明式（frontmatter 驱动），删除死代码 select_skills/get_skill"
```

---

## Task 3: planner.py 接入 skills_for_planner

**Files:**
- Modify: `src/conductor/nodes/planner.py:48-56`（`_plan()` 函数）
- Modify: `scripts/test_planner.py`（新增 1 个测试 + import 补充）

**Interfaces:**
- Consumes: `skills_for_planner()`（Task 2 产出，来自 `src.memory`）

- [ ] **Step 1: 写失败测试**

在 `scripts/test_planner.py` 顶部 import 区加一行：

```python
import tempfile
```

（文件已有 `from pathlib import Path`，够用。）

在 `test_plan_skips_quantity_when_urls_given` 函数后面加一个新测试：

```python
def test_plan_includes_planner_skills():
    # 用临时 SKILLS_DIR 隔离，不依赖真实 skills/ 目录已有哪些文件
    import src.memory.loader as loader_mod
    d = Path(tempfile.mkdtemp(prefix="mg_skills_planner_"))
    old_dir = loader_mod.SKILLS_DIR
    loader_mod.SKILLS_DIR = d
    (d / "demo-platform.md").write_text(
        "---\ntitle: 演示平台技能\ninject: planner\ntrigger:\n  always: true\n---\n演示平台正文标记XYZ",
        encoding="utf-8",
    )
    try:
        captured = {}

        async def fake(messages, *args, **kwargs):
            captured["system"] = messages[0]["content"]
            return '{"intent": "x", "keywords": ["k"], "outputs": ["report_md"]}'

        planner_mod.achat = fake
        state = {"understanding": {"intent": "x"}, "user_input": "x"}
        asyncio.run(planner_mod.planner_node(state))
        assert "演示平台正文标记XYZ" in captured["system"]
    finally:
        loader_mod.SKILLS_DIR = old_dir
```

把 `main()` 里的 `tests` 列表加上 `test_plan_includes_planner_skills`。

- [ ] **Step 2: 运行测试确认失败**

Run: `python scripts/test_planner.py`
Expected: `FAIL  test_plan_includes_planner_skills: assert '演示平台正文标记XYZ' in captured["system"]`
（`_plan()` 还没拼接 `skills_for_planner()`）

- [ ] **Step 3: 修改 `planner.py` 的 `_plan()`**

把第 9-11 行 import 区（绝对路径 `src.*` 一组）的：

```python
from src.collectors import known_platforms, normalize_platform
from src.config.settings import settings
from src.llm import achat
```

改为（新增一行，仍属同一组，与第 13 行起的相对路径 `..` 分组保持既有的空行分隔不变）：

```python
from src.collectors import known_platforms, normalize_platform
from src.config.settings import settings
from src.llm import achat
from src.memory import skills_for_planner
```

把 `_plan()`（第 48-63 行）里的：

```python
    platforms = "、".join(known_platforms())
    system = PLANNER_SYSTEM.format(platforms=platforms)
```

改为：

```python
    platforms = "、".join(known_platforms())
    system = PLANNER_SYSTEM.format(platforms=platforms) + skills_for_planner()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python scripts/test_planner.py`
Expected: `8/8 通过`

- [ ] **Step 5: 校验编码 + 提交**

```bash
iconv -f utf-8 -t utf-8 src/conductor/nodes/planner.py > /dev/null
iconv -f utf-8 -t utf-8 scripts/test_planner.py > /dev/null
git add src/conductor/nodes/planner.py scripts/test_planner.py
git commit -m "feat: planner 节点接入恒触发技能（skills_for_planner）"
```

---

## Task 4: 现有技能文件迁移到新范式

**Files:**
- Modify: `skills/voc-analysis.md`（补 frontmatter，正文不变）
- Delete/Move: `skills/scrape-social-media.md` → `docs/scrape-social-media.md`（`git mv`，内容不变）

**Interfaces:**
- Consumes: Task 2 的 frontmatter 范式（`inject`/`trigger` 字段）

- [ ] **Step 1: 给 `voc-analysis.md` 补 frontmatter**

读取当前 `skills/voc-analysis.md` 全文，在最前面加上这段 frontmatter（正文一字不改）：

```markdown
---
title: VOC 槽点分析做法
inject: analyze
trigger:
  analysis_type: voc
---
# 技能：用户声音 / 槽点分析（VOC）

## 何时使用
用户想了解某产品/品牌在某平台的口碑、槽点、吐槽、用户反馈时（analysis_type=voc）。

## 输出结构（Markdown）
1. **主要槽点**：负面反馈归类，按出现频次直觉排序。
2. **正面反馈**：用户认可点。
3. **高频关键词**：反复出现的词/短语。
4. **典型原声**：有代表性的用户原话引用（忠于原文）。
5. **改进建议**：基于槽点给出的可执行建议。

## 注意
- 忠于原文，不杜撰；无法判断的不要硬凑。
- 数据量大时已被清洗节点截断，分析以"样本代表性"表述，不要声称全量统计。
- 可复用既有引擎 `src/services/voc_processor/`（针对结构化评论 JSON 的标签提取）。
```

- [ ] **Step 2: 归档 `scrape-social-media.md`**

```bash
git mv skills/scrape-social-media.md docs/scrape-social-media.md
```

- [ ] **Step 3: 验证真实目录能正常加载**

```bash
python -c "
from src.memory import load_skills
skills = load_skills()
assert 'voc-analysis' in skills
assert skills['voc-analysis']['inject'] == 'analyze'
assert 'scrape-social-media' not in skills
print('OK:', list(skills.keys()))
"
```
Expected: 打印 `OK: ['voc-analysis']`，无异常

- [ ] **Step 4: 跑一遍已有回归**

Run: `python scripts/test_memory.py`
Expected: `16/16 通过`（真实文件改动不影响用临时目录隔离的单测）

- [ ] **Step 5: 校验编码 + 提交**

```bash
iconv -f utf-8 -t utf-8 skills/voc-analysis.md > /dev/null
git add skills/voc-analysis.md docs/scrape-social-media.md
git commit -m "feat: voc-analysis 补 frontmatter，scrape-social-media 归档为纯文档"
```

---

## Task 5: 新增技能 comparison-analysis.md

**Files:**
- Create: `skills/comparison-analysis.md`

**Interfaces:**
- Consumes: Task 2 的 frontmatter 范式

- [ ] **Step 1: 创建文件**

```markdown
---
title: 对比分析做法
inject: analyze
trigger:
  intent_keywords: [对比, 比较, vs, VS, 哪个好, 谁更, 差异, 优劣]
---
# 技能：对比分析

## 何时使用
用户要求对比两个或多个对象（品牌/产品/平台/方案）的口碑、评价或数据表现时。

## 输出结构（Markdown）
1. **对比维度**：先归纳双方（或多方）共有的评价维度（如价格、质量、服务、体验），
   维度应来自实际采集到的数据，不臆造数据里没有的维度。
2. **对比表格**：维度 × 对象组织，每格给结论性描述，末列给"差异"小结。
3. **差异点提炼**：挑出差异最显著的 2-4 个维度重点展开。
4. **选择建议**：基于对比结果给出适用场景建议（如"预算有限选A，看重体验选B"）。

## 注意
- 样本量不均衡时（如A有200条评论、B只有20条）必须显式披露样本量差异，
  按占比/比例而非绝对数量做比较，避免"A负面评论数比B多"这种因样本基数不同而失真的结论。
- 某一方数据缺失或明显不足时，如实标注"该项数据不足以支撑对比"，不得为凑对比结构而硬造结论。
- 忠于原文，不杜撰；无法判断的维度不要硬凑。
```

- [ ] **Step 2: 验证加载与触发**

```bash
python -c "
from src.memory import load_skills, skill_for_analysis
from src.conductor.task_spec import TaskSpec, AnalysisType

skills = load_skills()
assert 'comparison-analysis' in skills
assert skills['comparison-analysis']['inject'] == 'analyze'

spec = TaskSpec(intent='对比小米SU7和极氪007的口碑', analysis_type=AnalysisType.VOC)
injected = skill_for_analysis(spec)
assert '对比分析做法' in injected
assert '对比维度' in injected
print('OK')
"
```
Expected: 打印 `OK`，无异常

- [ ] **Step 3: 校验编码 + 提交**

```bash
iconv -f utf-8 -t utf-8 skills/comparison-analysis.md > /dev/null
git add skills/comparison-analysis.md
git commit -m "feat: 新增对比分析技能 comparison-analysis"
```

---

## Task 6: 新增技能 trend-analysis.md

**Files:**
- Create: `skills/trend-analysis.md`

**Interfaces:**
- Consumes: Task 2 的 frontmatter 范式

- [ ] **Step 1: 创建文件**

```markdown
---
title: 趋势分析做法
inject: analyze
trigger:
  time_range_required: true
  intent_keywords: [趋势, 变化, 走势, 演变, 变动, 舆情变化, 热度变化]
---
# 技能：趋势分析

## 何时使用
用户要求了解某话题/产品在一段时间内的变化、演变或走势（任务已指定时间范围）。

## 输出结构（Markdown）
1. **时间分桶**：按数据实际跨度选择粒度（数天用天、数周用周、数月用月），
   桶数不宜过多或过少（3-8 桶为宜）。
2. **分桶归纳**：每个时间桶内先归纳主题/情绪，再跨桶对比增减变化。
3. **拐点标注**：明显的转折/突变点，标注对应时间段并给出该时段的代表性原文佐证。
4. **趋势小结**：结尾用一句话总结整体走势（上升/下降/平稳/波动），忠于数据不夸大。

## 注意
- 某些时间段样本稀疏时，如实标注"该时段样本不足，趋势判断置信度低"，
  不得用插值、推测或"合理推断"填补数据空白。
- 忠于原文，不杜撰；不确定的拐点归因需标注"推测"。
```

- [ ] **Step 2: 验证加载与触发（含 time_range_required 约束）**

```bash
python -c "
from src.memory import load_skills, skill_for_analysis
from src.conductor.task_spec import TaskSpec

skills = load_skills()
assert 'trend-analysis' in skills

spec_no_range = TaskSpec(intent='小米SU7舆情变化趋势', time_range=None)
assert '趋势分析做法' not in skill_for_analysis(spec_no_range)  # 没给时间范围不该触发

spec_with_range = TaskSpec(intent='小米SU7舆情变化趋势', time_range='最近30天')
injected = skill_for_analysis(spec_with_range)
assert '趋势分析做法' in injected
assert '时间分桶' in injected
print('OK')
"
```
Expected: 打印 `OK`，无异常

- [ ] **Step 3: 校验编码 + 提交**

```bash
iconv -f utf-8 -t utf-8 skills/trend-analysis.md > /dev/null
git add skills/trend-analysis.md
git commit -m "feat: 新增趋势分析技能 trend-analysis"
```

---

## Task 7: 新增技能 platform-selection.md

**Files:**
- Create: `skills/platform-selection.md`

**Interfaces:**
- Consumes: Task 2 的 frontmatter 范式（`inject: planner`）；Task 3 的 `skills_for_planner()` 恒注入逻辑

- [ ] **Step 1: 创建文件**

```markdown
---
title: 平台选型经验
inject: planner
trigger:
  always: true
---
# 技能：平台选型经验

## 何时使用
规划任务时，用户没有明确指定要去哪个平台采集，需要根据话题类型推荐合适的平台。

## 平台适配参考（仅限已有专用采集器的 10 个平台）
- **小红书**：美妆、穿搭、种草、生活方式、母婴、家居等消费类话题。
- **B站**：数码评测、游戏、动漫、长视频深度解说、UP主观点类内容。
- **知乎**：专业性问答、深度讨论、行业分析类话题。
- **贴吧**：垂直兴趣社区讨论（如球队吧、游戏吧等）。
- **微博**：热点事件、舆情、明星/社会话题的即时讨论。
- **抖音 / 快手**：短视频内容、大众化/下沉市场话题。
- **京东**：商品评论、购物体验、电商类话题。
- **YouTube**：海外/国际化内容。
- **V2EX**：程序员、技术、开发者社区话题。

## 注意
- 上述是当前有专用采集器支持的全部平台（共 10 个：抖音、小红书、微博、B站、快手、
  知乎、贴吧、京东、YouTube、V2EX），选型只在这个范围内推荐；一个话题可能适配
  多个平台时，优先选列表靠前者。
- **话题不在上述范围时**（如具体品牌官网、新闻站、招投标网、垂直行业站点），
  不要把它们塞进 platforms 字段——那不属于本表管辖范围，按你已知的域名信息
  处理即可（网站主域名填 site_domains，由现有的站点限定机制处理）。
- 用户已明确指定平台/网址时，不需要套用本表，直接按用户指定的来。
```

- [ ] **Step 2: 验证加载与恒触发**

```bash
python -c "
from src.memory import load_skills, skills_for_planner

skills = load_skills()
assert 'platform-selection' in skills
assert skills['platform-selection']['inject'] == 'planner'
assert skills['platform-selection']['trigger'].get('always') is True

injected = skills_for_planner()
assert '平台选型经验' in injected
assert '小红书' in injected
print('OK')
"
```
Expected: 打印 `OK`，无异常

- [ ] **Step 3: 校验编码 + 提交**

```bash
iconv -f utf-8 -t utf-8 skills/platform-selection.md > /dev/null
git add skills/platform-selection.md
git commit -m "feat: 新增平台选型经验技能 platform-selection（planner 恒注入）"
```

---

## Task 8: 重写 skills/README.md

**Files:**
- Modify: `skills/README.md`

**Interfaces:**
- Consumes: 无（纯文档，描述 Task 1-7 建立的范式）

- [ ] **Step 1: 整体替换 `skills/README.md`**

```markdown
# skills/ —— 技能资产

> loops Engineering 的"技能"层：把跨任务复用的"做法/经验"沉淀为声明式 Markdown，
> 按任务自动匹配注入对应节点的提示词，让循环复利、不每次从零解释。

## 范式：必须带 YAML frontmatter

```yaml
---
title: <技能标题>
inject: analyze | planner    # 必填：注入哪个节点
trigger:
  analysis_type: voc         # 可选，匹配 TaskSpec.analysis_type
  data_type: [comment, post] # 可选，列表内 OR，匹配 TaskSpec.data_type
  intent_keywords: [对比, 比较]  # 可选，列表内任一词命中 intent+keywords 即算命中
  time_range_required: true  # 可选，要求 TaskSpec.time_range 非空
  always: true                # 与其余键互斥独占：出现即恒命中（目前只有 planner 侧技能这么用）
---
<正文：做法描述，会被追加到对应节点的 system prompt>
```

各 trigger 键之间是 AND 关系。**`inject: planner` 目前只支持 `trigger.always: true`**——
planner 运行时 TaskSpec 还没生成，只有松散的 `understanding` 字典，不支持关键词式精确匹配。

由 `src/memory/loader.py` 的 `load_skills()` 加载、`skill_for_analysis()`/`skills_for_planner()`
按 frontmatter 声明的条件匹配注入。无 frontmatter（`README.md` 除外，按文件名排除）/
YAML 解析失败 / 解析成功但正文为空的文件会被跳过并打 `logger.info` 留痕。

## 技能准入门槛（新增技能前自检）

新增技能前确认同时满足：
1. 有明确的注入节点（`analyze` 或 `planner`）；
2. 有明确的触发条件（能写进 `trigger`）；
3. 内容是"怎么做好这类事"的经验，不是报告结构——报告结构属于模板的事
   （固定领域模板见 `src/conductor/prompts.py`，自学习模板见 `data/templates/`）。

**本目录只放真正会被注入的技能**，不放纯参考文档——那些请放 `docs/`
（如 MediaCrawler 采集策略/合规说明见 `docs/scrape-social-media.md`）。

## 当前技能清单

- [voc-analysis.md](../../../../skills/voc-analysis.md)：VOC 槽点分析（注入 analyze，`analysis_type=voc` 时触发）。
- [comparison-analysis.md](../../../../skills/comparison-analysis.md)：对比分析（注入 analyze，命中"对比/比较"等关键词时触发）。
- [trend-analysis.md](../../../../skills/trend-analysis.md)：趋势分析（注入 analyze，需 `time_range` 非空且命中"趋势/变化"等关键词）。
- [platform-selection.md](../../../../skills/platform-selection.md)：平台选型经验（注入 planner，恒触发）。
```

- [ ] **Step 2: 跑一遍全量回归确认无破坏**

```bash
python scripts/test_frontmatter.py
python scripts/test_memory.py
python scripts/test_planner.py
python scripts/test_template_learning.py
python scripts/test_embeddings.py
```
Expected: 全部 `PASS`/全绿

- [ ] **Step 3: 校验编码 + 提交**

```bash
iconv -f utf-8 -t utf-8 skills/README.md > /dev/null
git add skills/README.md
git commit -m "docs: 重写 skills/README.md，反映声明式范式与当前技能清单"
```

---

## Task 9: 文档同步（AGENTS.md / README_AGENT.md）+ 端到端冒烟

**Files:**
- Modify: `AGENTS.md`（"记忆/技能"一行 + 新增一条日期化条目）
- Modify: `README_AGENT.md`（新增一节 + 变更日志条目，参照既有"15.2"一节的格式）

**Interfaces:**
- Consumes: 无（纯文档同步 + 手工验证）

- [ ] **Step 1: 更新 `AGENTS.md`**

把现有这一行（在"记忆/技能"描述附近）：

```
- **记忆/技能**（`src/memory/`）：读 `memory/user-preferences.md` 注入意图提示词；按任务选 `skills/*.md` 注入分析提示词（VOC）；`data/templates/*.md` 为自学习沉淀的分析模板（见下方"自学习"）。
```

改为：

```
- **记忆/技能**（`src/memory/`）：读 `memory/user-preferences.md`（全局）+ `webui.db.user_memory`（个人）注入意图提示词；`skills/*.md` 按 frontmatter 声明的 `inject`/`trigger` 声明式匹配，`analyze` 侧按 TaskSpec 匹配、`planner` 侧目前只支持恒触发；`data/templates/*.md` 为自学习沉淀的分析模板（见下方"自学习"）。
```

在 AGENTS.md 里最新的日期化条目列表后面（紧跟本会话已经加过的"自学习模板迁出 skills/"那条之后）新增一条：

```
  - **技能体系声明式化 + 新增3个技能**（2026-07-09）：`skills/*.md` 从"禁止 frontmatter、
    硬编码选择逻辑"改为"必须 frontmatter、按 trigger 声明式匹配"。审查发现
    `scrape-social-media.md` 从未被任何生产代码注入过（`select_skills()` 是只在测试里
    调用的死代码），已归档到 `docs/`；`voc-analysis.md` 补 frontmatter 内容不变。
    新增 3 个技能：`comparison-analysis`（对比分析，注入 analyze，命中"对比/比较"等
    关键词触发）、`trend-analysis`（趋势分析，注入 analyze，需 `time_range` 非空且命中
    "趋势/变化"等关键词）、`platform-selection`（平台选型经验，**新注入点** planner，
    恒触发——把话题映射到 `KNOWN_PLATFORMS` 里真正有专用采集器的 10 个平台）。
    共用 `src/memory/_frontmatter.py`（新抽出）解析 frontmatter，`data/templates/`
    的 `load_templates()`/`record_template_use()` 一并改用该共享函数。
```

- [ ] **Step 2: 更新 `README_AGENT.md`**

在 README_AGENT.md 里"## 15.2 记忆两层化 + 模板库目录迁移"一节之后，新增一节
"## 15.3 技能体系声明式化（2026-07-09 新增）"，内容参照上面 AGENTS.md 条目展开成完整段落
（背景、frontmatter 范式、3 个新技能的价值与触发条件、平台选型的边界说明），
写作风格与前面 15/15.1/15.2 节保持一致（先说问题、再说方案、列出关键代码位置）。
并在"对照 plan 仍未做"之前的变更日志区新增一条 changelog block，引用"详见第 15.3 节"，
格式对照 15.2 节对应的 changelog block。

- [ ] **Step 3: 校验编码**

```bash
iconv -f utf-8 -t utf-8 AGENTS.md > /dev/null
iconv -f utf-8 -t utf-8 README_AGENT.md > /dev/null
```

- [ ] **Step 4: 端到端冒烟（需要重启后端进程生效，见项目约定）**

停掉当前跑着的后端进程，重新启动（`E:/python3.13/python.exe -m src.api.main`），
然后在对话工作区分别发起：
1. "对比一下A产品和B产品的用户评价" → 观察节点白盒卡片，analyze 阶段应能看到
   分析结果体现"对比维度/对比表格"结构（技能已注入的间接证据）。
2. "最近一个月某话题的舆情变化趋势"（给出真实存在的话题）→ 分析结果应体现
   "按时间分桶"的组织方式。
3. 不指定平台的口碑类任务（如"看看大家对某数码新品的评价"）→ 观察规划节点
   White-box 卡片里 `plan_reasoning`，看规划出的 platforms 是否合理（不应出现
   未支持平台被塞进 platforms 字段的情况）。
4. VOC 回归：任意一个"分析XX口碑"的既有任务类型，确认行为与改造前一致。

- [ ] **Step 5: 提交**

```bash
git add AGENTS.md README_AGENT.md
git commit -m "docs: 同步技能体系声明式化改动到 AGENTS.md / README_AGENT.md"
```
