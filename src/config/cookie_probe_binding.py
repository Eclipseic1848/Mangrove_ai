"""绑定并冻结身份探针实际使用的环境；原值仅在内存中传递。"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import platform
import sys

_PROBE_ENVIRONMENT = ContextVar("cookie_probe_environment", default=None)


def capture_probe_environment():
    from src.config.settings import settings
    from src.config.secret_refs import RUNTIME_CONFIG_SECRET_KEYS
    from src.collectors.social_media_collector import _proxy_env

    excluded = {key.upper() for key in RUNTIME_CONFIG_SECRET_KEYS if "cookie" in key} | {"COOKIES"}
    environment = {key: value for key, value in os.environ.items() if key.upper() not in excluded}
    environment.update(_proxy_env())
    return {
        "node": platform.node(), "platform": platform.platform(),
        "python": settings.mediacrawler_python or sys.executable,
        "collector_path": str(Path(settings.mediacrawler_path).expanduser().resolve()) if settings.mediacrawler_path else "",
        "environment": environment,
        "timeout": settings.collect_timeout_mediacrawler_seconds,
        "sleep_min": settings.mc_crawl_min_sleep_sec,
        "sleep_max": settings.mc_crawl_max_sleep_sec,
    }


@contextmanager
def probe_environment_context(environment):
    token = _PROBE_ENVIRONMENT.set(environment)
    try:
        yield
    finally:
        _PROBE_ENVIRONMENT.reset(token)


def current_probe_environment():
    return _PROBE_ENVIRONMENT.get()


def cookie_probe_binding(key, version, value, *, environment=None):
    payload = {"policy": "isolated-cookie-identity-v1", "key": key,
               "version": version, "credential": value,
               "execution": environment if environment is not None else capture_probe_environment()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
