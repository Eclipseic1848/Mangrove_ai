# -*- coding: utf-8 -*-
from fastapi.testclient import TestClient
from fastapi import FastAPI
import pytest
import os
import subprocess

from src.api import main
from src.api.main import app


def test_unknown_api_route_returns_json_404_instead_of_spa_html():
    response = TestClient(app).get("/api/__missing_json_probe__")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "API 接口不存在"}


@pytest.fixture
def frontend_dist(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>工作台</html>", encoding="utf-8")
    (dist / "logo.svg").write_text("<svg>品牌</svg>", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("仅限构建目录外", encoding="utf-8")
    monkeypatch.setattr(main, "_FRONTEND_DIST", dist)
    return dist


@pytest.mark.parametrize("path", ["/%2e%2e%2foutside.txt", "/..%5coutside.txt"])
def test_anonymous_spa_request_rejects_paths_outside_dist(frontend_dist, path):
    response = TestClient(app).get(path)

    assert response.status_code == 404
    assert "仅限构建目录外" not in response.text


def test_spa_keeps_normal_assets_and_client_routes(frontend_dist):
    client = TestClient(app)

    assert client.get("/logo.svg").text == "<svg>品牌</svg>"
    assert client.get("/data-prep/task-example").text == "<html>工作台</html>"


@pytest.mark.parametrize("name,path", [("linked.txt", "/linked.txt"), ("index.html", "/data-prep")])
def test_spa_rejects_links_to_files_outside_dist(frontend_dist, name, path):
    link = frontend_dist / name
    if link.exists():
        link.unlink()
    try:
        link.symlink_to(frontend_dist.parent / "outside.txt")
    except OSError as exc:
        pytest.skip(f"当前环境不允许创建符号链接：{exc}")

    response = TestClient(app).get(path)

    assert response.status_code == 404
    assert "仅限构建目录外" not in response.text


def test_missing_frontend_build_returns_404(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_FRONTEND_DIST", tmp_path / "not-built")

    assert TestClient(app).get("/data-prep").status_code == 404


def test_assets_mount_root_cannot_escape_through_directory_link(frontend_dist):
    from src.api.main import FrontendAssets

    target = frontend_dist.parent / "outside-assets"
    target.mkdir()
    (target / "app.js").write_text("仅限构建目录外", encoding="utf-8")
    link = frontend_dist / "assets"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        # Windows 无符号链接权限时，使用无需提权的真实目录联接验证相同边界。
        quoted_link = "'" + str(link).replace("'", "''") + "'"
        quoted_target = "'" + str(target).replace("'", "''") + "'"
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             f"New-Item -ItemType Junction -Path {quoted_link} -Target {quoted_target} | Out-Null"],
            check=True, capture_output=True, encoding="utf-8",
        )
    isolated_app = FastAPI()
    isolated_app.mount("/assets", FrontendAssets(directory=frontend_dist))

    response = TestClient(isolated_app).get("/assets/app.js")

    assert response.status_code == 404
    assert "仅限构建目录外" not in response.text


def test_assets_keep_head_conditional_requests_and_missing_file_404(frontend_dist):
    from src.api.main import FrontendAssets

    assets = frontend_dist / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('fixture');", encoding="utf-8")
    isolated_app = FastAPI()
    isolated_app.mount("/assets", FrontendAssets(directory=frontend_dist))
    client = TestClient(isolated_app)

    response = client.get("/assets/app.js")

    assert response.status_code == 200
    assert response.text == "console.log('fixture');"
    assert client.head("/assets/app.js").status_code == 200
    assert client.get("/assets/app.js", headers={"If-None-Match": response.headers["etag"]}).status_code == 304
    assert client.get("/assets/missing.js").status_code == 404
