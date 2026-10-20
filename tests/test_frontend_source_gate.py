"""前端源码上线门：通过命令行拒绝遗漏现役修复的构建输入。"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_frontend_source.py"


def run_gate(*args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")


def write_json(path, value):
    write(path, json.dumps(value, ensure_ascii=False))


@pytest.fixture
def frontend(tmp_path):
    root = tmp_path / "frontend"
    for name, content in {
        "index.html": "<div id='root'></div>\n",
        "package.json": '{"scripts":{"build":"vite build"}}\n',
        "package-lock.json": '{"lockfileVersion":3}\n',
        "tsconfig.json": '{"include":["src"]}\n',
        "vite.config.ts": "export default {};\n",
        "src/lib/api.ts": "export const safeSession = true;\n",
    }.items():
        write(root / name, content)
    return root


def take_snapshot(frontend, path):
    result = run_gate("snapshot", frontend, "--output", path)
    assert result.returncode == 0, result.stderr
    return json.loads(path.read_text(encoding="utf-8"))


def test_cli_rejects_unplanned_current_http_fix_regression(frontend, tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline = take_snapshot(frontend, baseline_path)
    assert baseline["src/lib/api.ts"] == hashlib.sha256(
        b"export const safeSession = true;\n"
    ).hexdigest()
    planned = tmp_path / "planned.json"
    write_json(planned, {})
    assert run_gate("check", baseline_path, frontend, planned).returncode == 0

    write(frontend / "src/lib/api.ts", "export const safeSession = false;\n")
    result = run_gate("check", baseline_path, frontend, planned)
    assert result.returncode == 1
    assert "src/lib/api.ts" in result.stderr


def test_snapshot_covers_build_inputs_normalizes_text_and_preserves_asset_bytes(frontend, tmp_path):
    for name in (
        "src/components/OperationsActivity.tsx", "public/logo.svg", ".env.production",
        "postcss.config.js", "tailwind.config.ts", "tsconfig.node.json",
    ):
        write(frontend / name, "第一行\r\n第二行\r\n")
    (frontend / "public/logo.png").write_bytes(b"\x89PNG\r\n\xff")
    write(frontend / "dist/index.html", "排除构建产物")
    write(frontend / "node_modules/dependency.js", "排除依赖")
    write(frontend / "tsconfig.tsbuildinfo", "排除构建缓存")
    result = run_gate("snapshot", frontend)
    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    assert set(manifest) == {
        "index.html", "package.json", "package-lock.json", "tsconfig.json", "vite.config.ts",
        "src/lib/api.ts", "src/components/OperationsActivity.tsx", "public/logo.svg",
        "public/logo.png", ".env.production", "postcss.config.js", "tailwind.config.ts",
        "tsconfig.node.json",
    }
    assert manifest["src/components/OperationsActivity.tsx"] == hashlib.sha256(
        "第一行\n第二行\n".encode("utf-8")
    ).hexdigest()
    assert manifest["public/logo.svg"] == hashlib.sha256(
        "第一行\r\n第二行\r\n".encode("utf-8")
    ).hexdigest()
    assert manifest["public/logo.png"] == hashlib.sha256(b"\x89PNG\r\n\xff").hexdigest()


def test_exact_plan_accepts_only_authorized_modification_addition_and_deletion(frontend, tmp_path):
    write(frontend / "src/old.ts", "旧文件\n")
    baseline_path = tmp_path / "baseline.json"
    baseline = take_snapshot(frontend, baseline_path)
    (frontend / "src/old.ts").unlink()
    write(frontend / "src/new.ts", "新文件\n")
    write(frontend / "src/lib/api.ts", "保留现役兼容修复\n")
    current = take_snapshot(frontend, tmp_path / "current.json")
    planned = {
        "src/old.ts": {"before": baseline["src/old.ts"], "after": None},
        "src/new.ts": {"before": None, "after": current["src/new.ts"]},
        "src/lib/api.ts": {"before": baseline["src/lib/api.ts"], "after": current["src/lib/api.ts"]},
    }
    planned_path = tmp_path / "planned.json"
    write_json(planned_path, planned)
    result = run_gate("check", baseline_path, frontend, planned_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["planned_changes"] == 3
    for field in ("before", "after"):
        wrong = {**planned, "src/lib/api.ts": {**planned["src/lib/api.ts"], field: "0" * 64}}
        write_json(planned_path, wrong)
        result = run_gate("check", baseline_path, frontend, planned_path)
        assert result.returncode == 1
        assert "src/lib/api.ts" in result.stderr


@pytest.mark.parametrize("name", [
    "src/pages/Feedback.tsx", "src/pages/Operations.tsx",
    "src/pages/settings/ConnectionEditor.tsx", "src/pages/settings/CapabilityGovernancePanel.tsx",
    "src/components/OperationsActivity.tsx", "src/components/PlatformConfigCenter.tsx",
])
def test_unlisted_existing_compatibility_fix_cannot_be_overwritten(frontend, tmp_path, name):
    write(frontend / name, "现役兼容能力\n")
    baseline_path = tmp_path / "baseline.json"
    take_snapshot(frontend, baseline_path)
    write(frontend / name, "遗漏兼容能力的本地版本\n")
    planned_path = tmp_path / "planned.json"
    write_json(planned_path, {})
    result = run_gate("check", baseline_path, frontend, planned_path)
    assert result.returncode == 1
    assert name in result.stderr


@pytest.mark.parametrize("operation", ["add", "delete"])
def test_unplanned_new_or_missing_source_is_rejected(frontend, tmp_path, operation):
    baseline_path = tmp_path / "baseline.json"
    take_snapshot(frontend, baseline_path)
    if operation == "add":
        write(frontend / "src/unplanned.ts", "新文件\n")
    else:
        (frontend / "src/lib/api.ts").unlink()
    planned = tmp_path / "planned.json"
    write_json(planned, {})
    assert run_gate("check", baseline_path, frontend, planned).returncode == 1


@pytest.mark.parametrize("name", [
    "../outside.ts", "/outside.ts", "C:/outside.ts", "src/../outside.ts", "src//api.ts",
    "src/./api.ts", "src\\api.ts", "dist/index.js", "node_modules/package.json", "",
    ".env.production/hidden.ts", "src/api?.ts",
])
def test_illegal_manifest_paths_are_rejected(frontend, tmp_path, name):
    baseline_path = tmp_path / "baseline.json"
    baseline = take_snapshot(frontend, baseline_path)
    planned = tmp_path / "planned.json"
    write_json(planned, {name: {"before": None, "after": "0" * 64}})
    result = run_gate("check", baseline_path, frontend, planned)
    assert result.returncode == 1
    assert "非法构建输入路径" in result.stderr
    write_json(baseline_path, {**baseline, name: "0" * 64})
    write_json(planned, {})
    assert run_gate("check", baseline_path, frontend, planned).returncode == 1


@pytest.mark.parametrize("change", [
    {"before": None, "after": None}, {"before": "0" * 64, "after": "0" * 64},
    {"before": "bad", "after": None}, {"before": None, "after": 123},
    {"after": "0" * 64}, {"before": None, "after": "0" * 64, "extra": True},
])
def test_malformed_or_noop_plans_are_rejected(frontend, tmp_path, change):
    baseline_path = tmp_path / "baseline.json"
    take_snapshot(frontend, baseline_path)
    planned = tmp_path / "planned.json"
    write_json(planned, {"src/lib/api.ts": change})
    assert run_gate("check", baseline_path, frontend, planned).returncode == 1


def test_duplicate_keys_and_invalid_utf8_do_not_silently_pass(frontend, tmp_path):
    baseline = tmp_path / "baseline.json"
    take_snapshot(frontend, baseline)
    planned = tmp_path / "planned.json"
    write(planned, '{"src/lib/api.ts":null,"src/lib/api.ts":null}')
    result = run_gate("check", baseline, frontend, planned)
    assert result.returncode == 1
    assert "重复键" in result.stderr
    (frontend / "src/lib/api.ts").write_bytes(b"\xff")
    assert run_gate("snapshot", frontend).returncode == 1


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_source_links_outside_root_are_rejected(frontend, tmp_path, kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    write(outside / "secret.ts", "不读取根目录外内容\n")
    try:
        (frontend / "src/link").symlink_to(
            outside if kind == "directory" else outside / "secret.ts",
            target_is_directory=kind == "directory",
        )
    except OSError as error:
        pytest.skip(f"当前宿主不允许创建测试链接：{error}")
    result = run_gate("snapshot", frontend)
    assert result.returncode == 1
    assert "禁止链接或越界" in result.stderr
