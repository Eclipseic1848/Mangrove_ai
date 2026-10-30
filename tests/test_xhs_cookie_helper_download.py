"""本机助手只分发固定工具源码，下载不读取或修改采集凭据。"""
from io import BytesIO
import shutil
from zipfile import ZipFile

import pytest

from src.api import auth
from src.api.routes import config_routes as routes
from tests.test_auth_bootstrap import auth_client


URL = "/api/config/cookie-helper/xhs/download"


def helper_client(auth_client, role="user"):
    client, store = auth_client
    client.app.include_router(routes.router)
    password = "synthetic-helper-password"
    store.create_user("helper-owner", auth.hash_password(password), role=role, pending=False)
    assert client.post("/api/auth/login", json={"username": "helper-owner", "password": password}).status_code == 200
    return client, store


@pytest.mark.parametrize("role", ["user", "admin", "super_admin"])
def test_authenticated_roles_download_the_same_fixed_code(auth_client, role):
    client, store = helper_client(auth_client, role)
    response = client.get(URL)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == 'attachment; filename="mangrove-xhs-cookie-helper.zip"'
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == ["mangrove-xhs-cookie-helper/" + name for name in routes._XHS_HELPER_FILES]
        for name in routes._XHS_HELPER_FILES:
            assert archive.read("mangrove-xhs-cookie-helper/" + name) == (routes._XHS_HELPER_ROOT / name).read_bytes()
    assert store.config_all("global") == {}
    assert store.config_all(store.get_user_by_name("helper-owner")["user_id"]) == {}
    assert client.get(URL).content == response.content


def test_anonymous_revoked_and_wrong_owner_cannot_download(auth_client):
    client, _ = auth_client
    client.app.include_router(routes.router)
    assert client.get(URL).status_code == 401
    client, _ = helper_client(auth_client)
    assert client.get(URL, headers={"X-Mangrove-Owner": "another-owner"}).status_code == 401
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get(URL).status_code == 401


@pytest.mark.parametrize("failure", ["missing", "directory", "oversized"])
def test_incomplete_package_fails_closed_without_host_paths(auth_client, monkeypatch, tmp_path, failure):
    client, _ = helper_client(auth_client)
    root = tmp_path / "helper"
    root.mkdir()
    for name in routes._XHS_HELPER_FILES:
        shutil.copyfile(routes._XHS_HELPER_ROOT / name, root / name)
    broken = root / "manifest.json"
    if failure == "missing":
        broken.unlink()
    elif failure == "directory":
        broken.unlink()
        broken.mkdir()
    else:
        broken.write_bytes(b"x" * 65537)
    monkeypatch.setattr(routes, "_XHS_HELPER_ROOT", root)
    response = client.get(URL)
    assert response.status_code == 503
    assert response.json() == {"detail": "登录助手安装包暂不可用，请联系管理员"}
    assert str(tmp_path) not in response.text


def test_extra_files_and_requested_paths_are_not_packaged(auth_client, monkeypatch, tmp_path):
    client, _ = helper_client(auth_client)
    root = tmp_path / "helper"
    root.mkdir()
    for name in routes._XHS_HELPER_FILES:
        shutil.copyfile(routes._XHS_HELPER_ROOT / name, root / name)
    (root / "private.env").write_text("synthetic-private-data", encoding="utf-8")
    monkeypatch.setattr(routes, "_XHS_HELPER_ROOT", root)
    response = client.get(URL, params={"filename": "../private.env"})
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        assert all("private.env" not in name for name in archive.namelist())
        assert b"synthetic-private-data" not in b"".join(archive.read(name) for name in archive.namelist())
