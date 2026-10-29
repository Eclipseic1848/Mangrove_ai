"""锁定的登录来源必须与实际启动来源一致，全部使用临时目录和进程替身。"""
import asyncio
import json

from filelock import FileLock, Timeout
import pytest

from src.collectors import social_media_collector as collector
from src.config.settings import settings
from src.conductor.task_spec import TaskSpec


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    first = tmp_path / "first" / "xhs_user_data_dir"
    second = tmp_path / "second" / "xhs_user_data_dir"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    for key, value in {
        "webui_db_path": str(tmp_path / "webui.db"),
        "mediacrawler_path": str(tmp_path),
        "mc_xhs_user_data_dir": str(first),
        "mc_enable_ip_proxy": False,
        "collect_timeout_mediacrawler_seconds": 2,
    }.items():
        monkeypatch.setattr(settings, key, value)
    return first, second


def lock_available(profile):
    lock = FileLock(profile / ".mangrove-collector.lock", thread_local=False)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return False
    else:
        lock.release()
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_mode,changed_mode", [
    ("browser", "browser"),
    ("browser", "cookie"),
    ("cookie", "browser"),
])
async def test_configuration_change_cannot_switch_locked_login_source(
    profiles, monkeypatch, initial_mode, changed_mode
):
    first, second = profiles
    monkeypatch.setattr(settings, "mc_xhs_login_mode", initial_mode)
    cookie = "synthetic_session=one" if initial_mode == "cookie" else ""
    monkeypatch.setattr(collector, "_platform_cookie", lambda platform: cookie)
    original_collect = collector.SocialMediaCollector._collect
    observed = {}

    async def change_after_lock(self, spec, run_dir, selected_cookie, **kwargs):
        monkeypatch.setattr(settings, "mc_xhs_user_data_dir", str(second))
        monkeypatch.setattr(settings, "mc_xhs_login_mode", changed_mode)
        return await original_collect(self, spec, run_dir, selected_cookie, **kwargs)

    class Process:
        returncode = 1

        async def communicate(self, *, input):
            observed["cookie"] = json.loads(input)

    async def launch(*args, **kwargs):
        observed.update(
            profile=kwargs["env"].get("MANGROVE_XHS_BROWSER_PROFILE", ""),
            adapter=args[2],
            first_locked=not lock_available(first),
            second_locked=not lock_available(second),
        )
        return Process()

    monkeypatch.setattr(collector.SocialMediaCollector, "_collect", change_after_lock)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    await collector.SocialMediaCollector().collect(TaskSpec(
        intent="合成登录来源冻结回归", platforms=["xhs"], keywords=["synthetic"], max_items=1
    ))

    assert observed, "配置改变后仍应沿本次原选定来源执行"
    assert observed["profile"] == (str(first) if initial_mode == "browser" else "")
    assert observed["adapter"] == ("xhs-browser" if initial_mode == "browser" else "xhs")
    assert observed["cookie"] == cookie
    assert observed["first_locked"] is (initial_mode == "browser")
    assert observed["second_locked"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_failed_collection_does_not_leak_profile_into_later_private_call(
    profiles, monkeypatch, failure
):
    first, second = profiles
    monkeypatch.setattr(settings, "mc_xhs_login_mode", "browser")
    monkeypatch.setattr(collector, "_platform_cookie", lambda platform: "")
    original_collect = collector.SocialMediaCollector._collect
    observed = {}

    async def fail(self, spec, run_dir, cookie, **kwargs):
        monkeypatch.setattr(settings, "mc_xhs_user_data_dir", str(second))
        raise failure("合成中断")

    class Process:
        returncode = 1

        async def communicate(self, *, input):
            pass

    async def launch(*args, **kwargs):
        observed["profile"] = kwargs["env"].get("MANGROVE_XHS_BROWSER_PROFILE", "")
        return Process()

    monkeypatch.setattr(collector.SocialMediaCollector, "_collect", fail)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    spec = TaskSpec(intent="合成中断清理回归", platforms=["xhs"], keywords=["synthetic"])
    instance = collector.SocialMediaCollector()
    with pytest.raises(failure):
        await instance.collect(spec)
    assert lock_available(first)
    await original_collect(instance, spec, second.parent, "")
    assert observed["profile"] == str(second)
