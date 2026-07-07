from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus


def test_quote_snapshot_accepts_ok_quote() -> None:
    quote = QuoteSnapshot.model_validate(
        {
            "symbol": "600000",
            "name": "浦发银行",
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


def test_quote_snapshot_rejects_invalid_symbol() -> None:
    with pytest.raises(ValidationError):
        QuoteSnapshot.model_validate(
            {
                "symbol": "60000",
                "fetched_at": datetime(2026, 7, 7, 2, 30, tzinfo=UTC),
                "source": "akshare",
                "status": "failed",
            }
        )


@pytest.mark.parametrize("field", ["data_time", "fetched_at"])
def test_quote_snapshot_rejects_naive_datetimes(field: str) -> None:
    payload = {
        "symbol": "600000",
        "current_price": 10.5,
        "data_time": datetime(2026, 7, 7, 10, 30, tzinfo=UTC),
        "fetched_at": datetime(2026, 7, 7, 10, 30, 3, tzinfo=UTC),
        "source": "akshare",
        "status": "ok",
    }
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
    quote = QuoteSnapshot(
        symbol="600000",
        fetched_at=datetime(2026, 7, 7, 2, 30, tzinfo=UTC),
        source="test",
        status=status,
        warning="not fully usable",
    )

    assert quote.model_dump(mode="json")["status"] == status.value
    assert f'"status":"{status.value}"' in quote.model_dump_json()
