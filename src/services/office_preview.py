"""Office 原件隔离转换，只返回内存中的 PDF，不持久化副本或修改原件。"""
from pathlib import Path
import shutil
import subprocess
import threading
import uuid


_SLOTS = threading.BoundedSemaphore(2)
_CONVERT = '''
import pathlib, subprocess, sys
profile = pathlib.Path('/tmp/office-profile/user')
profile.mkdir(parents=True)
profile.joinpath('registrymodifications.xcu').write_text('<oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item></oor:items>', encoding='utf-8')
subprocess.run(['libreoffice', '-env:UserInstallation=file:///tmp/office-profile', '--headless', '--convert-to', 'pdf', '--outdir', '/tmp', sys.argv[1]], check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
result = pathlib.Path('/tmp/input.pdf')
if result.stat().st_size > 64 * 1024 * 1024:
    raise ValueError('preview too large')
sys.stdout.buffer.write(result.read_bytes())
'''


def office_preview(path: Path, extension: str) -> bytes:
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("文件超过 20 MB Office 在线预览上限")
    if not shutil.which("docker"):
        raise RuntimeError("Office 隔离预览服务未安装")
    if not _SLOTS.acquire(blocking=False):
        raise RuntimeError("预览服务忙，请稍后重试")
    name = "mangrove-preview-" + uuid.uuid4().hex
    try:
        # 只挂载这一份原件，断网、去权限、只读根目录；临时产物受内存盘上限约束。
        process = subprocess.run([
            "docker", "run", "--rm", "--name", name, "--network=none", "--read-only",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=768m", "--cpus=1", "--pids-limit=128",
            "--tmpfs", "/tmp:rw,size=256m,mode=1777", "--mount", f"type=bind,source={path.resolve()},target=/input{extension},readonly",
            "--entrypoint", "python", "mangrove/office-preview:local", "-c", _CONVERT, f"/input{extension}",
        ], capture_output=True, timeout=85)
        if process.returncode or not process.stdout.startswith(b"%PDF-"):
            raise RuntimeError("Office 预览转换失败，请检查文件是否加密或服务是否可用")
        return process.stdout
    except subprocess.TimeoutExpired:
        raise RuntimeError("Office 预览转换超时，请下载原件查看")
    finally:
        # Docker 客户端超时不代表容器已停；只清理本次随机命名的预览容器。
        try:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
        _SLOTS.release()
