"""在临时 Git 仓库验证专用增量补丁；不联网或修改真实第三方工作副本。"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="安装器使用 Windows PowerShell 路径")
SETUP = Path(__file__).resolve().parents[1] / "scripts" / "setup_external_dependencies.ps1"
CLIENT = "media_platform/xhs/client.py"
HELPER = "media_platform/xhs/session_cookies.py"


def sha(value):
    return hashlib.sha256(value).hexdigest()


def git(repository, *args):
    result = subprocess.run(["git", "-C", str(repository), *args], capture_output=True, check=True)
    return result.stdout


def test_frozen_patch_survives_windows_checkout(tmp_path):
    workspace = SETUP.parents[1]
    relative = Path("external/patches/mediacrawler-cookie-refresh.patch")
    manifest = json.loads((workspace / relative.with_suffix(".manifest.json")).read_text(encoding="utf-8"))
    patch = (workspace / relative).read_bytes()
    assert sha(patch) == manifest["patch_sha256"]
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "core.autocrlf", "true")
    (tmp_path / ".gitattributes").write_bytes((workspace / ".gitattributes").read_bytes())
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(patch)
    git(tmp_path, "add", "--", ".gitattributes", relative.as_posix())
    # 强制重新检出，验证打包原件不被 Windows 换行转换破坏。
    target.unlink()
    git(tmp_path, "checkout-index", "--", relative.as_posix())
    assert sha(target.read_bytes()) == manifest["patch_sha256"]


@pytest.fixture
def overlay(tmp_path, request):
    variant = getattr(request, "param", None)
    shell = shutil.which("powershell.exe") if variant == "frozen-ps51" else shutil.which("pwsh") or shutil.which("powershell.exe")
    if not shell or not shutil.which("git"):
        pytest.skip("需要现有 PowerShell 和 Git")
    source = tmp_path / "synthetic-source"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.name", "synthetic-fixture")
    git(source, "config", "user.email", "synthetic@example.invalid")
    git(source, "config", "core.autocrlf", "false")
    client_before = b"# synthetic\nVALUE = 1\n"
    client_after = b"# synthetic\nVALUE = 1\nSESSION = True\n"
    helper_after = b"# synthetic helper\nENABLED = True\n"
    frozen = variant in {"frozen", "frozen-ps51"}
    if frozen:
        workspace = SETUP.parents[1]
        artifacts = workspace / "external" / "patches"
        manifest_path = artifacts / "mediacrawler-cookie-refresh.manifest.json"
        if not manifest_path.is_file():
            pytest.skip("实际增量补丁尚未冻结")
        real_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        repository = workspace / "external" / "MediaCrawler" / "repo"
        client_before = git(repository, "show", "07a11337a5e3cfc5544e155803e67bb79b33a1bd:" + CLIENT)
        client_after = (repository / CLIENT).read_bytes().replace(b"\r\n", b"\n")
        helper_after = (repository / HELPER).read_bytes().replace(b"\r\n", b"\n")
        assert [sha(client_before), sha(client_after), sha(helper_after)] == [
            real_manifest["files"][0]["before_sha256"], real_manifest["files"][0]["after_sha256"],
            real_manifest["files"][1]["after_sha256"]]
    (source / CLIENT).parent.mkdir(parents=True)
    (source / CLIENT).write_bytes(client_before)
    (source / "base.txt").write_bytes(b"original\n")
    git(source, "add", "--", CLIENT, "base.txt")
    git(source, "commit", "-qm", "synthetic baseline")
    baseline = git(source, "rev-parse", "HEAD").decode().strip()
    (source / "base.txt").write_bytes(b"prepared\n")
    base_patch = git(source, "diff", "--binary")
    git(source, "add", "--", "base.txt")
    git(source, "commit", "-qm", "synthetic prepared")
    prepared = git(source, "rev-parse", "HEAD").decode().strip()

    root = tmp_path / "project with spaces"
    target = root / "external" / "MediaCrawler" / "repo"
    target.parent.mkdir(parents=True)
    subprocess.run(["git", "-c", "core.autocrlf=false", "clone", "-q", "--no-hardlinks", str(source), str(target)], check=True, capture_output=True)
    git(target, "config", "core.autocrlf", "false")
    patches = root / "external" / "patches"
    patches.mkdir()
    (patches / "mediacrawler-mangrove.patch").write_bytes(base_patch)
    patch = "diff --git a/{0} b/{0}\n".format(CLIENT)
    patch += "".join(difflib.unified_diff(client_before.decode().splitlines(True), client_after.decode().splitlines(True),
                                        fromfile="a/" + CLIENT, tofile="b/" + CLIENT))
    patch += "diff --git a/{0} b/{0}\nnew file mode 100644\n".format(HELPER)
    patch += "".join(difflib.unified_diff([], helper_after.decode().splitlines(True),
                                        fromfile="/dev/null", tofile="b/" + HELPER))
    patch = patch.encode("utf-8")
    if frozen:
        patch = (artifacts / "mediacrawler-cookie-refresh.patch").read_bytes()
        assert sha(patch) == real_manifest["patch_sha256"]
    (patches / "mediacrawler-cookie-refresh.patch").write_bytes(patch)

    def raw_hashes(value):
        return [sha(value), sha(value.replace(b"\n", b"\r\n"))]

    manifest = {"schema": "mangrove-xhs-cookie-refresh-v1", "line_endings": "preserve", "patch_sha256": sha(patch),
                "files": [{"path": CLIENT, "before_sha256": sha(client_before), "after_sha256": sha(client_after),
                           "before_raw_sha256": raw_hashes(client_before), "after_raw_sha256": raw_hashes(client_after)},
                          {"path": HELPER, "before_sha256": None, "after_sha256": sha(helper_after),
                           "before_raw_sha256": None, "after_raw_sha256": raw_hashes(helper_after)}]}
    if frozen:
        manifest = real_manifest
    manifest_path = patches / "mediacrawler-cookie-refresh.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    harness = tmp_path / "invoke-installer-functions.ps1"
    harness.write_text(r'''
param($Setup, $Root, $Url, $Revision, $Prepared)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$root = $Root
$tokens = $null
$errors = $null
$code = [IO.File]::ReadAllText($Setup, [Text.Encoding]::UTF8)
$ast = [Management.Automation.Language.Parser]::ParseInput($code, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw "安装器语法错误" }
foreach ($node in $ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
    Invoke-Expression $node.Extent.Text
}
# 只跳过已有本地提交的 fetch；其余检查及 git apply 使用真实 Git。
function Invoke-Git {
    param([string[]]$Arguments)
    if ($Arguments -contains "clone") { throw "合成门禁止克隆或联网" }
    if ($Arguments -contains "fetch") { return }
    & git @Arguments
    if ($LASTEXITCODE -ne 0) { throw "合成 Git 操作失败" }
}
try {
    Install-ExternalComponent -Name "MediaCrawler" -Url $Url -Revision $Revision -PreparedRevision $Prepared `
        -RelativeTarget "external\MediaCrawler\repo" -RelativePatch "external\patches\mediacrawler-mangrove.patch"
    @{ok=$true} | ConvertTo-Json -Compress
} catch {
    @{ok=$false; error=$_.Exception.Message} | ConvertTo-Json -Compress
    exit 1
}
''', encoding="utf-8-sig")

    def run():
        environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
        if variant == "frozen-ps51":
            # 从 Python 启动旧宿主时，不能继承 PowerShell 7 的模块搜索路径。
            environment = {key: value for key, value in environment.items() if key.upper() != "PSMODULEPATH"}
        result = subprocess.run([shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness),
                                 "-Setup", str(SETUP), "-Root", str(root), "-Url", str(source),
                                 "-Revision", baseline, "-Prepared", prepared], capture_output=True,
                                encoding="utf-8", errors="replace", env=environment)
        lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
        assert lines, result.stdout + result.stderr
        data = json.loads(lines[-1])
        assert result.returncode == (0 if data["ok"] else 1), result.stderr
        return data

    def snapshot():
        # 只记录合成工作树、HEAD 和 index，拒绝场景不得写入或暂存任何内容。
        files = {str(path.relative_to(target)): path.read_bytes() for path in target.rglob("*") if path.is_file() and ".git" not in path.relative_to(target).parts}
        return files, git(target, "rev-parse", "HEAD"), (target / ".git" / "index").read_bytes()

    return {"root": root, "target": target, "run": run, "snapshot": snapshot, "baseline": baseline,
            "manifest": manifest, "manifest_path": manifest_path, "before": client_before, "after": client_after,
            "helper": helper_after, "base_patch": patches / "mediacrawler-mangrove.patch"}


@pytest.mark.parametrize("state", ["prepared", "baseline", "baseline_patched"])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_overlay_installs_and_repeats_without_changing_head_or_index(overlay, state, newline):
    # 此处旧补丁仅为抽象分支夹具，不代表实际 c9 全量补丁已完成安装验收。
    assert_install_and_repeat(overlay, state, newline)


def assert_install_and_repeat(overlay, state, newline):
    target = overlay["target"]
    if state != "prepared":
        git(target, "checkout", "--detach", overlay["baseline"])
        if state == "baseline_patched":
            git(target, "apply", str(overlay["base_patch"]))
    (target / CLIENT).write_bytes(overlay["before"].replace(b"\n", newline))
    before_head = git(target, "rev-parse", "HEAD")
    before_index = (target / ".git" / "index").read_bytes()
    assert (result := overlay["run"]())["ok"], result
    assert (target / CLIENT).read_bytes() == overlay["after"].replace(b"\n", newline)
    assert (target / HELPER).read_bytes() == overlay["helper"].replace(b"\n", newline)
    assert (target / "base.txt").read_bytes() == b"prepared\n"
    installed = overlay["snapshot"]()
    assert (result := overlay["run"]())["ok"], result
    assert overlay["snapshot"]() == installed
    assert git(target, "rev-parse", "HEAD") == before_head
    # 干净基线的旧安装步骤原本会 checkout；增量应用与幂等重跑不得暂存新文件。
    if state != "baseline":
        assert (target / ".git" / "index").read_bytes() == before_index
    assert not git(target, "diff", "--cached", "--name-only")


@pytest.mark.parametrize("overlay", ["frozen", "frozen-ps51"], indirect=True)
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_frozen_overlay_prepared_installs_repeats_and_refuses_tampering(overlay, newline):
    assert_install_and_repeat(overlay, "prepared", newline)
    target = overlay["target"]
    (target / CLIENT).write_bytes((target / CLIENT).read_bytes() + b"# unknown change" + newline)
    before = overlay["snapshot"]()
    assert not overlay["run"]()["ok"]
    assert overlay["snapshot"]() == before


@pytest.mark.parametrize("state", ["prepared", "baseline_patched"])
@pytest.mark.parametrize("mutation", ["client", "helper", "partial", "unknown_tracked", "unknown_untracked", "staged_overlay", "staged_unknown", "patch", "manifest_path", "patch_scope", "mixed_newlines"])
def test_overlay_refuses_unknown_changes_without_overwriting(overlay, mutation, state):
    target = overlay["target"]
    if state == "baseline_patched":
        git(target, "checkout", "--detach", overlay["baseline"])
        git(target, "apply", str(overlay["base_patch"]))
    if mutation not in {"partial", "mixed_newlines", "patch", "manifest_path", "patch_scope"}:
        assert (result := overlay["run"]())["ok"], result
    if mutation == "client":
        (target / CLIENT).write_bytes(b"unknown\n")
    elif mutation == "helper":
        (target / HELPER).write_bytes(b"unknown\n")
    elif mutation == "partial":
        (target / CLIENT).write_bytes(overlay["after"])
    elif mutation == "unknown_tracked":
        (target / "base.txt").write_bytes(b"unknown\n")
    elif mutation == "unknown_untracked":
        (target / "other.py").write_bytes(b"unknown\n")
    elif mutation == "staged_overlay":
        git(target, "add", "--", CLIENT, HELPER)
    elif mutation == "staged_unknown":
        (target / "base.txt").write_bytes(b"unknown\n")
        git(target, "add", "--", "base.txt")
    elif mutation == "patch":
        patch = overlay["manifest_path"].with_name("mediacrawler-cookie-refresh.patch")
        patch.write_bytes(patch.read_bytes() + b"\n")
    elif mutation == "manifest_path":
        value = overlay["manifest"]
        value["files"][1]["path"] = "../outside.py"
        overlay["manifest_path"].write_text(json.dumps(value), encoding="utf-8")
    elif mutation == "patch_scope":
        patch = overlay["manifest_path"].with_name("mediacrawler-cookie-refresh.patch")
        extra = b"diff --git a/other.py b/other.py\nnew file mode 100644\n--- /dev/null\n+++ b/other.py\n@@ -0,0 +1 @@\n+unknown\n"
        patch.write_bytes(patch.read_bytes() + extra)
        value = overlay["manifest"]
        value["patch_sha256"] = sha(patch.read_bytes())
        overlay["manifest_path"].write_text(json.dumps(value), encoding="utf-8")
    else:
        (target / CLIENT).write_bytes(b"# synthetic\r\nVALUE = 1\n")
    before = overlay["snapshot"]()
    assert not overlay["run"]()["ok"]
    assert overlay["snapshot"]() == before
