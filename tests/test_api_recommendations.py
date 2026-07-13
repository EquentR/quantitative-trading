from datetime import UTC, datetime

from quantitative_trading.config import Settings
from tests.planning_fixtures import persist_test_plan
from tests.test_api_positions import authenticated_client, position_payload
from tests.test_api_watchlist import watchlist_payload


def test_scan_recommendations_persists_list_and_detail(tmp_path, monkeypatch) -> None:
    import quantitative_trading.api.routes.recommendations as recommendation_routes

    client, headers = authenticated_client(tmp_path)
    client.post("/api/v1/positions", json=position_payload("600000"), headers=headers)
    client.post(
        "/api/v1/watchlist/pinned",
        json={**watchlist_payload("000001"), "rank": 2, "plan_enabled": True},
        headers=headers,
    )
    persist_test_plan(Settings(database_path=tmp_path / "api.db"))
    monkeypatch.setattr(
        recommendation_routes,
        "_current_time",
        lambda: datetime(2026, 7, 9, 6, 0, tzinfo=UTC),
    )

    scan_response = client.post("/api/v1/recommendations/scan", headers=headers)
    list_response = client.get("/api/v1/recommendations", headers=headers)

    assert scan_response.status_code == 201
    body = scan_response.json()
    assert body["count"] == 2
    assert [item["symbol"] for item in body["recommendations"]] == ["600000", "000001"]
    assert body["recommendations"][0]["action"] == "hold"
    assert body["recommendations"][0]["risk"]["invalid_if"]
    assert body["recommendations"][0]["data_time"]
    assert list_response.status_code == 200
    assert list_response.json() == body["recommendations"]

    recommendation_id = body["recommendations"][0]["recommendation_id"]
    detail_response = client.get(
        f"/api/v1/recommendations/{recommendation_id}",
        headers=headers,
    )

    assert detail_response.status_code == 200
    assert detail_response.json()["recommendation_id"] == recommendation_id

    trace_response = client.get(
        f"/api/v1/recommendations/{recommendation_id}/trace",
        headers=headers,
    )
    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert trace["recommendation"]["recommendation_id"] == recommendation_id
    assert trace["plan"]["plan_id"] == "plan-20260709"
    assert trace["market_input_snapshot"] is None
    assert trace["references"]["plan"]["status"] == "active"

    paged_response = client.get(
        "/api/v1/recommendations?page=1&page_size=1",
        headers=headers,
    )
    assert paged_response.status_code == 200
    assert paged_response.json()["total"] == 2
    assert len(paged_response.json()["items"]) == 1


def test_recommendation_scan_without_plan_returns_uniform_404(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    response = client.post("/api/v1/recommendations/scan", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "plan_not_found"


def test_recommendation_scan_rejects_expired_plan_without_persisting(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.api.routes.recommendations as recommendation_routes

    client, headers = authenticated_client(tmp_path)
    client.post("/api/v1/positions", json=position_payload("600000"), headers=headers)
    persist_test_plan(Settings(database_path=tmp_path / "api.db"))
    monkeypatch.setattr(
        recommendation_routes,
        "_current_time",
        lambda: datetime(2026, 7, 9, 8, 0, tzinfo=UTC),
    )

    scan_response = client.post("/api/v1/recommendations/scan", headers=headers)
    list_response = client.get("/api/v1/recommendations", headers=headers)

    assert scan_response.status_code == 422
    body = scan_response.json()
    assert body["error"]["code"] == "plan_not_scannable"
    assert body["error"]["message"] == "trading plan is not scannable"
    assert body["error"]["details"]["plan_id"] == "plan-20260709"
    assert body["error"]["details"]["status"] == "active"
    assert list_response.status_code == 200
    assert list_response.json() == []


def test_get_missing_recommendation_returns_uniform_404(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    response = client.get("/api/v1/recommendations/missing", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "recommendation_not_found"


def test_recommendation_routes_require_authentication_after_setup(tmp_path) -> None:
    client, _headers = authenticated_client(tmp_path)

    requests = [
        ("post", "/api/v1/recommendations/scan"),
        ("get", "/api/v1/recommendations"),
        ("get", "/api/v1/recommendations/rec-plan-20260709-600000"),
        ("get", "/api/v1/recommendations/rec-plan-20260709-600000/trace"),
    ]

    for method, path in requests:
        response = getattr(client, method)(path)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"
