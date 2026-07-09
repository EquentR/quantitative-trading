from tests.test_api_positions import authenticated_client, position_payload
from tests.test_api_watchlist import watchlist_payload


def test_create_plan_persists_universe_and_latest_reads_it(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    client.post("/api/v1/positions", json=position_payload("600000"), headers=headers)
    client.post(
        "/api/v1/watchlist/pinned",
        json={**watchlist_payload("000001"), "rank": 2, "plan_enabled": True},
        headers=headers,
    )

    create_response = client.post(
        "/api/v1/plans",
        json={"trading_day": "2026-07-09"},
        headers=headers,
    )
    latest_response = client.get("/api/v1/plans/latest", headers=headers)

    assert create_response.status_code == 201
    body = create_response.json()
    assert body["plan"]["plan_id"] == "plan-20260709"
    assert body["plan"]["trading_day"] == "2026-07-09"
    assert body["plan"]["status"] == "active"
    assert body["plan"]["universe_snapshot_id"] == 1
    assert body["plan"]["holding_symbols"] == ["600000"]
    assert body["plan"]["watch_symbols"] == ["000001"]
    assert body["plan"]["candidate_actions"]["600000"] == ["hold", "reduce"]
    assert body["plan"]["candidate_actions"]["000001"] == ["watch"]
    assert "support" in body["plan"]["key_levels"]["600000"]
    assert latest_response.status_code == 200
    assert latest_response.json() == body["plan"]


def test_get_plan_by_id_and_missing_plan_error(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    client.post("/api/v1/positions", json=position_payload("600000"), headers=headers)
    client.post(
        "/api/v1/plans",
        json={"trading_day": "2026-07-09"},
        headers=headers,
    )

    detail_response = client.get("/api/v1/plans/plan-20260709", headers=headers)
    missing_response = client.get("/api/v1/plans/missing", headers=headers)

    assert detail_response.status_code == 200
    assert detail_response.json()["plan_id"] == "plan-20260709"
    assert missing_response.status_code == 404
    assert missing_response.json()["error"]["code"] == "plan_not_found"


def test_latest_plan_returns_uniform_404_when_empty(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    response = client.get("/api/v1/plans/latest", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "plan_not_found"


def test_plan_routes_require_authentication_after_setup(tmp_path) -> None:
    client, _headers = authenticated_client(tmp_path)

    requests = [
        ("post", "/api/v1/plans"),
        ("get", "/api/v1/plans/latest"),
        ("get", "/api/v1/plans/plan-20260709"),
    ]

    for method, path in requests:
        response = getattr(client, method)(path)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"
