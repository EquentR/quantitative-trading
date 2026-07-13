from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)


StrictPositiveId = Annotated[int, Field(strict=True, gt=0)]


class QuoteStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    STALE = "stale"


class TradingStatus(StrEnum):
    NORMAL = "normal"
    SUSPENDED = "suspended"
    UNKNOWN = "unknown"


class LimitStatus(StrEnum):
    NONE = "none"
    UP = "up"
    DOWN = "down"
    UNKNOWN = "unknown"


class CaptureRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    FAILED = "failed"


class CaptureRunAlreadyActiveError(RuntimeError):
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"workflow run is already active: {run_id}")


class CaptureDataset(StrEnum):
    QUOTE = "quote"
    DAILY_BAR = "daily_bar"
    MONEY_FLOW = "money_flow"
    MINUTE_BAR = "minute_bar"
    INTRADAY_STRENGTH = "intraday_strength"


class CaptureResultStatus(StrEnum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    FAILED = "failed"
    STALE = "stale"


class StrengthLabel(StrEnum):
    STRONG = "strong"
    NEUTRAL = "neutral"
    WEAK = "weak"


class StrengthConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ComponentStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


def _must_be_timezone_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value


class QuoteSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(pattern=r"^[0-9]{6}$")
    name: str = ""
    previous_close: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    open_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    high_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    low_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    current_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    change_pct: float | None = Field(default=None, allow_inf_nan=False)
    volume: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    trading_status: TradingStatus = TradingStatus.UNKNOWN
    limit_status: LimitStatus = LimitStatus.UNKNOWN
    data_time: datetime | None = None
    fetched_at: datetime
    source: str = Field(min_length=1)
    status: QuoteStatus
    warning: str = ""

    @field_validator("data_time", "fetched_at")
    @classmethod
    def datetimes_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        return _must_be_timezone_aware(value)

    @model_validator(mode="after")
    def status_fields_must_match_quote_contract(self) -> "QuoteSnapshot":
        if self.status is QuoteStatus.OK:
            if self.current_price is None:
                raise ValueError("ok quote requires current_price")
            if self.data_time is None:
                raise ValueError("ok quote requires data_time")
        elif self.status is QuoteStatus.PARTIAL:
            if self.current_price is None:
                raise ValueError("partial quote requires current_price")
            if self.data_time is None:
                raise ValueError("partial quote requires data_time")
            if not self.warning:
                raise ValueError("partial quote requires warning")
        elif self.status is QuoteStatus.STALE:
            if self.current_price is None:
                raise ValueError("stale quote requires current_price")
            if self.data_time is None:
                raise ValueError("stale quote requires data_time")
            if not self.warning:
                raise ValueError("stale quote requires warning")
        elif self.status is QuoteStatus.FAILED and not self.warning:
            raise ValueError("failed quote requires warning")
        return self


def _canonical_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class DailyBar(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(pattern=r"^[0-9]{6}$")
    trade_date: date
    adjustment: str = Field(default="forward", pattern="^forward$")
    open: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)
    low: float = Field(gt=0, allow_inf_nan=False)
    close: float = Field(gt=0, allow_inf_nan=False)
    volume: float = Field(ge=0, allow_inf_nan=False)
    amount: float = Field(ge=0, allow_inf_nan=False)
    source: str = Field(min_length=1)
    source_updated_at: datetime | None = None
    fetched_at: datetime

    @field_validator("source_updated_at", "fetched_at")
    @classmethod
    def datetimes_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        return _must_be_timezone_aware(value)

    @model_validator(mode="after")
    def prices_must_form_valid_bar(self) -> "DailyBar":
        if self.high < max(self.open, self.low, self.close):
            raise ValueError("daily bar high must cover open, low, and close")
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("daily bar low must cover open, high, and close")
        return self

    @computed_field
    @property
    def content_hash(self) -> str:
        return _canonical_hash(
            {
                "symbol": self.symbol,
                "trade_date": self.trade_date.isoformat(),
                "adjustment": self.adjustment,
                "open": self.open,
                "high": self.high,
                "low": self.low,
                "close": self.close,
                "volume": self.volume,
                "amount": self.amount,
            }
        )


class DailyMoneyFlow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(pattern=r"^[0-9]{6}$")
    trade_date: date
    main_net_amount: float = Field(allow_inf_nan=False)
    main_net_pct: float = Field(allow_inf_nan=False)
    super_large_net_amount: float = Field(allow_inf_nan=False)
    super_large_net_pct: float = Field(allow_inf_nan=False)
    large_net_amount: float = Field(allow_inf_nan=False)
    large_net_pct: float = Field(allow_inf_nan=False)
    medium_net_amount: float = Field(allow_inf_nan=False)
    medium_net_pct: float = Field(allow_inf_nan=False)
    small_net_amount: float = Field(allow_inf_nan=False)
    small_net_pct: float = Field(allow_inf_nan=False)
    source: str = Field(min_length=1)
    source_updated_at: datetime | None = None
    fetched_at: datetime

    @field_validator("source_updated_at", "fetched_at")
    @classmethod
    def datetimes_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        return _must_be_timezone_aware(value)

    @computed_field
    @property
    def content_hash(self) -> str:
        return _canonical_hash(
            {
                key: value
                for key, value in self.model_dump(
                    exclude={"source", "source_updated_at", "fetched_at", "content_hash"}
                ).items()
            }
        )


class MinuteBar(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(pattern=r"^[0-9]{6}$")
    trade_date: date
    minute: datetime
    open: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)
    low: float = Field(gt=0, allow_inf_nan=False)
    close: float = Field(gt=0, allow_inf_nan=False)
    volume: float = Field(ge=0, allow_inf_nan=False)
    amount: float = Field(ge=0, allow_inf_nan=False)
    source: str = Field(min_length=1)
    source_updated_at: datetime | None = None
    fetched_at: datetime

    @field_validator("source_updated_at", "fetched_at")
    @classmethod
    def timestamps_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        return _must_be_timezone_aware(value)

    @model_validator(mode="after")
    def minute_must_use_shanghai_semantics(self) -> "MinuteBar":
        _must_be_timezone_aware(self.minute)
        if self.minute.utcoffset() != timedelta(hours=8):
            raise ValueError("minute must use Asia/Shanghai timezone semantics")
        if self.minute.date() != self.trade_date:
            raise ValueError("minute date must match trade_date")
        minute_time = self.minute.timetz().replace(tzinfo=None)
        if not (
            time(9, 30) <= minute_time <= time(11, 30)
            or time(13, 0) <= minute_time <= time(15, 0)
        ):
            raise ValueError("minute must fall within an A-share trading session")
        if self.high < max(self.open, self.low, self.close) or self.low > min(
            self.open, self.high, self.close
        ):
            raise ValueError("minute bar OHLC values are inconsistent")
        return self


class MarketCaptureRun(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_id: str = Field(min_length=1)
    workflow_type: str = Field(pattern="^(close|intraday|backfill|cleanup)$")
    trade_date: date
    period_start: datetime | None = None
    period_end: datetime | None = None
    idempotency_key: str = Field(min_length=1)
    status: CaptureRunStatus
    started_at: datetime
    finished_at: datetime | None = None
    requested_symbols: int = Field(default=0, ge=0)
    processed_symbols: int = Field(default=0, ge=0)
    provider_calls: int = Field(default=0, ge=0)
    provider_duration_ms: float = Field(default=0, ge=0, allow_inf_nan=False)
    rows_received: int = Field(default=0, ge=0)
    rows_written: int = Field(default=0, ge=0)
    cleaned_rows: int = Field(default=0, ge=0)
    plan_count: int = Field(default=0, ge=0)
    recommendation_count: int = Field(default=0, ge=0)
    notification_count: int = Field(default=0, ge=0)
    email_outbox_count: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    error_summary: str = ""

    @field_validator("period_start", "period_end", "started_at", "finished_at")
    @classmethod
    def run_times_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        return _must_be_timezone_aware(value)

    @computed_field
    @property
    def duration_ms(self) -> float | None:
        if self.finished_at is None:
            return None
        return max(0.0, (self.finished_at - self.started_at).total_seconds() * 1000)


class MarketCaptureResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_id: str = Field(min_length=1)
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    dataset: CaptureDataset
    status: CaptureResultStatus
    data_start: date | None = None
    data_end: date | None = None
    data_time: datetime | None = None
    fetched_at: datetime
    expected_rows: int = Field(default=0, ge=0)
    actual_rows: int = Field(default=0, ge=0)
    source: str = Field(min_length=1)
    warning: str = ""
    error_summary: str = ""

    @field_validator("data_time", "fetched_at")
    @classmethod
    def result_times_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        return _must_be_timezone_aware(value)


class DatasetSnapshotBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_id: str = Field(min_length=1)
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    data_start: date | None
    data_end: date | None
    row_count: int = Field(ge=0)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: CaptureResultStatus
    warning: str = ""
    fetched_at: datetime

    @field_validator("fetched_at")
    @classmethod
    def snapshot_time_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return _must_be_timezone_aware(value)  # type: ignore[return-value]


class HistorySnapshot(DatasetSnapshotBase):
    adjustment: str = Field(default="forward", pattern="^forward$")


class MoneyFlowSnapshot(DatasetSnapshotBase):
    pass


class StrengthComponent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    status: ComponentStatus
    value: float | None = Field(default=None, allow_inf_nan=False)
    threshold: float | None = Field(default=None, allow_inf_nan=False)
    direction: int = Field(default=0, ge=-1, le=1)
    reason: str = Field(min_length=1)
    source: str = ""

    @property
    def available(self) -> bool:
        return self.status is ComponentStatus.AVAILABLE


class IntradayStrengthSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_id: str = Field(min_length=1)
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    trade_date: date
    label: StrengthLabel
    confidence: StrengthConfidence
    degraded: bool
    degradation_reasons: list[str]
    components: list[StrengthComponent]
    direction_sum: int = 0
    minute_volume_ratio: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    thresholds: dict[str, float]
    rule_version: str = Field(min_length=1)
    last_minute: datetime | None = None
    data_coverage: float = Field(ge=0, le=1)
    source: str = Field(min_length=1)
    data_time: datetime
    fetched_at: datetime

    @field_validator("last_minute", "data_time", "fetched_at")
    @classmethod
    def strength_times_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        return _must_be_timezone_aware(value)


class DatasetQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CaptureResultStatus
    data_start: date | None = None
    data_end: date | None = None
    data_time: datetime | None = None
    expected_rows: int = Field(default=0, ge=0)
    actual_rows: int = Field(default=0, ge=0)
    source: str = ""
    warning: str = ""

    @field_validator("data_time")
    @classmethod
    def quality_time_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        return _must_be_timezone_aware(value)


class MarketInputSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_snapshot_id: StrictPositiveId
    quote_snapshot_refs: dict[str, StrictPositiveId]
    history_snapshot_refs: dict[str, StrictPositiveId]
    money_flow_snapshot_refs: dict[str, StrictPositiveId]
    intraday_strength_snapshot_refs: dict[str, StrictPositiveId]
    dataset_quality: dict[str, dict[CaptureDataset, DatasetQuality]] = Field(
        default_factory=dict
    )
    thresholds: dict[str, float] = Field(default_factory=dict)
    capture_run_id: str | None = Field(default=None, min_length=1)
    data_time: datetime | None = None
    fetched_at: datetime
    warnings: list[str]

    @field_validator("data_time", "fetched_at")
    @classmethod
    def datetimes_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        return _must_be_timezone_aware(value)

    @field_validator(
        "quote_snapshot_refs",
        "history_snapshot_refs",
        "money_flow_snapshot_refs",
        "intraday_strength_snapshot_refs",
    )
    @classmethod
    def references_must_be_valid(
        cls, value: dict[str, StrictPositiveId]
    ) -> dict[str, StrictPositiveId]:
        if any(
            len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit()
            for symbol in value
        ):
            raise ValueError("snapshot reference symbols must contain six ASCII digits")
        return value

    @field_validator("dataset_quality")
    @classmethod
    def quality_symbols_must_be_valid(
        cls, value: dict[str, dict[CaptureDataset, DatasetQuality]]
    ) -> dict[str, dict[CaptureDataset, DatasetQuality]]:
        if any(
            len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit()
            for symbol in value
        ):
            raise ValueError("dataset quality symbols must contain six ASCII digits")
        return value
