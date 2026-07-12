from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.market.models import MarketInputSnapshot, QuoteSnapshot, QuoteStatus
from quantitative_trading.market.providers import MarketDataProvider
from quantitative_trading.market.repository import (
    MarketInputSnapshotRepository,
    QuoteSnapshotRepository,
)
from quantitative_trading.sanitization import redact_sensitive_text, safe_error_summary
from quantitative_trading.universe.models import UniverseSnapshot, UniverseSnapshotStatus
from quantitative_trading.universe.repository import UniverseSnapshotRepository
from quantitative_trading.universe.service import build_universe
from quantitative_trading.watchlist.repository import WatchPinnedRepository


_PHASE_BOUNDARY_WARNINGS = (
    "历史K线快照未在此阶段采集",
    "资金流快照未在此阶段采集",
    "分时强弱快照未在此阶段采集",
)
_BARE_BEARER_TOKEN_RE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")


@dataclass(frozen=True)
class CreatedMarketInputSnapshot:
    universe_snapshot_id: int
    market_input_snapshot_id: int
    quotes: dict[str, QuoteSnapshot]


class MarketSnapshotService:
    """Capture the decision-enabled market inputs used by later workflows."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        provider: MarketDataProvider,
        *,
        now: datetime | None = None,
    ) -> None:
        self.connection = connection
        self.provider = provider
        self.now = now

    def capture(self) -> CreatedMarketInputSnapshot:
        fetched_at = self.now or datetime.now(UTC)
        _require_timezone_aware(fetched_at)

        members = build_universe(
            positions=PositionRepository(self.connection).list(),
            watchlist=WatchPinnedRepository(self.connection).list(),
            created_at=fetched_at,
        )
        requested_symbols = sorted(
            member.symbol for member in members if member.plan_enabled
        )
        quotes, collection_warnings = self._fetch_quotes(
            requested_symbols,
            fetched_at=fetched_at,
        )
        universe_snapshot = UniverseSnapshot(
            created_at=fetched_at,
            status=UniverseSnapshotStatus.OK,
            warnings=[],
            members=members,
        )

        try:
            universe_snapshot_id = UniverseSnapshotRepository(self.connection).save(
                universe_snapshot,
                commit=False,
            )
            quote_repository = QuoteSnapshotRepository(self.connection)
            quote_snapshot_refs = {
                symbol: quote_repository.save(quotes[symbol], commit=False)
                for symbol in requested_symbols
            }
            market_snapshot = MarketInputSnapshot(
                universe_snapshot_id=universe_snapshot_id,
                quote_snapshot_refs=quote_snapshot_refs,
                history_snapshot_refs={},
                money_flow_snapshot_refs={},
                intraday_strength_snapshot_refs={},
                data_time=min(
                    (
                        quote.data_time
                        for quote in quotes.values()
                        if quote.data_time is not None
                    ),
                    default=None,
                ),
                fetched_at=fetched_at,
                warnings=[*collection_warnings, *_PHASE_BOUNDARY_WARNINGS],
            )
            market_input_snapshot_id = MarketInputSnapshotRepository(self.connection).save(
                market_snapshot,
                commit=False,
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

        return CreatedMarketInputSnapshot(
            universe_snapshot_id=universe_snapshot_id,
            market_input_snapshot_id=market_input_snapshot_id,
            quotes=quotes,
        )

    def _fetch_quotes(
        self,
        requested_symbols: Sequence[str],
        *,
        fetched_at: datetime,
    ) -> tuple[dict[str, QuoteSnapshot], list[str]]:
        if not requested_symbols:
            return {}, ["无决策启用标的，未调用行情数据源"]

        try:
            provider_quotes = self.provider.get_quotes(requested_symbols)
        except Exception as exc:
            error = _sanitize_warning(safe_error_summary(exc))
            warning = f"行情数据源调用失败: {error}"
            return (
                {
                    symbol: _failed_quote(
                        symbol=symbol,
                        fetched_at=fetched_at,
                        warning=warning,
                    )
                    for symbol in requested_symbols
                },
                [warning],
            )

        requested_set = set(requested_symbols)
        extras = sorted(set(provider_quotes) - requested_set)
        warnings = []
        if extras:
            warnings.append(
                _sanitize_warning(
                    f"行情数据源返回了未请求标的，已忽略: {', '.join(extras)}"
                )
            )

        quotes: dict[str, QuoteSnapshot] = {}
        for symbol in requested_symbols:
            quote = provider_quotes.get(symbol)
            if quote is None:
                quote = _failed_quote(
                    symbol=symbol,
                    fetched_at=fetched_at,
                    warning=f"行情数据源未返回标的 {symbol} 的报价",
                )
            elif quote.symbol != symbol:
                warnings.append(f"行情数据源报价标的 {symbol} symbol mismatch，已记录失败")
                quote = _failed_quote(
                    symbol=symbol,
                    fetched_at=fetched_at,
                    warning=f"行情数据源报价标的 {symbol} symbol mismatch",
                )
            elif quote.warning:
                quote = quote.model_copy(
                    update={"warning": _sanitize_warning(quote.warning)}
                )

            quotes[symbol] = quote
            if quote.status is not QuoteStatus.OK:
                warnings.append(f"标的 {symbol} 行情状态为 {quote.status.value}")

        return quotes, warnings


def _failed_quote(*, symbol: str, fetched_at: datetime, warning: str) -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol=symbol,
        fetched_at=fetched_at,
        source="market_snapshot_service",
        status=QuoteStatus.FAILED,
        warning=_sanitize_warning(warning),
    )


def _require_timezone_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("market snapshot fetched_at must be timezone-aware")


def _sanitize_warning(value: str) -> str:
    redacted = redact_sensitive_text(value)
    return _BARE_BEARER_TOKEN_RE.sub("Bearer [redacted]", redacted)
