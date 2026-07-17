from datetime import UTC, datetime, timedelta

from tests.test_api_positions import authenticated_client
from quantitative_trading.config import Settings
from quantitative_trading.notification.models import NotificationStatus, NotificationSummary
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.recommendation.models import Recommendation, RecommendationAction
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.storage.sqlite import connect


NOW = datetime(2026, 7, 13, 2, 30, tzinfo=UTC)


def recommendation(
    recommendation_id: str,
    symbol: str,
    *,
    action: RecommendationAction,
    data_time: datetime,
) -> Recommendation:
    return Recommendation(
        recommendation_id=recommendation_id,
        symbol=symbol,
        name=symbol,
        action=action,
        confidence="medium",
        position_context={},
        account_context={},
        price_context={"current_price": 10.0},
        reason=["test condition"],
        risk={"invalid_if": ["condition invalid"]},
        valid_until=NOW + timedelta(hours=5),
        data_time=data_time,
        created_at=data_time,
    )


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


def test_recommendation_list_preserves_legacy_shape_and_supports_linked_views(
    tmp_path,
) -> None:
    client, headers = authenticated_client(tmp_path)
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    with connect(settings) as connection:
        repository = RecommendationRepository(connection)
        old_600 = recommendation(
            "rec-600-old",
            "600000",
            action=RecommendationAction.HOLD,
            data_time=NOW + timedelta(minutes=10),
        )
        current_000001 = recommendation(
            "rec-000001-current",
            "000001",
            action=RecommendationAction.WATCH,
            data_time=NOW + timedelta(minutes=2),
        )
        current_600 = recommendation(
            "rec-600-current",
            "600000",
            action=RecommendationAction.REDUCE,
            data_time=NOW,
        )
        repository.save_many([old_600], created_at=NOW + timedelta(minutes=2, seconds=30))
        repository.save_many([current_000001], created_at=NOW + timedelta(minutes=2))
        repository.save_many([current_600], created_at=NOW + timedelta(minutes=3))
        notification_repository = NotificationRepository(connection)
        linked = NotificationSummary(
            notification_id="notif-600-current",
            recommendation_id=current_600.recommendation_id,
            symbol=current_600.symbol,
            action=current_600.action.value,
            confidence=current_600.confidence,
            key_price=10.0,
            reason=list(current_600.reason),
            risk=list(current_600.risk["invalid_if"]),
            data_time=current_600.data_time,
            audit_id="audit-600-current",
            status=NotificationStatus.READ,
            created_at=NOW + timedelta(minutes=3),
        )
        notification_repository.save(linked)
        notification_repository.save_canonical_group(
            "canonical-600-current",
            linked.notification_id,
            created_at=NOW + timedelta(minutes=3),
        )
        notification_repository.link_recommendation(
            current_600.recommendation_id,
            linked.notification_id,
            "canonical-600-current",
            created_at=NOW + timedelta(minutes=3),
        )

    legacy = client.get(
        "/api/v1/recommendations?page=1&page_size=2",
        headers=headers,
    )
    history = client.get(
        "/api/v1/recommendations?view=history&page=1&page_size=10",
        headers=headers,
    )
    current_first = client.get(
        "/api/v1/recommendations?view=current&page=1&page_size=1",
        headers=headers,
    )
    current_second = client.get(
        "/api/v1/recommendations?view=current&page=2&page_size=1",
        headers=headers,
    )
    invalid = client.get(
        "/api/v1/recommendations?view=latest",
        headers=headers,
    )

    assert legacy.status_code == 200
    assert legacy.json()["total"] == 3
    assert [item["recommendation_id"] for item in legacy.json()["items"]] == [
        "rec-600-old",
        "rec-000001-current",
    ]
    assert "recommendation" not in legacy.json()["items"][0]
    assert history.json()["total"] == 3
    history_items = history.json()["items"]
    assert all(set(item) == {"recommendation", "notification"} for item in history_items)
    assert [item["recommendation"]["recommendation_id"] for item in history_items] == [
        "rec-600-old",
        "rec-000001-current",
        "rec-600-current",
    ]
    assert history_items[2]["notification"] == {
        "notification_id": "notif-600-current",
        "status": "read",
    }
    assert current_first.json()["total"] == 2
    assert set(current_first.json()["items"][0]) == {"recommendation", "notification"}
    assert current_first.json()["items"][0]["recommendation"]["recommendation_id"] == (
        "rec-600-current"
    )
    assert current_first.json()["items"][0]["notification"] == {
        "notification_id": "notif-600-current",
        "status": "read",
    }
    assert current_second.json()["total"] == 2
    assert current_second.json()["items"][0]["recommendation"]["recommendation_id"] == (
        "rec-000001-current"
    )
    assert current_second.json()["items"][0]["notification"] is None
    assert invalid.status_code == 422


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
