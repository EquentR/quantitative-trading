from datetime import datetime

from tests.test_api_positions import authenticated_client, position_payload
from tests.test_api_watchlist import watchlist_payload


def test_get_universe_merges_holding_and_disabled_watchlist_item(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    create_position = client.post(
        "/api/v1/positions",
        json=position_payload("600000"),
        headers=headers,
    )
    create_watch = client.post(
        "/api/v1/watchlist/pinned",
        json={**watchlist_payload("600000"), "name": "自选名称", "plan_enabled": False},
        headers=headers,
    )

    response = client.get("/api/v1/universe", headers=headers)

    assert create_position.status_code == 201
    assert create_watch.status_code == 201
    assert response.status_code == 200
    members = response.json()
    assert len(members) == 1
    member = members[0]
    assert member["symbol"] == "600000"
    assert member["name"] == "浦发银行"
    assert set(member["sources"]) == {"holding", "watch_pinned"}
    assert member["plan_enabled"] is True
    assert member["plan_enabled_source"] == "holding"
    assert member["ledger_updated_at"] == create_position.json()["updated_at"]
    assert member["watch_pinned_rank"] == 1
    created_at = datetime.fromisoformat(member["created_at"])
    assert created_at.tzinfo is not None
    assert created_at.utcoffset() is not None


def test_universe_snapshot_create_persists_and_latest_reads_it(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    client.post("/api/v1/positions", json=position_payload("600000"), headers=headers)
    client.post(
        "/api/v1/watchlist/pinned",
        json={**watchlist_payload("000001"), "rank": 2, "plan_enabled": True},
        headers=headers,
    )

    create_response = client.post("/api/v1/universe/snapshots", headers=headers)
    latest_response = client.get("/api/v1/universe/snapshots/latest", headers=headers)

    assert create_response.status_code == 201
    body = create_response.json()
    assert body["snapshot_id"] == 1
    assert body["snapshot"]["status"] == "ok"
    assert body["snapshot"]["warnings"] == []
    assert [member["symbol"] for member in body["snapshot"]["members"]] == [
        "600000",
        "000001",
    ]
    assert latest_response.status_code == 200
    assert latest_response.json() == body["snapshot"]


def test_latest_universe_snapshot_returns_uniform_404_when_empty(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    response = client.get("/api/v1/universe/snapshots/latest", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "snapshot_not_found"


def test_universe_routes_require_authentication_after_setup(tmp_path) -> None:
    client, _headers = authenticated_client(tmp_path)

    requests = [
        ("get", "/api/v1/universe"),
        ("post", "/api/v1/universe/snapshots"),
        ("get", "/api/v1/universe/snapshots/latest"),
    ]

    for method, path in requests:
        response = getattr(client, method)(path)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"
