from datetime import UTC, datetime
from math import inf, nan

import pytest
from pydantic import ValidationError

from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus


def valid_quote_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "symbol": "600000",
        "name": "Pufa Bank",
        "current_price": 10.5,
        "change_pct": 1.2,
        "data_time": datetime(2026, 7, 7, 10, 30, tzinfo=UTC),
        "fetched_at": datetime(2026, 7, 7, 10, 30, 3, tzinfo=UTC),
        "source": "akshare",
        "status": "ok",
        "warning": "",
    }
    payload.update(overrides)
    return payload


def test_quote_snapshot_accepts_ok_quote() -> None:
    quote = QuoteSnapshot.model_validate(
        {
            "symbol": "600000",
            "name": "Pufa Bank",
            "current_price": 10.5,
            "change_pct": 1.2,
            "data_time": "2026-07-07T10:30:00+08:00",
            "fetched_at": "2026-07-07T10:30:03+08:00",
            "source": "akshare",
            "status": "ok",
            "warning": "",
        }
    )

    assert quote.symbol == "600000"
    assert quote.status is QuoteStatus.OK
    assert quote.current_price == 10.5
    assert quote.data_time is not None
    assert quote.data_time.utcoffset() is not None
    assert quote.fetched_at.utcoffset() is not None


def test_quote_snapshot_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        QuoteSnapshot.model_validate(valid_quote_payload(raw_price=10.5))


def test_quote_snapshot_rejects_invalid_symbol() -> None:
    with pytest.raises(ValidationError):
        QuoteSnapshot.model_validate(
            {
                "symbol": "60000",
                "fetched_at": datetime(2026, 7, 7, 2, 30, tzinfo=UTC),
                "source": "akshare",
                "status": "failed",
                "warning": "quote not found",
            }
        )


@pytest.mark.parametrize("source", ["", "   "])
def test_quote_snapshot_rejects_blank_source(source: str) -> None:
    with pytest.raises(ValidationError):
        QuoteSnapshot.model_validate(valid_quote_payload(source=source))


@pytest.mark.parametrize("field", ["data_time", "fetched_at"])
def test_quote_snapshot_rejects_naive_datetimes(field: str) -> None:
    payload = valid_quote_payload()
    payload[field] = datetime(2026, 7, 7, 10, 30)

    with pytest.raises(ValidationError):
        QuoteSnapshot.model_validate(payload)


@pytest.mark.parametrize("current_price", [0, -0.01])
def test_quote_snapshot_rejects_nonpositive_price(current_price: float) -> None:
    with pytest.raises(ValidationError):
        QuoteSnapshot.model_validate(
            {
                "symbol": "600000",
                "current_price": current_price,
                "data_time": datetime(2026, 7, 7, 10, 30, tzinfo=UTC),
                "fetched_at": datetime(2026, 7, 7, 10, 30, 3, tzinfo=UTC),
                "source": "akshare",
                "status": "ok",
            }
        )


@pytest.mark.parametrize("current_price", [nan, inf, -inf])
def test_quote_snapshot_rejects_nonfinite_current_price(current_price: float) -> None:
    with pytest.raises(ValidationError):
        QuoteSnapshot.model_validate(valid_quote_payload(current_price=current_price))


@pytest.mark.parametrize("change_pct", [nan, inf, -inf])
def test_quote_snapshot_rejects_nonfinite_change_pct(change_pct: float) -> None:
    with pytest.raises(ValidationError):
        QuoteSnapshot.model_validate(valid_quote_payload(change_pct=change_pct))


@pytest.mark.parametrize("missing_field", ["current_price", "data_time"])
def test_ok_quote_requires_price_and_data_time(missing_field: str) -> None:
    payload = valid_quote_payload()
    payload[missing_field] = None

    with pytest.raises(ValidationError):
        QuoteSnapshot.model_validate(payload)


@pytest.mark.parametrize("overrides", [{"current_price": None}, {"warning": ""}])
def test_partial_quote_requires_price_and_warning(overrides: dict[str, object]) -> None:
    payload = valid_quote_payload(status="partial", warning="missing name")
    payload.update(overrides)

    with pytest.raises(ValidationError):
        QuoteSnapshot.model_validate(payload)


@pytest.mark.parametrize("status", [QuoteStatus.FAILED, QuoteStatus.STALE])
def test_unusable_quote_statuses_require_warning(status: QuoteStatus) -> None:
    with pytest.raises(ValidationError):
        QuoteSnapshot.model_validate(
            {
                "symbol": "600000",
                "fetched_at": datetime(2026, 7, 7, 2, 30, tzinfo=UTC),
                "source": "test",
                "status": status,
                "warning": "",
            }
        )


def test_quote_snapshot_allows_missing_price_for_failed_quote() -> None:
    quote = QuoteSnapshot(
        symbol="600000",
        fetched_at=datetime(2026, 7, 7, 2, 30, tzinfo=UTC),
        source="disabled",
        status=QuoteStatus.FAILED,
        warning="market fetch disabled",
    )

    assert quote.current_price is None
    assert quote.data_time is None


@pytest.mark.parametrize(
    "status",
    [QuoteStatus.PARTIAL, QuoteStatus.STALE, QuoteStatus.FAILED],
)
def test_quote_statuses_serialize_to_contract_values(status: QuoteStatus) -> None:
    quote_kwargs: dict[str, object] = {}
    if status is QuoteStatus.PARTIAL:
        quote_kwargs = {
            "current_price": 10.5,
            "data_time": datetime(2026, 7, 7, 2, 30, tzinfo=UTC),
        }

    quote = QuoteSnapshot(
        symbol="600000",
        fetched_at=datetime(2026, 7, 7, 2, 30, tzinfo=UTC),
        source="test",
        status=status,
        warning="not fully usable",
        **quote_kwargs,
    )

    assert quote.model_dump(mode="json")["status"] == status.value
    assert f'"status":"{status.value}"' in quote.model_dump_json()
