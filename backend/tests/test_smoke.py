"""Smoke 测试：导入 app + 路由表存在性。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def _reachable(client: TestClient, method: str, path: str) -> bool:
    """通过 TestClient 探测路由是否注册（不依赖 Starlette 内部结构）。"""
    try:
        resp = client.request(method, path)
        # 任何非 404 都视为路由存在（401/405 也说明路由已注册）
        return resp.status_code != 404
    except Exception:
        return False


def test_app_creates() -> None:
    assert app.title == "CryptoQuant"


def test_health_route_registered() -> None:
    client = TestClient(app)
    assert _reachable(client, "GET", "/health")


def test_market_routes_registered() -> None:
    client = TestClient(app)
    assert _reachable(client, "GET", "/api/v1/market/tickers")
    assert _reachable(client, "GET", "/api/v1/market/klines")


def test_data_routes_registered() -> None:
    """通过 OpenAPI schema 验证路由已注册（避免触发 DB 连接）。"""
    schema = app.openapi()
    paths = schema.get("paths", {})
    assert "/api/v1/data/klines" in paths
    assert "/api/v1/data/downloads" in paths


def test_health_responds_with_db_redis_flags() -> None:
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "OK"
    assert "db" in body["data"]
    assert "redis" in body["data"]
