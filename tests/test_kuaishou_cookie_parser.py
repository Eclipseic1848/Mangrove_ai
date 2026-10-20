"""独立入口保留快手 Cookie 值，不访问平台或真实凭据。"""
import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run_crawler(tmp_path, cookie, expected, *, platform="ks", adapter="default"):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "__init__.py").write_text("", encoding="utf-8")
    (tools / "crawler_util.py").write_text('''
def convert_str_cookie_to_dict(cookie):
    result = {}
    for field in cookie.split(";"):
        parts = field.strip().split("=")
        if len(parts) == 2:
            result[parts[0].strip()] = parts[1].strip()
    return result
''', encoding="utf-8")
    (tools / "utils.py").write_text("from .crawler_util import convert_str_cookie_to_dict\n", encoding="utf-8")
    (tmp_path / "config.py").write_text("PLATFORM = 'xhs'\n", encoding="utf-8")
    core = tmp_path / "media_platform" / "kuaishou"
    core.mkdir(parents=True)
    (core / "core.py").write_text('''
import config
from tools import utils
from tools.crawler_util import convert_str_cookie_to_dict
from_export = utils.convert_str_cookie_to_dict(config.COOKIES)
from_original = convert_str_cookie_to_dict(config.COOKIES)
''', encoding="utf-8")
    (tmp_path / "mediacrawler_identity.py").write_text(f'''
def install_probe(platform, output):
    from media_platform.kuaishou import core
    assert core.from_export == {expected!r} and core.from_original == {expected!r}
    return True
''', encoding="utf-8")
    (tmp_path / "main.py").write_text(f'''
import config
from tools import crawler_util, utils
from media_platform.kuaishou import core
expected = {expected!r}
assert utils.convert_str_cookie_to_dict(config.COOKIES) == expected
assert crawler_util.convert_str_cookie_to_dict(config.COOKIES) == expected
assert core.from_export == expected and core.from_original == expected
assert not config.SAVE_LOGIN_STATE and not config.ENABLE_CDP_MODE
print("COOKIE_MAPPING_OK")
''', encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts/mediacrawler_isolated.py"
    return subprocess.run([sys.executable, "-X", "utf8", str(script), adapter,
                           "--platform", platform, "--save_data_path", str(tmp_path)],
                          cwd=tmp_path, input=json.dumps(cookie).encode("utf-8"),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)


@pytest.mark.parametrize("adapter", ["default", "identity"])
def test_kuaishou_collection_preserves_cookie_equals(tmp_path, adapter):
    completed = _run_crawler(tmp_path, "session=synthetic-value==; token=prefix=middle=last",
                             {"session": "synthetic-value==", "token": "prefix=middle=last"}, adapter=adapter)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    assert completed.stdout.strip() == b"COOKIE_MAPPING_OK"
    assert b"synthetic-value" not in completed.stdout + completed.stderr


@pytest.mark.parametrize("cookie,expected", [
    ("session=first==rest; empty=; session=last===", {"session": "last===", "empty": ""}),
    (" ; ; ", {}),
    ("", {}),
    ("ignored; session=synthetic==; another-flag", {"session": "synthetic=="}),
])
def test_kuaishou_preserves_duplicate_and_empty_value_semantics(tmp_path, cookie, expected):
    completed = _run_crawler(tmp_path, cookie, expected)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    assert completed.stdout.strip() == b"COOKIE_MAPPING_OK"


@pytest.mark.parametrize("cookie", ["=synthetic-secret==", "bad name=synthetic-secret=="])
def test_kuaishou_rejects_invalid_cookie_names_without_exposing_values(tmp_path, cookie):
    completed = _run_crawler(tmp_path, cookie, {})
    assert completed.returncode == 1
    assert b"MANGROVE_MC_EXCEPTION_TYPE:ValueError" in completed.stderr
    assert b"synthetic-secret" not in completed.stdout + completed.stderr
    assert b"COOKIE_MAPPING_OK" not in completed.stdout


@pytest.mark.parametrize("platform", ["xhs", "dy", "wb", "bili", "zhihu", "tieba"])
def test_other_six_platforms_keep_the_original_cookie_parser(tmp_path, platform):
    completed = _run_crawler(tmp_path, "session=synthetic-value==; original=unchanged",
                             {"original": "unchanged"}, platform=platform)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    assert completed.stdout.strip() == b"COOKIE_MAPPING_OK"


def test_empty_kuaishou_identity_does_not_enter_crawler(tmp_path):
    completed = _run_crawler(tmp_path, "", {}, adapter="identity")
    assert completed.returncode == 1
    assert b"MANGROVE_MC_EXCEPTION_TYPE:ValueError" in completed.stderr
    assert b"COOKIE_MAPPING_OK" not in completed.stdout
