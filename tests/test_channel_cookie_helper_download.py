"""多渠道工具下载只分发固定源码，鉴权与原小红书包保持兼容。"""
from io import BytesIO
from pathlib import Path
import shutil
from zipfile import ZipFile

import pytest

from src.api.routes import config_routes as routes
from tests.test_auth_bootstrap import auth_client
from tests.test_xhs_cookie_helper_download import helper_client


URL = "/api/config/cookie-helper/download"
FILES = (
    "manifest.json", "channels.mjs", "cookies.mjs", "popup.html", "popup.css", "popup.mjs", "README.md", "icon.png",
    *(f"brands/{name}.png" for name in (
        "xiaohongshu", "weibo", "douyin", "bilibili", "zhihu", "kuaishou", "tieba", "jd", "taobao", "pdd",
    )),
)


@pytest.mark.parametrize("role", ["user", "admin", "super_admin"])
def test_roles_download_fixed_channel_code_without_touching_config(auth_client, role):
    client, store = helper_client(auth_client, role)
    response = client.get(URL)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == 'attachment; filename="mangrove-channel-cookie-helper.zip"'
    assert routes._CHANNEL_HELPER_FILES == FILES
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == ["mangrove-channel-cookie-helper/" + name for name in FILES]
        for name in FILES:
            assert archive.read("mangrove-channel-cookie-helper/" + name) == (routes._CHANNEL_HELPER_ROOT / name).read_bytes()
    assert client.get(URL).content == response.content
    assert store.config_all("global") == {}
    assert store.config_all(store.get_user_by_name("helper-owner")["user_id"]) == {}


def test_anonymous_revoked_and_mismatched_owner_are_rejected(auth_client):
    client, _ = auth_client
    client.app.include_router(routes.router)
    assert client.get(URL).status_code == 401
    client, _ = helper_client(auth_client)
    assert client.get(URL, headers={"X-Mangrove-Owner": "another-owner"}).status_code == 401
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get(URL).status_code == 401


@pytest.mark.parametrize("failure", ["missing", "directory", "oversized", "parent_link", "root_link"])
def test_invalid_channel_package_fails_closed(auth_client, monkeypatch, tmp_path, failure):
    client, _ = helper_client(auth_client)
    root = tmp_path / "helper"
    shutil.copytree(routes._CHANNEL_HELPER_ROOT, root)
    broken = root / "brands/weibo.png"
    if failure == "missing":
        broken.unlink()
    elif failure == "directory":
        broken.unlink()
        broken.mkdir()
    elif failure == "oversized":
        broken.write_bytes(b"x" * 65537)
    else:
        # Windows不要求创建真实链接权限；在文件系统边界明确模拟链接标志。
        original = Path.is_symlink
        linked = root if failure == "root_link" else root / "brands"
        monkeypatch.setattr(Path, "is_symlink", lambda path: path == linked or original(path))
    monkeypatch.setattr(routes, "_CHANNEL_HELPER_ROOT", root)
    response = client.get(URL)
    assert response.status_code == 503
    assert response.json() == {"detail": "登录助手安装包暂不可用，请联系管理员"}
    assert str(tmp_path) not in response.text


def test_client_paths_and_extra_files_cannot_extend_the_package(auth_client, monkeypatch, tmp_path):
    client, _ = helper_client(auth_client)
    root = tmp_path / "helper"
    shutil.copytree(routes._CHANNEL_HELPER_ROOT, root)
    (root / "private.env").write_text("synthetic-channel-private-data", encoding="utf-8")
    monkeypatch.setattr(routes, "_CHANNEL_HELPER_ROOT", root)
    response = client.get(URL, params={"filename": "../private.env", "channel": "../../outside"})
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ["mangrove-channel-cookie-helper/" + name for name in FILES]
        assert b"synthetic-channel-private-data" not in b"".join(archive.read(name) for name in archive.namelist())


def test_legacy_xhs_download_still_contains_its_original_seven_files(auth_client):
    client, _ = helper_client(auth_client)
    assert client.get(URL).status_code == 200
    response = client.get("/api/config/cookie-helper/xhs/download")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="mangrove-xhs-cookie-helper.zip"'
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ["mangrove-xhs-cookie-helper/" + name for name in routes._XHS_HELPER_FILES]
        for name in routes._XHS_HELPER_FILES:
            assert archive.read("mangrove-xhs-cookie-helper/" + name) == (routes._XHS_HELPER_ROOT / name).read_bytes()
