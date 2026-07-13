from datetime import UTC, datetime
from math import inf, nan

import pytest
from pydantic import ValidationError

from quantitative_trading.market.models import (
    MarketInputSnapshot,
    QuoteSnapshot,
    QuoteStatus,
)


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


def valid_market_input_snapshot_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "universe_snapshot_id": 1,
        "quote_snapshot_refs": {"600000": 10},
        "history_snapshot_refs": {},
        "money_flow_snapshot_refs": {},
        "intraday_strength_snapshot_refs": {},
        "data_time": datetime(2026, 7, 12, 6, 0, tzinfo=UTC),
        "fetched_at": datetime(2026, 7, 12, 6, 0, 5, tzinfo=UTC),
        "warnings": [],
    }
    payload.update(overrides)
    return payload


def test_market_input_snapshot_accepts_quote_snapshot_refs() -> None:
    snapshot = MarketInputSnapshot(
        universe_snapshot_id=1,
        quote_snapshot_refs={"600000": 10},
        history_snapshot_refs={},
        money_flow_snapshot_refs={},
        intraday_strength_snapshot_refs={},
        data_time=datetime(2026, 7, 12, 6, 0, tzinfo=UTC),
        fetched_at=datetime(2026, 7, 12, 6, 0, 5, tzinfo=UTC),
        warnings=[],
    )

    assert snapshot.quote_snapshot_refs == {"600000": 10}


@pytest.mark.parametrize("field", ["data_time", "fetched_at"])
def test_market_input_snapshot_rejects_naive_datetimes(field: str) -> None:
    payload = valid_market_input_snapshot_payload()
    payload[field] = datetime(2026, 7, 12, 6, 0)

    with pytest.raises(ValidationError):
        MarketInputSnapshot.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "quote_snapshot_refs",
        "history_snapshot_refs",
        "money_flow_snapshot_refs",
        "intraday_strength_snapshot_refs",
    ],
)
def test_market_input_snapshot_rejects_invalid_reference_symbols(field: str) -> None:
    payload = valid_market_input_snapshot_payload(**{field: {"SH600000": 10}})

    with pytest.raises(ValidationError):
        MarketInputSnapshot.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "quote_snapshot_refs",
        "history_snapshot_refs",
        "money_flow_snapshot_refs",
        "intraday_strength_snapshot_refs",
    ],
)
@pytest.mark.parametrize("symbol", ["６０００００", "٦٠٠٠٠٠"])
def test_market_input_snapshot_rejects_unicode_digit_reference_symbols(
    field: str,
    symbol: str,
) -> None:
    payload = valid_market_input_snapshot_payload(**{field: {symbol: 10}})

    with pytest.raises(ValidationError):
        MarketInputSnapshot.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "quote_snapshot_refs",
        "history_snapshot_refs",
        "money_flow_snapshot_refs",
        "intraday_strength_snapshot_refs",
    ],
)
@pytest.mark.parametrize("reference_id", [0, -1])
def test_market_input_snapshot_rejects_nonpositive_reference_ids(
    field: str,
    reference_id: int,
) -> None:
    payload = valid_market_input_snapshot_payload(**{field: {"600000": reference_id}})

    with pytest.raises(ValidationError):
        MarketInputSnapshot.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "universe_snapshot_id",
        "quote_snapshot_refs",
        "history_snapshot_refs",
        "money_flow_snapshot_refs",
        "intraday_strength_snapshot_refs",
    ],
)
@pytest.mark.parametrize("snapshot_id", [True, "1", 1.0])
def test_market_input_snapshot_rejects_coercible_snapshot_ids(
    field: str,
    snapshot_id: object,
) -> None:
    value = snapshot_id if field == "universe_snapshot_id" else {"600000": snapshot_id}
    payload = valid_market_input_snapshot_payload(**{field: value})

    with pytest.raises(ValidationError):
        MarketInputSnapshot.model_validate(payload)


def test_market_input_snapshot_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        MarketInputSnapshot.model_validate(
            valid_market_input_snapshot_payload(unexpected="value")
        )


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


@pytest.mark.parametrize("symbol", ["６０００００", "٦٠٠٠٠٠"])
def test_quote_snapshot_rejects_unicode_digit_symbol(symbol: str) -> None:
    with pytest.raises(ValidationError):
        QuoteSnapshot.model_validate(valid_quote_payload(symbol=symbol))


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
def test_partial_quote_requires_price_data_time_and_warning(overrides: dict[str, object]) -> None:
    payload = valid_quote_payload(status="partial", warning="missing name")
    payload.update(overrides)

    with pytest.raises(ValidationError):
        QuoteSnapshot.model_validate(payload)


def test_partial_quote_allows_unknown_market_time_without_using_fetch_time() -> None:
    payload = valid_quote_payload(
        status="partial",
        warning="market source time unavailable",
    )
    payload["data_time"] = None

    quote = QuoteSnapshot.model_validate(payload)

    assert quote.current_price is not None
    assert quote.data_time is None
    assert quote.fetched_at is not None


@pytest.mark.parametrize("overrides", [{"current_price": None}, {"data_time": None}, {"warning": ""}])
def test_stale_quote_requires_price_data_time_and_warning(overrides: dict[str, object]) -> None:
    payload = valid_quote_payload(status="stale", warning="quote is stale")
    payload.update(overrides)

    with pytest.raises(ValidationError):
        QuoteSnapshot.model_validate(payload)


def test_stale_quote_accepts_old_price_data_time_and_warning() -> None:
    quote = QuoteSnapshot.model_validate(
        valid_quote_payload(
            status="stale",
            warning="quote is stale",
            data_time=datetime(2026, 7, 6, 7, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 7, 2, 30, tzinfo=UTC),
        )
    )

    assert quote.status is QuoteStatus.STALE
    assert quote.current_price == 10.5
    assert quote.data_time == datetime(2026, 7, 6, 7, 0, tzinfo=UTC)


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
    if status in {QuoteStatus.PARTIAL, QuoteStatus.STALE}:
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
