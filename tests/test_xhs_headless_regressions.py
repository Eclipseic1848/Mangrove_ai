"""只运行隔离入口和合成采集器，不启动浏览器或访问来源。"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ISOLATOR = Path(__file__).resolve().parents[1] / "scripts" / "mediacrawler_isolated.py"


@pytest.mark.parametrize("display", [None, "obsolete-desktop.invalid:0"])
@pytest.mark.parametrize("platform,cookie,original,expected", [
    ("xhs", "web_session=synthetic-only", False, True),
    ("xhs", "web_session=synthetic-only", True, True),
    ("wb", "synthetic-only", False, False),
    ("xhs", "", False, False),
])
def test_cookie_mode_does_not_need_display(tmp_path, platform, cookie, original, expected, display):
    (tmp_path / "config.py").write_text(
        f"HEADLESS = {original!r}\nSAVE_LOGIN_STATE = True\nPLATFORM = {platform!r}\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text(
        "import config, json, os\n"
        "assert config.SAVE_LOGIN_STATE is False\n"
        "assert config.ENABLE_CDP_MODE is False\n"
        "print(json.dumps({'headless': config.HEADLESS, 'display': os.environ.get('DISPLAY')}))\n",
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in {"DISPLAY", "MANGROVE_XHS_BROWSER_PROFILE", "PYTHONPATH"}}
    if display is not None:
        env["DISPLAY"] = display
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", str(ISOLATOR), "default", "--platform", platform],
        input=json.dumps(cookie).encode("utf-8"), cwd=tmp_path, env=env,
        capture_output=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert json.loads(result.stdout) == {
        "headless": expected,
        "display": None if platform == "xhs" and cookie else display,
    }


@pytest.mark.parametrize("adapter", ["xhs", "identity", "xhs-browser"])
def test_xhs_display_boundary_at_each_isolated_entry(tmp_path, monkeypatch, adapter):
    import io
    from types import SimpleNamespace
    from scripts import mediacrawler_isolated as isolated

    profile = tmp_path / "xhs_user_data_dir"
    profile.mkdir()
    desktop = adapter == "xhs-browser"
    config = SimpleNamespace(HEADLESS=False)
    monkeypatch.setenv("DISPLAY", "obsolete-desktop.invalid:0")
    monkeypatch.setenv("MANGROVE_XHS_BROWSER_PROFILE", str(profile))
    monkeypatch.setitem(sys.modules, "config", config)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", [str(ISOLATOR), adapter, "--platform", "xhs", "--save_data_path", str(tmp_path)])
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps("" if desktop else "synthetic-only").encode("utf-8"))))
    reached = []

    def reached_browser_entry(*args, **kwargs):
        reached.append(True)
        assert config.HEADLESS is (not desktop)
        assert os.environ.get("DISPLAY") == ("obsolete-desktop.invalid:0" if desktop else None)
        assert config.SAVE_LOGIN_STATE is desktop

    def install_probe(*args):
        reached_browser_entry()
        return False

    monkeypatch.setitem(sys.modules, "mediacrawler_identity", SimpleNamespace(install_probe=install_probe))
    monkeypatch.setattr(isolated.runpy, "run_path", reached_browser_entry)
    isolated.main()
    assert reached == [True]
