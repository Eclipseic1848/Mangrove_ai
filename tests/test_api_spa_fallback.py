# -*- coding: utf-8 -*-
from fastapi.testclient import TestClient

from src.api.main import app


def test_unknown_api_route_returns_json_404_instead_of_spa_html():
    response = TestClient(app).get("/api/__missing_json_probe__")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "API 接口不存在"}
