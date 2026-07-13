from tests.test_api_positions import authenticated_client
from quantitative_trading.config import Settings


def test_legacy_recommendation_scan_is_retired_without_writing(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    scan_response = client.post("/api/v1/recommendations/scan", headers=headers)
    list_response = client.get("/api/v1/recommendations", headers=headers)

    assert scan_response.status_code == 410
    assert scan_response.json()["error"] == {
        "code": "recommendation_scan_retired",
        "message": "recommendation scan moved to the intraday decision workflow",
        "details": {"replacement": "/api/v1/service/workflows/intraday/run"},
    }
    assert list_response.status_code == 200
    assert list_response.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
    }


def test_get_missing_recommendation_returns_uniform_404(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    response = client.get("/api/v1/recommendations/missing", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "recommendation_not_found"


def test_recommendation_trace_resolves_persisted_audit_reference(tmp_path) -> None:
    from tests.test_api_market_read import seed_market_data

    client, headers = authenticated_client(tmp_path)
    seed_market_data(
        Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    )

    response = client.get(
        "/api/v1/recommendations/rec-600000-1/trace",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["audit"] == {
        "audit_id": "audit-1",
        "event_type": "recommendation.generated",
        "recommendation_id": "rec-600000-1",
        "payload": {"symbol": "600000", "action": "hold"},
        "created_at": "2026-07-13T02:03:00Z",
    }


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
