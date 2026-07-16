from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentCandidate,
    InstrumentMetadata,
    InstrumentPreview,
    InstrumentPreviewSource,
    InstrumentType,
    SettlementCycle,
)


NOW = datetime(2026, 7, 15, 2, 0, tzinfo=UTC)


def metadata(**overrides) -> InstrumentMetadata:
    payload = {
        "symbol": "600519",
        "name": "贵州茅台",
        "exchange": Exchange.SH,
        "instrument_type": InstrumentType.A_SHARE,
        "settlement_cycle": SettlementCycle.T1,
        "price_limit_ratio": 0.10,
        "metadata_source": "akshare_sse_a_share",
        "metadata_checked_at": NOW,
        "rule_version": "instrument-rules-v1",
        "warnings": [],
    }
    payload.update(overrides)
    return InstrumentMetadata.model_validate(payload)


@pytest.mark.parametrize("symbol", ["６００５１９", "60051A", "60051", "6005190"])
def test_instrument_metadata_requires_six_ascii_digits(symbol: str) -> None:
    with pytest.raises(ValidationError):
        metadata(symbol=symbol)


def test_known_instrument_requires_exchange() -> None:
    with pytest.raises(ValidationError, match="known instrument requires exchange"):
        metadata(exchange=None)


def test_a_share_requires_t1_settlement() -> None:
    with pytest.raises(ValidationError, match="A-share settlement must be t1"):
        metadata(settlement_cycle=SettlementCycle.T0)


def test_unknown_instrument_can_preserve_legacy_symbol_without_exchange() -> None:
    item = metadata(
        exchange=None,
        instrument_type=InstrumentType.UNKNOWN,
        settlement_cycle=SettlementCycle.UNKNOWN,
        price_limit_ratio=None,
        metadata_source="legacy",
        warnings=["instrument metadata unavailable"],
    )

    assert item.exchange is None
    assert item.instrument_type is InstrumentType.UNKNOWN
    assert item.settlement_cycle is SettlementCycle.UNKNOWN


def test_unknown_instrument_cannot_claim_known_settlement() -> None:
    with pytest.raises(ValidationError, match="unknown instrument requires unknown settlement"):
        metadata(
            exchange=None,
            instrument_type=InstrumentType.UNKNOWN,
            settlement_cycle=SettlementCycle.T1,
            price_limit_ratio=None,
        )


def test_instrument_preview_round_trips_normalized_candidate() -> None:
    candidate = InstrumentCandidate.from_metadata(
        metadata(),
        source=InstrumentPreviewSource.INSTRUMENT_SEARCH,
        source_rank=None,
        already_monitored=False,
        selectable=True,
    )
    preview = InstrumentPreview(
        preview_id="d98fe6ba-d471-4c10-8b84-8cfef8cd94de",
        source=InstrumentPreviewSource.INSTRUMENT_SEARCH,
        query="600519",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        items=[candidate],
        warnings=[],
    )

    restored = InstrumentPreview.model_validate_json(preview.model_dump_json())

    assert restored.items[0].symbol == "600519"
    assert restored.items[0].instrument_type is InstrumentType.A_SHARE
    assert restored.expires_at > restored.created_at


def test_instrument_preview_rejects_naive_times() -> None:
    with pytest.raises(ValidationError):
        InstrumentPreview(
            preview_id="d98fe6ba-d471-4c10-8b84-8cfef8cd94de",
            source=InstrumentPreviewSource.EASTMONEY_WATCHLIST,
            query=None,
            created_at=datetime(2026, 7, 15, 10, 0),
            expires_at=datetime(2026, 7, 15, 10, 10),
            items=[],
            warnings=[],
        )
