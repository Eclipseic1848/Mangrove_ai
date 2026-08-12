#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""记忆模块并发安全与缓存单元测试（方案 A）。

运行：python scripts/test_memory_concurrency.py
按 TDD 循环逐步构建，每个测试先红后绿。
"""
import sys
import tempfile
import threading
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.memory._io import atomic_write, MtimeCache
from src.memory._frontmatter import parse_frontmatter


def test_atomic_write_reads_back_correctly():
    """原子写：写入后能正确读回，内容完整。"""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "test.md"
        content = "---\ntitle: 测试\n---\n正文内容\n"
        atomic_write(path, content)
        result = path.read_text(encoding="utf-8")
        assert result == content, f"读回内容不一致: {result!r}"
        # 验证 frontmatter 可解析（无半截损坏）
        parsed = parse_frontmatter(result)
        assert parsed is not None
        meta, body = parsed
        assert meta["title"] == "测试"
        assert body == "正文内容"


def test_atomic_write_replaces_atomically():
    """原子写：覆盖写后旧内容完全被替换，无残留。"""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "test.md"
        atomic_write(path, "---\ntitle: 旧\n---\n旧正文\n")
        atomic_write(path, "---\ntitle: 新\n---\n新正文\n")
        result = path.read_text(encoding="utf-8")
        parsed = parse_frontmatter(result)
        assert parsed[0]["title"] == "新"
        assert parsed[1] == "新正文"


def test_atomic_write_creates_parent_dirs():
    """原子写：父目录不存在时自动创建。"""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "sub" / "deep" / "test.md"
        atomic_write(path, "内容")
        assert path.read_text(encoding="utf-8") == "内容"


# ---- MtimeCache ----

def test_cache_miss_on_first_get():
    """首次 get 返回 None（缓存未命中）。"""
    with tempfile.TemporaryDirectory() as d:
        dir_path = Path(d)
        cache = MtimeCache()
        assert cache.get(dir_path) is None


def test_cache_hit_after_set():
    """set 后 get 返回相同值。"""
    with tempfile.TemporaryDirectory() as d:
        dir_path = Path(d)
        cache = MtimeCache()
        cache.set(dir_path, ["a", "b"])
        assert cache.get(dir_path) == ["a", "b"]


def test_cache_invalidate_clears():
    """invalidate 后 get 返回 None。"""
    with tempfile.TemporaryDirectory() as d:
        dir_path = Path(d)
        cache = MtimeCache()
        cache.set(dir_path, ["x"])
        cache.invalidate()
        assert cache.get(dir_path) is None


def test_cache_miss_after_mtime_change():
    """目录 mtime 变化后缓存失效。

    不能用 os.utime(path, None)（设为"当前时间"）触发变化——Windows NTFS 目录 mtime
    分辨率较粗，紧邻的两次系统调用有约 20% 概率落入同一时间粒度、时间戳完全不变，
    导致本用例偶发 flaky（实测 200 次里 44 次 mtime 未变化）。改用显式指定一个
    确定性偏移的时间戳（当前时间 +1 秒），确保新旧 mtime 一定不同，消除对系统
    时钟精度的依赖。"""
    import os as _os
    import time as _time
    with tempfile.TemporaryDirectory() as d:
        dir_path = Path(d)
        cache = MtimeCache()
        cache.set(dir_path, ["old"])
        old_mtime = dir_path.stat().st_mtime
        # 显式设为比原 mtime 晚 1 秒的确定性时间戳，保证一定变化
        new_ts = old_mtime + 1
        _os.utime(dir_path, (new_ts, new_ts))
        assert dir_path.stat().st_mtime_ns != cache._key[1], "测试前置条件：mtime 必须确实变化"
        assert cache.get(dir_path) is None


def test_cache_single_slot_overwrites():
    """单槽缓存：set 新目录覆盖旧值。"""
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        cache = MtimeCache()
        cache.set(Path(d1), ["one"])
        cache.set(Path(d2), ["two"])
        # 单槽被覆盖，旧目录的 get 返回 None
        assert cache.get(Path(d1)) is None
        assert cache.get(Path(d2)) == ["two"]


# ---- 并发安全：教训库 ----

def _setup_lesson_tmp():
    import src.memory.lessons as lesson
    d = Path(tempfile.mkdtemp(prefix="mg_concur_lesson_"))
    lesson.LESSONS_DIR = d
    # 清缓存（改造后生效）
    if hasattr(lesson, "_lessons_cache"):
        lesson._lessons_cache.invalidate()
    return d


def _write_lesson_file(d, slug, title, data_type, keywords, body, status="active", occurrences=1):
    front = yaml.safe_dump(
        {"title": title, "data_type": data_type, "keywords": keywords,
         "status": status, "occurrences": occurrences},
        allow_unicode=True, sort_keys=False,
    ).strip()
    (d / f"{slug}.md").write_text(f"---\n{front}\n---\n{body}\n", encoding="utf-8")


def test_record_failure_concurrent_no_lost_occurrences():
    import src.memory.lessons as lesson
    from src.config.settings import settings

    d = _setup_lesson_tmp()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False  # 走 Jaccard 确定性兜底

    N = 5  # 并发线程数
    payload = {"title": "测试教训", "keywords": ["测试", "并发"], "body": "应对建议正文"}

    async def _fake_achat(messages, **kwargs):
        return json.dumps(payload, ensure_ascii=False)

    errors = []

    def _run():
        try:
            asyncio.run(
                lesson.record_failure(
                    "测试失败任务", "comment", ["测试", "并发"],
                    "未采集到有效数据",
                )
            )
        except Exception as e:
            errors.append(e)

    try:
        with patch("src.memory.lessons.achat", new=_fake_achat):
            threads = [threading.Thread(target=_run) for _ in range(N)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert not errors, f"并发错误: {errors}"
        lessons = lesson.load_lessons()
        assert len(lessons) == 1, f"期望 1 条教训，实际 {len(lessons)} 条"
        t = lessons[0]
        assert t["occurrences"] == N, f"期望 occurrences={N}，实际 {t['occurrences']}"
    finally:
        settings.embedding_enabled = old_enabled


# ---- 并发安全：模板库 ----

def _setup_tpl_tmp():
    import src.memory.templates as tpl
    d = Path(tempfile.mkdtemp(prefix="mg_concur_tpl_"))
    tpl.TEMPLATES_DIR = d
    if hasattr(tpl, "_templates_cache"):
        tpl._templates_cache.invalidate()
    if hasattr(tpl, "_vectors_cache"):
        tpl._vectors_cache.invalidate()
    return d


def _write_tpl_file(d, slug, title, data_type, keywords, body, status="active", uses=0, quality_avg=0):
    front = yaml.safe_dump(
        {"title": title, "data_type": data_type, "keywords": keywords,
         "status": status, "uses": uses, "quality_avg": quality_avg},
        allow_unicode=True, sort_keys=False,
    ).strip()
    (d / f"{slug}.md").write_text(f"---\n{front}\n---\n{body}\n", encoding="utf-8")


def test_record_template_use_concurrent_no_lost_uses():
    """并发 record_template_use 不丢 uses 累加。"""
    import src.memory.templates as tpl

    d = _setup_tpl_tmp()
    _write_tpl_file(d, "existing", "测试模板", "comment", ["测试"], "正文", status="draft", uses=0, quality_avg=0)

    N = 10
    errors = []

    def _run():
        try:
            tpl.record_template_use("existing", quality_score=80)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_run) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发错误: {errors}"
    loaded = tpl.load_templates()
    assert len(loaded) == 1
    assert loaded[0]["uses"] == N, f"期望 uses={N}，实际 {loaded[0]['uses']}"


def main():
    tests = [
        test_atomic_write_reads_back_correctly,
        test_atomic_write_replaces_atomically,
        test_atomic_write_creates_parent_dirs,
        test_cache_miss_on_first_get,
        test_cache_hit_after_set,
        test_cache_invalidate_clears,
        test_cache_miss_after_mtime_change,
        test_cache_single_slot_overwrites,
        test_record_failure_concurrent_no_lost_occurrences,
        test_record_template_use_concurrent_no_lost_uses,
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