from datetime import date

from quantitative_trading.config import Settings
from quantitative_trading.storage.sqlite import connect
from tests.planning_fixtures import persist_test_plan
from tests.test_api_positions import authenticated_client


def test_create_plan_is_deprecated_without_persisting_data(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    response = client.post(
        "/api/v1/plans",
        json={"trading_day": "2026-07-09"},
        headers=headers,
    )

    assert response.status_code == 410
    assert response.json() == {
        "error": {
            "code": "plan_write_deprecated",
            "message": "direct plan generation is deprecated; use the close decision workflow",
            "details": {
                "api_workflow": "close",
                "cli": "qt workflow close",
            },
        }
    }
    with connect(Settings(database_path=tmp_path / "api.db")) as connection:
        plan_count = connection.execute(
            "SELECT COUNT(*) FROM trading_plans"
        ).fetchone()[0]
        universe_count = connection.execute(
            "SELECT COUNT(*) FROM universe_snapshots"
        ).fetchone()[0]
    assert plan_count == 0
    assert universe_count == 0


def test_get_plan_by_id_and_missing_plan_error(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    persist_test_plan(Settings(database_path=tmp_path / "api.db"))

    detail_response = client.get("/api/v1/plans/plan-20260709", headers=headers)
    missing_response = client.get("/api/v1/plans/missing", headers=headers)

    assert detail_response.status_code == 200
    assert detail_response.json()["plan_id"] == "plan-20260709"
    assert missing_response.status_code == 404
    assert missing_response.json()["error"]["code"] == "plan_not_found"


def test_plan_list_is_stably_paginated(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    settings = Settings(database_path=tmp_path / "api.db")
    persist_test_plan(settings)
    persist_test_plan(
        settings,
        trading_day=date(2026, 7, 10),
    )

    response = client.get(
        "/api/v1/plans?page=1&page_size=1",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert response.json()["page"] == 1
    assert response.json()["page_size"] == 1
    assert response.json()["items"][0]["plan_id"] == "plan-20260710"


def test_latest_plan_returns_uniform_404_when_empty(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    response = client.get("/api/v1/plans/latest", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "plan_not_found"


def test_plan_routes_require_authentication_after_setup(tmp_path) -> None:
    client, _headers = authenticated_client(tmp_path)

    requests = [
        ("post", "/api/v1/plans"),
        ("get", "/api/v1/plans"),
        ("get", "/api/v1/plans/latest"),
        ("get", "/api/v1/plans/plan-20260709"),
    ]

    for method, path in requests:
        response = getattr(client, method)(path)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"
