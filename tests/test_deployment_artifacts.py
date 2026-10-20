# -*- coding: utf-8 -*-
"""候选清单与旧前端资源保留的离线回归。"""
from __future__ import annotations

from pathlib import Path
import os
import subprocess
import json
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

def _dist(tmp_path: Path, name: str, files: dict[str, bytes]) -> Path:
    root = tmp_path / name
    root.mkdir()
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return root


def test_retaining_old_assets_keeps_lazy_chunks_without_replacing_new_index(tmp_path: Path):
    from src.api.main import FrontendAssets

    previous = _dist(tmp_path, "previous", {
        "index.html": b"old index",
        "assets/old-chunk.js": b"old lazy chunk",
        "assets/pdf.worker-old.mjs": b"old PDF worker",
        "assets/nested/font.woff2": b"old font",
    })
    candidate = _dist(tmp_path, "candidate", {
        "index.html": b"new index", "assets/new-chunk.js": b"new lazy chunk",
    })
    app = FastAPI()
    app.mount("/assets", FrontendAssets(directory=candidate))
    client = TestClient(app)
    # 新构建本身确实缺少旧页面的延迟资源；不能依靠 SPA 首页冒充资源成功。
    assert client.get("/assets/old-chunk.js").status_code == 404
    assert client.get("/assets/pdf.worker-old.mjs").status_code == 404

    from src.services.deployment_artifacts import frontend_manifest, retain_frontend_assets, verify_manifest

    native = frontend_manifest(candidate)
    served = retain_frontend_assets(
        candidate, previous, native_manifest=native,
        previous_commit="a" * 40, previous_source_digest="b" * 64, previous_dirty=True,
    )

    assert client.get("/assets/old-chunk.js").content == b"old lazy chunk"
    assert client.get("/assets/pdf.worker-old.mjs").content == b"old PDF worker"
    assert client.get("/assets/nested/font.woff2").content == b"old font"
    assert client.get("/assets/new-chunk.js").content == b"new lazy chunk"
    assert (candidate / "index.html").read_bytes() == b"new index"
    assert (previous / "index.html").read_bytes() == b"old index"
    assert "assets/old-chunk.js" not in native["files"]
    assert served["native_content_sha256"] == native["content_sha256"]
    assert served["provenance"] == {
        "previous_commit": "a" * 40,
        "previous_source_digest": "b" * 64,
        "previous_dirty": True,
    }
    assert served["retained_assets"] == [
        "assets/nested/font.woff2", "assets/old-chunk.js", "assets/pdf.worker-old.mjs",
    ]
    verify_manifest(candidate, served)


def test_old_asset_conflict_is_rejected_before_copying_any_file(tmp_path: Path):
    from src.services.deployment_artifacts import DeploymentArtifactError, frontend_manifest, retain_frontend_assets

    previous = _dist(tmp_path, "previous", {"assets/a-missing.js": b"keep", "assets/z-shared.js": b"old"})
    candidate = _dist(tmp_path, "candidate", {"index.html": b"new", "assets/z-shared.js": b"new"})
    native = frontend_manifest(candidate)

    with pytest.raises(DeploymentArtifactError, match="同名异内容"):
        retain_frontend_assets(
            candidate, previous, native_manifest=native,
            previous_commit="a" * 40, previous_source_digest="b" * 64, previous_dirty=True,
        )

    assert not (candidate / "assets/a-missing.js").exists()
    assert (candidate / "assets/z-shared.js").read_bytes() == b"new"


def test_source_manifest_uses_only_exact_tracked_paths_and_checks_actual_bytes(tmp_path: Path):
    from src.services.deployment_artifacts import DeploymentArtifactError, source_manifest, verify_manifest

    source = _dist(tmp_path, "source", {
        "src/app.py": b"print('candidate')\n", ".env.example": b"API_KEY=\n",
        "docker/rsshub/.env.example": b"PORT=1200\n",
        "data/lessons/README.md": "说明\n".encode("utf-8"),
        "data/templates/README.md": b"templates\n", "memory/README.md": b"memory\n",
        ".env": b"PRIVATE=not-for-manifest", "data/private.db": b"private data",
        ".git/config": b"local", ".venv/bin/python": b"local interpreter",
    })
    tracked = ["src/app.py", ".env.example", "docker/rsshub/.env.example", "data/lessons/README.md", "data/templates/README.md", "memory/README.md"]
    manifest = source_manifest(source, tracked, commit_sha="c" * 40)

    assert sorted(manifest["files"]) == sorted(tracked)
    assert source_manifest(source, reversed(tracked), commit_sha="c" * 40) == manifest
    assert manifest["commit_sha"] == "c" * 40
    assert manifest["files"]["src/app.py"]["sha256"] == "fbf786686eccc3f356b46aa39e66aaa0aeb9f216e16c14fd316006aa01c6cb73"
    verify_manifest(source, manifest)
    (source / "src/app.py").write_bytes(b"changed")
    with pytest.raises(DeploymentArtifactError, match="不一致"):
        verify_manifest(source, manifest)


@pytest.mark.parametrize("path", [".env", ".env.local", "data/private.db", ".git/config", ".venv/bin/python", "frontend/dist/index.html"])
def test_source_manifest_rejects_protected_tracked_paths_instead_of_silently_omitting_them(tmp_path: Path, path: str):
    from src.services.deployment_artifacts import DeploymentArtifactError, source_manifest

    source = _dist(tmp_path, "source", {"src/app.py": b"public", path: b"private"})
    with pytest.raises(DeploymentArtifactError, match="受管数据或本机配置"):
        source_manifest(source, ["src/app.py", path], commit_sha="c" * 40)


@pytest.mark.parametrize("path", ["../outside.py", "/outside.py", "src/../app.py", "src//app.py", "src\\app.py", "C:/app.py"])
def test_source_manifest_rejects_path_escape_and_noncanonical_paths(tmp_path: Path, path: str):
    from src.services.deployment_artifacts import DeploymentArtifactError, source_manifest

    source = _dist(tmp_path, "source", {"src/app.py": b"public"})
    with pytest.raises(DeploymentArtifactError, match="相对路径"):
        source_manifest(source, [path], commit_sha="c" * 40)


def test_frontend_served_manifest_is_replayable_but_never_becomes_native(tmp_path: Path):
    from src.services.deployment_artifacts import DeploymentArtifactError, frontend_manifest, retain_frontend_assets, verify_manifest

    previous = _dist(tmp_path, "previous", {"assets/old.mjs": b"keep", "assets/shared.js": b"same"})
    candidate = _dist(tmp_path, "candidate", {"index.html": b"new", "assets/shared.js": b"same"})
    native = frontend_manifest(candidate)
    arguments = dict(native_manifest=native, previous_commit="a" * 40, previous_source_digest="b" * 64, previous_dirty=True)
    served = retain_frontend_assets(candidate, previous, **arguments)

    assert retain_frontend_assets(candidate, previous, **arguments) == served
    with pytest.raises(DeploymentArtifactError, match="不一致"):
        verify_manifest(candidate, native)
    with pytest.raises(DeploymentArtifactError, match="原生构建"):
        retain_frontend_assets(candidate, previous, **{**arguments, "native_manifest": served})
    (candidate / "assets/injected.js").write_bytes(b"unexpected")
    with pytest.raises(DeploymentArtifactError, match="之外"):
        retain_frontend_assets(candidate, previous, **arguments)


def test_source_manifest_rejects_parent_directory_link_even_inside_root(tmp_path: Path):
    from src.services.deployment_artifacts import DeploymentArtifactError, source_manifest

    source = _dist(tmp_path, "source", {"real/app.py": b"public"})
    link = source / "linked"
    try:
        link.symlink_to(source / "real", target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        # 与既有静态资源越界回归一致，无提权环境使用目录联接验证真实类型。
        quoted_link = "'" + str(link).replace("'", "''") + "'"
        quoted_target = "'" + str(source / "real").replace("'", "''") + "'"
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             f"New-Item -ItemType Junction -Path {quoted_link} -Target {quoted_target} | Out-Null"],
            check=True, capture_output=True, encoding="utf-8",
        )

    with pytest.raises(DeploymentArtifactError, match="目录链接"):
        source_manifest(source, ["linked/app.py"], commit_sha="c" * 40)


def test_asset_directory_collision_is_rejected_before_copying_any_file(tmp_path: Path):
    from src.services.deployment_artifacts import DeploymentArtifactError, frontend_manifest, retain_frontend_assets

    previous = _dist(tmp_path, "previous", {"assets/a-first.js": b"old", "assets/z-directory.js": b"old"})
    candidate = _dist(tmp_path, "candidate", {"index.html": b"new"})
    (candidate / "assets/z-directory.js").mkdir(parents=True)
    native = frontend_manifest(candidate)

    with pytest.raises(DeploymentArtifactError, match="目录冲突"):
        retain_frontend_assets(
            candidate, previous, native_manifest=native,
            previous_commit="a" * 40, previous_source_digest="b" * 64, previous_dirty=True,
        )
    assert not (candidate / "assets/a-first.js").exists()


def test_file_link_cannot_escape_dist_and_keeps_type_when_retained(tmp_path: Path):
    from src.services.deployment_artifacts import DeploymentArtifactError, frontend_manifest, retain_frontend_assets

    previous = _dist(tmp_path, "previous", {"assets/real.mjs": b"worker"})
    candidate = _dist(tmp_path, "candidate", {"index.html": b"new"})
    link = previous / "assets/linked.mjs"
    try:
        link.symlink_to("real.mjs")
    except OSError as error:
        pytest.skip(f"当前环境不允许文件符号链接：{error}")
    old = frontend_manifest(previous)
    assert old["files"]["assets/linked.mjs"]["type"] == "symlink"
    assert old["files"]["assets/linked.mjs"]["target"] == "assets/real.mjs"
    served = retain_frontend_assets(
        candidate, previous, native_manifest=frontend_manifest(candidate),
        previous_commit="a" * 40, previous_source_digest="b" * 64, previous_dirty=True,
    )
    assert served["files"]["assets/linked.mjs"]["type"] == "file"
    assert served["retained_from_previous"]["assets/linked.mjs"] == old["files"]["assets/linked.mjs"]
    assert (candidate / "assets/linked.mjs").read_bytes() == b"worker"
    link.unlink()
    (tmp_path / "outside.mjs").write_bytes(b"private")
    link.symlink_to(tmp_path / "outside.mjs")
    with pytest.raises(DeploymentArtifactError, match="越界"):
        frontend_manifest(previous)


def test_source_file_link_cannot_disguise_secret_as_source(tmp_path: Path):
    from src.services.deployment_artifacts import DeploymentArtifactError, source_manifest

    source = _dist(tmp_path, "source", {".env": b"private", "src/app.py": b"public"})
    try:
        (source / "src/linked.py").symlink_to("../.env")
    except OSError as error:
        pytest.skip(f"当前环境不允许文件符号链接：{error}")
    with pytest.raises(DeploymentArtifactError, match="受管数据或本机配置"):
        source_manifest(source, ["src/linked.py"], commit_sha="c" * 40)


def test_artifact_cli_generates_and_verifies_explicit_utf8_manifests(tmp_path: Path):
    source = _dist(tmp_path, "source", {"src/中文.py": "print('候选')\n".encode("utf-8"), ".env": b"private"})
    tracked = tmp_path / "tracked.nul"
    tracked.write_bytes("src/中文.py\0".encode("utf-8"))
    source_output = tmp_path / "source.json"
    repository = Path(__file__).resolve().parents[1]
    arguments = [sys.executable, "-m", "src.services.deployment_artifacts"]

    result = subprocess.run(
        [*arguments, "source", "--root", str(source), "--tracked-paths", str(tracked), "--commit", "c" * 40, "--output", str(source_output)],
        cwd=repository, capture_output=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads(source_output.read_text(encoding="utf-8"))
    assert list(manifest["files"]) == ["src/中文.py"]
    verified = subprocess.run(
        [*arguments, "verify", "--root", str(source), "--manifest", str(source_output)],
        cwd=repository, capture_output=True, encoding="utf-8", check=False,
    )
    assert verified.returncode == 0, verified.stderr
    (source / "src/中文.py").write_bytes(b"changed")
    rejected = subprocess.run(
        [*arguments, "verify", "--root", str(source), "--manifest", str(source_output)],
        cwd=repository, capture_output=True, encoding="utf-8", check=False,
    )
    assert rejected.returncode == 1
    assert json.loads(rejected.stdout)["ok"] is False


def test_artifact_cli_retains_native_and_served_manifests_separately(tmp_path: Path):
    previous = _dist(tmp_path, "previous", {"index.html": b"old", "assets/worker-old.mjs": b"old worker"})
    candidate = _dist(tmp_path, "candidate", {"index.html": b"new", "assets/app-new.js": b"new app"})
    repository = Path(__file__).resolve().parents[1]
    command = [sys.executable, "-m", "src.services.deployment_artifacts"]
    native_file, served_file = tmp_path / "native.json", tmp_path / "served.json"
    native_result = subprocess.run(
        [*command, "native", "--root", str(candidate), "--output", str(native_file)],
        cwd=repository, capture_output=True, encoding="utf-8", check=False,
    )
    assert native_result.returncode == 0, native_result.stdout + native_result.stderr
    retain_result = subprocess.run(
        [*command, "retain", "--candidate-dist", str(candidate), "--previous-dist", str(previous),
         "--native-manifest", str(native_file), "--previous-commit", "a" * 40,
         "--previous-source-digest", "b" * 64, "--previous-dirty", "true", "--output", str(served_file)],
        cwd=repository, capture_output=True, encoding="utf-8", check=False,
    )
    assert retain_result.returncode == 0, retain_result.stdout + retain_result.stderr
    native = json.loads(native_file.read_text(encoding="utf-8"))
    served = json.loads(served_file.read_text(encoding="utf-8"))
    assert native["kind"] == "frontend-native"
    assert served["kind"] == "frontend-served"
    assert set(native["files"]) == {"index.html", "assets/app-new.js"}
    assert set(served["files"]) == {"index.html", "assets/app-new.js", "assets/worker-old.mjs"}
    assert served["native_content_sha256"] == native["content_sha256"]
    assert served["provenance"]["previous_dirty"] is True
    assert (candidate / "index.html").read_bytes() == b"new"
    verified = subprocess.run(
        [*command, "verify", "--root", str(candidate), "--manifest", str(served_file)],
        cwd=repository, capture_output=True, encoding="utf-8", check=False,
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr


@pytest.mark.parametrize("failure", ["missing-nul", "existing-output", "output-inside-root"])
def test_artifact_cli_rejects_invalid_path_file_or_output_without_overwriting_evidence(tmp_path: Path, failure: str):
    source = _dist(tmp_path, "source", {"src/app.py": b"public"})
    tracked = tmp_path / "tracked.nul"
    tracked.write_bytes(b"src/app.py" if failure == "missing-nul" else b"src/app.py\0")
    output = source / "manifest.json" if failure == "output-inside-root" else tmp_path / "manifest.json"
    if failure == "existing-output":
        output.write_text("existing evidence", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "src.services.deployment_artifacts", "source", "--root", str(source),
         "--tracked-paths", str(tracked), "--commit", "c" * 40, "--output", str(output)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["ok"] is False
    if failure == "existing-output":
        assert output.read_text(encoding="utf-8") == "existing evidence"
    else:
        assert not output.exists()
