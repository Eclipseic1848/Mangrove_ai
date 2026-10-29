"""只运行隔离入口和合成采集器，不启动浏览器或访问来源。"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ISOLATOR = Path(__file__).resolve().parents[1] / "scripts" / "mediacrawler_isolated.py"


@pytest.mark.parametrize("platform,cookie,original,expected", [
    ("xhs", "web_session=synthetic-only", False, True),
    ("xhs", "web_session=synthetic-only", True, True),
    ("wb", "synthetic-only", False, False),
    ("xhs", "", False, False),
])
def test_cookie_mode_does_not_need_display(tmp_path, platform, cookie, original, expected):
    (tmp_path / "config.py").write_text(
        f"HEADLESS = {original!r}\nSAVE_LOGIN_STATE = True\nPLATFORM = {platform!r}\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text(
        "import config, json, os\n"
        "assert config.SAVE_LOGIN_STATE is False\n"
        "assert config.ENABLE_CDP_MODE is False\n"
        "assert 'DISPLAY' not in os.environ\n"
        "print(json.dumps({'headless': config.HEADLESS}))\n",
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in {"DISPLAY", "MANGROVE_XHS_BROWSER_PROFILE", "PYTHONPATH"}}
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", str(ISOLATOR), "default", "--platform", platform],
        input=json.dumps(cookie).encode("utf-8"), cwd=tmp_path, env=env,
        capture_output=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert json.loads(result.stdout)["headless"] is expected
