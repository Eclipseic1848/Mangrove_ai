# -*- coding: utf-8 -*-
"""验证三个候选共用的 Docker 沙箱基线，不向 Agent 暴露宿主 Shell。"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess
import uuid

from common import PROTOTYPE_ROOT


IMAGE = "python:3.13-slim-bookworm"


def main() -> int:
    run_dir = (
        PROTOTYPE_ROOT
        / "runs"
        / f"sandbox-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    )
    input_dir = run_dir / "input"
    output_dir = run_dir / "output"
    input_dir.mkdir(parents=True)
    output_dir.mkdir()
    source = input_dir / "source.txt"
    source.write_text("immutable-source", encoding="utf-8")
    container_name = f"mangrove-stage1-sandbox-{uuid.uuid4().hex[:8]}"
    script = """
import json
from pathlib import Path
import socket

result = {"source": Path("/input/source.txt").read_text(encoding="utf-8")}
try:
    Path("/input/source.txt").write_text("mutated", encoding="utf-8")
    result["input_read_only"] = False
except Exception:
    result["input_read_only"] = True
sock = socket.socket()
sock.settimeout(1)
try:
    sock.connect(("1.1.1.1", 53))
    result["network_blocked"] = False
except Exception:
    result["network_blocked"] = True
finally:
    sock.close()
result["host_path_absent"] = not Path("/host").exists()
Path("/output/result.json").write_text(
    json.dumps(result, sort_keys=True),
    encoding="utf-8",
)
"""
    # 这些约束共同构成任务级执行边界：断网、只读根目录、最小权限和硬资源上限。
    # 输入与输出分卷挂载，保证 Agent 可以产出候选，但不能修改原始生产资料。
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--network",
        "none",
        "--read-only",
        "--cpus",
        "0.5",
        "--memory",
        "128m",
        "--pids-limit",
        "64",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "65532:65532",
        "--mount",
        f"type=bind,src={input_dir},dst=/input,readonly",
        "--mount",
        f"type=bind,src={output_dir},dst=/output",
        IMAGE,
        "python",
        "-c",
        script,
    ]
    completed = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
        check=False,
    )
    result_path = output_dir / "result.json"
    sandbox_result = (
        json.loads(result_path.read_text(encoding="utf-8"))
        if result_path.is_file()
        else {}
    )
    image = subprocess.run(
        ["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"],
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=10,
        check=False,
    )
    passed = (
        completed.returncode == 0
        and source.read_text(encoding="utf-8") == "immutable-source"
        and sandbox_result.get("source") == "immutable-source"
        and sandbox_result.get("input_read_only") is True
        and sandbox_result.get("network_blocked") is True
        and sandbox_result.get("host_path_absent") is True
    )
    evidence = {
        "passed": passed,
        "image": IMAGE,
        "image_id": image.stdout.strip(),
        "container_exit_code": completed.returncode,
        "sandbox_result": sandbox_result,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "run_dir": str(run_dir),
    }
    (run_dir / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
