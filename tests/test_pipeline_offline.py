# -*- coding: utf-8 -*-
"""数据准备管线离线 e2e 测试（plan Phase 1 退出标准）。

预置 RawArtifact（模拟 collector 输出），跑 parse -> profile -> clean -> validate -> output
全链路，验证：
- 产物完整：manifest/quality/schema/lineage/clean/rejects 全部生成
- 账本守恒：raw = parsed + rejects_parse；parsed = clean + rejects_clean + merged
- 血缘覆盖：每条干净记录可追溯到 artifact_id（plan 15.3：100%）
- 不进 analyze（6B：data_prep 主链路无分析节点）
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.cleaning.rules.web_rules import _denoise  # noqa: E402
from src.data_prep.artifact_store import ArtifactStore  # noqa: E402
from src.data_prep.graph import (  # noqa: E402
    acquire_node,
    clean_node,
    output_node,
    parse_node,
    profile_node,
    validate_node,
)
from src.data_prep.models import (  # noqa: E402
    DataPrepTaskSpec,
    OutputFormat,
    QualityPolicy,
    Recipe,
    SourceLimits,
    SourceSpec,
    SourceType,
)


def _make_raw_artifacts(store: ArtifactStore, task_id: str) -> list:
    """预置 11 条原始制品：8 正常 + 1 样板噪声 + 1 验证页 + 1 重复。"""
    good = [
        {"url": f"http://a.com/{i}", "title": f"真实文章{i}",
         "content": f"第{i}篇足够长的真实正文内容，详述产品参数与用户体验，内容详实。"}
        for i in range(1, 9)
    ]
    rejects = [
        {"url": "http://a.com/boiler", "title": "导航", "content": "首页 登录 注册"},                   # 样板噪声
        {"url": "http://a.com/captcha", "title": "验证", "content": "请完成安全验证后继续访问，拖动滑块。"},  # 验证页
        {"url": "http://a.com/1", "title": "重复", "content": good[0]["content"]},                      # 重复（同 good[0]）
    ]
    items = good + rejects
    artifacts = []
    for it in items:
        payload = json.dumps(it, ensure_ascii=False).encode("utf-8")
        art = store.write_raw(
            task_id=task_id, source_id="web-1", data=payload,
            uri=it["url"], media_type="application/json", ext="json",
        )
        artifacts.append(art)
    return artifacts


def _build_spec() -> DataPrepTaskSpec:
    return DataPrepTaskSpec(
        intent="离线管线测试",
        sources=[SourceSpec(
            source_id="web-1", source_type=SourceType.WEB, locator="http://a.com",
            limits=SourceLimits(max_records=20),
        )],
        cleaning_recipe=Recipe(),
        quality_policy=QualityPolicy(max_reject_rate=0.4, warn_reject_rate=0.1),
        outputs=[OutputFormat.JSONL, OutputFormat.CSV],
    )


async def _run_pipeline(task_id: str, spec: DataPrepTaskSpec) -> dict:
    """跑 parse -> profile -> clean -> validate -> output（跳过 acquire，预置 artifacts）。"""
    store = ArtifactStore()
    artifacts = _make_raw_artifacts(store, task_id)
    state = {
        "task_id": task_id, "spec": spec, "artifacts": artifacts,
        "record_counts": {"raw": len(artifacts)},
    }
    state.update(await parse_node(state))
    state.update(await profile_node(state))
    state.update(await clean_node(state))
    state.update(await validate_node(state))
    state.update(await output_node(state))
    return state


def test_pipeline_offline():
    task_id = f"test_offline_{uuid.uuid4().hex[:8]}"
    try:
        state = asyncio.run(_run_pipeline(task_id, _build_spec()))

        # 1. 产物完整
        task_dir = Path("downloads") / task_id
        assert (task_dir / "manifest.json").exists(), "manifest.json 未生成"
        assert (task_dir / "quality_report.json").exists(), "quality_report.json 未生成"
        assert (task_dir / "schema.json").exists(), "schema.json 未生成"
        assert (task_dir / "trace.json").exists(), "trace.json 未生成"
        assert (task_dir / "recipe.json").exists(), "recipe.json 未生成"
        assert (task_dir / "lineage" / "records.jsonl").exists(), "lineage 未生成"
        assert (task_dir / "clean" / "data.jsonl").exists(), "clean/data.jsonl 未生成"
        assert (task_dir / "clean" / "data.csv").exists(), "clean/data.csv 未生成"

        # 2. 账本守恒
        counts = state["record_counts"]
        assert counts["raw"] == 11, f"raw 应 11，实际 {counts['raw']}"
        assert counts["parsed"] == 11, f"parsed 应 11，实际 {counts['parsed']}"
        assert counts["rejects_parse"] == 0
        # clean=8（8 正常保留；1 样板 + 1 验证页 + 1 重复被隔离）
        assert counts["clean"] == 8, f"clean 应 8，实际 {counts['clean']}"
        assert counts["rejects_clean"] == 3, f"rejects_clean 应 3，实际 {counts['rejects_clean']}"
        # 守恒：parsed = clean + rejects_clean + merged
        assert counts["parsed"] == counts["clean"] + counts["rejects_clean"] + counts.get("merged", 0), \
            f"清洗账本不守恒: {counts}"

        # 3. 血缘覆盖 100%
        lineage = (task_dir / "lineage" / "records.jsonl").read_text(encoding="utf-8").strip().split("\n")
        lineage_records = [json.loads(l) for l in lineage if l]
        assert len(lineage_records) == counts["clean"]
        for rec in lineage_records:
            assert rec.get("artifact_id"), f"记录 {rec.get('record_id')} 缺少 artifact_id，血缘不完整"

        # 4. 隔离原因正确
        store = ArtifactStore()
        clean_rejects = store.read_jsonl(task_id, "rejects/clean_rejects.jsonl")
        reasons = {}
        for r in clean_rejects:
            reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
        assert reasons.get("样板噪声") == 1, f"样板噪声应 1，实际 {reasons}"
        assert reasons.get("验证页") == 1, f"验证页应 1，实际 {reasons}"
        assert reasons.get("重复") == 1, f"重复应 1，实际 {reasons}"

        # 5. 质量结论非 fail（有隔离但未超阈值）
        quality = state["quality"]
        assert quality.overall.value != "fail", f"质量不应 fail: {quality.issues}"

        # 6. Manifest 无凭证泄露
        manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest_str = json.dumps(manifest, ensure_ascii=False)
        assert "cookie" not in manifest_str.lower() or "····" in manifest_str, "Manifest 不应含明文凭证"

        # 7. 未进 analyze（6B：产物目录无 report.md / analysis）
        assert not (task_dir / "report.md").exists(), "data_prep 模式不应生成 report.md"
        assert not (task_dir / "analysis").exists()

        print(f"  状态: {state['status']}")
        print(f"  账本: {counts}")
        print(f"  隔离原因: {reasons}")
        print(f"  质量结论: {quality.overall.value}")
        print(f"  产物文件: {len(state['outputs'])} 个")
    finally:
        # 清理测试目录
        task_dir = Path("downloads") / task_id
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)


def test_acquire_no_source_fails():
    """无数据源时 acquire 应返回 error（plan 5.3 第 4 步：确定性完整性检查）。"""
    spec = DataPrepTaskSpec(intent="空任务", sources=[])
    state = {"task_id": "test_empty", "spec": spec}
    result = asyncio.run(acquire_node(state))
    assert result.get("error"), "无数据源应报错"
    assert result.get("status") == "FAILED"


TESTS = [test_pipeline_offline, test_acquire_no_source_fails]


def main():
    failed = 0
    for t in TESTS:
        try:
            t(); print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1; import traceback; traceback.print_exc()
            print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50); print(f"{len(TESTS) - failed}/{len(TESTS)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
