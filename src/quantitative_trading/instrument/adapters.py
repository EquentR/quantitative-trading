from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentMetadata,
    InstrumentType,
    SettlementCycle,
)
from quantitative_trading.instrument.rules import InstrumentTradingRuleResolver
from quantitative_trading.sanitization import safe_error_summary


class InstrumentDirectoryProviderError(RuntimeError):
    pass


SSE_FUND_LIST_URL = "https://query.sse.com.cn/commonSoaQuery.do"
SSE_SUBCLASS_CONFLICT = "__conflict__"
SseFundClassificationTransport = Callable[[str, dict[str, str], float], object]


class SseFundClassificationProvider(Protocol):
    def fetch(self) -> dict[str, str]: ...


class SseFundClassificationSource:
    def __init__(
        self,
        *,
        transport: SseFundClassificationTransport | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._transport = transport or _urllib_json_get
        self._timeout_seconds = timeout_seconds

    def fetch(self) -> dict[str, str]:
        query = urlencode(
            {
                "isPagination": "true",
                "pageHelp.pageSize": "10000",
                "pageHelp.pageNo": "1",
                "pageHelp.beginPage": "1",
                "pageHelp.cacheSize": "1",
                "pageHelp.endPage": "1",
                "pagecache": "false",
                "sqlId": "FUND_LIST",
                "fundType": "00",
                "subClass": "01,02,03,04,05,06,07,08,09,31,32,33,34,35,36,37,38",
            }
        )
        url = f"{SSE_FUND_LIST_URL}?{query}"
        try:
            payload = self._transport(
                url,
                {
                    "Accept": "application/json",
                    "Referer": "https://www.sse.com.cn/",
                    "User-Agent": "Mozilla/5.0",
                },
                self._timeout_seconds,
            )
            if not isinstance(payload, dict):
                raise ValueError("response is not an object")
            rows = payload.get("result")
            if not isinstance(rows, list) or not rows:
                raise ValueError("result is missing or empty")

            subclasses: dict[str, str] = {}
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("result row is not an object")
                symbol = row.get("fundCode")
                subclass = row.get("subClass")
                if (
                    not isinstance(symbol, str)
                    or len(symbol) != 6
                    or not symbol.isascii()
                    or not symbol.isdigit()
                    or not isinstance(subclass, str)
                    or not subclass.strip()
                ):
                    raise ValueError("result row has invalid fundCode or subClass")
                normalized_subclass = subclass.strip()
                existing = subclasses.get(symbol)
                if existing is not None and existing != normalized_subclass:
                    subclasses[symbol] = SSE_SUBCLASS_CONFLICT
                elif existing is None:
                    subclasses[symbol] = normalized_subclass
            return subclasses
        except InstrumentDirectoryProviderError:
            raise
        except Exception:
            raise InstrumentDirectoryProviderError(
                "SSE fund classification source is unavailable or invalid"
            ) from None


@dataclass(frozen=True)
class InstrumentDirectorySnapshot:
    items: list[InstrumentMetadata]
    source_trade_dates: dict[str, date]
    warnings: list[str]
    source_item_counts: dict[str, int] = field(default_factory=dict)


class AkShareInstrumentDirectoryAdapter:
    SH_A_SOURCE = "akshare_sh_a_share"
    SZ_A_SOURCE = "akshare_sz_a_share"
    SH_ETF_SOURCE = "akshare_sse_etf"
    SZ_ETF_SOURCE = "akshare_szse_etf"
    sources = (SH_A_SOURCE, SZ_A_SOURCE, SH_ETF_SOURCE, SZ_ETF_SOURCE)

    def __init__(
        self,
        *,
        akshare_module: Any | None = None,
        sse_fund_classification_source: SseFundClassificationProvider | None = None,
        rule_resolver: InstrumentTradingRuleResolver | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._akshare = akshare_module
        self._sse_fund_classifications = (
            sse_fund_classification_source or SseFundClassificationSource()
        )
        self._resolver = rule_resolver or InstrumentTradingRuleResolver()
        self._now = now or (lambda: datetime.now(UTC))

    def fetch(self, trade_date: date) -> InstrumentDirectorySnapshot:
        checked_at = self._now()
        if checked_at.tzinfo is None or checked_at.utcoffset() is None:
            raise ValueError("directory checked_at must be timezone-aware")
        try:
            module = self._module()
            sh_main = module.stock_info_sh_name_code(symbol="主板A股")
            sh_star = module.stock_info_sh_name_code(symbol="科创板")
            sz_a = module.stock_info_sz_name_code(symbol="A股列表")
            etf_spot = module.fund_etf_spot_em()
            sse_etf = module.fund_etf_scale_sse(date=trade_date.strftime("%Y%m%d"))
            szse_etf = module.fund_etf_scale_szse()
        except Exception as exc:
            raise InstrumentDirectoryProviderError(safe_error_summary(exc)) from None

        classification_warnings: list[str] = []
        try:
            sse_subclasses = self._sse_fund_classifications.fetch()
        except Exception as exc:
            sse_subclasses = {}
            classification_warnings.append(
                "SSE fund classification unavailable; affected ETFs remain watch-only: "
                f"{safe_error_summary(exc)}"
            )

        try:
            return self._normalize_frames(
                trade_date=trade_date,
                checked_at=checked_at,
                sh_main=sh_main,
                sh_star=sh_star,
                sz_a=sz_a,
                etf_spot=etf_spot,
                sse_etf=sse_etf,
                szse_etf=szse_etf,
                sse_subclasses=sse_subclasses,
                initial_warnings=classification_warnings,
            )
        except Exception as exc:
            raise InstrumentDirectoryProviderError(
                f"directory mapping failed: {safe_error_summary(exc)}"
            ) from None

    def _normalize_frames(
        self,
        *,
        trade_date: date,
        checked_at: datetime,
        sh_main: Any,
        sh_star: Any,
        sz_a: Any,
        etf_spot: Any,
        sse_etf: Any,
        szse_etf: Any,
        sse_subclasses: dict[str, str],
        initial_warnings: list[str] | None = None,
    ) -> InstrumentDirectorySnapshot:
        warnings: list[str] = list(initial_warnings or [])
        entries: dict[str, list[InstrumentMetadata]] = defaultdict(list)
        self._append_a_shares(
            entries,
            sh_main,
            exchange=Exchange.SH,
            code_field="证券代码",
            name_field="证券简称",
            source=self.SH_A_SOURCE,
            checked_at=checked_at,
            warnings=warnings,
        )
        self._append_a_shares(
            entries,
            sh_star,
            exchange=Exchange.SH,
            code_field="证券代码",
            name_field="证券简称",
            source=self.SH_A_SOURCE,
            checked_at=checked_at,
            warnings=warnings,
        )
        self._append_a_shares(
            entries,
            sz_a,
            exchange=Exchange.SZ,
            code_field="A股代码",
            name_field="A股简称",
            source=self.SZ_A_SOURCE,
            checked_at=checked_at,
            warnings=warnings,
        )

        spot_names = self._spot_names(etf_spot, warnings=warnings)
        self._append_sse_etfs(
            entries,
            sse_etf,
            spot_names=spot_names,
            subclasses=sse_subclasses,
            checked_at=checked_at,
            warnings=warnings,
        )
        self._append_szse_etfs(
            entries,
            szse_etf,
            spot_names=spot_names,
            checked_at=checked_at,
            warnings=warnings,
        )

        items: list[InstrumentMetadata] = []
        source_item_counts = {
            source: sum(
                item.metadata_source == source
                for candidates in entries.values()
                for item in candidates
            )
            for source in self.sources
        }
        for symbol, candidates in sorted(entries.items()):
            signatures = {
                (
                    item.name,
                    item.exchange,
                    item.instrument_type,
                    item.settlement_cycle,
                    item.price_limit_ratio,
                    item.rule_version,
                    tuple(item.warnings),
                )
                for item in candidates
            }
            if len(signatures) != 1:
                warning = f"instrument {symbol} has conflicting directory metadata"
                warnings.append(warning)
                items.append(
                    self._unknown_metadata(
                        symbol,
                        candidates[0].name,
                        checked_at=checked_at,
                        warning=warning,
                        source="directory_conflict",
                    )
                )
                continue
            items.append(candidates[0])

        return InstrumentDirectorySnapshot(
            items=items,
            source_trade_dates={
                self.SH_A_SOURCE: trade_date,
                self.SZ_A_SOURCE: trade_date,
                self.SH_ETF_SOURCE: self._source_trade_date(
                    sse_etf,
                    "统计日期",
                    fallback=trade_date,
                ),
                self.SZ_ETF_SOURCE: trade_date,
            },
            warnings=warnings,
            source_item_counts=source_item_counts,
        )

    def _module(self) -> Any:
        if self._akshare is not None:
            return self._akshare
        import akshare  # type: ignore[import-not-found]

        return akshare

    def _append_a_shares(
        self,
        entries: dict[str, list[InstrumentMetadata]],
        frame: Any,
        *,
        exchange: Exchange,
        code_field: str,
        name_field: str,
        source: str,
        checked_at: datetime,
        warnings: list[str],
    ) -> None:
        rule = self._resolver.resolve_a_share()
        for _, row in frame.iterrows():
            identity = self._identity(row, code_field, name_field, source, warnings)
            if identity is None:
                continue
            symbol, name, name_valid = identity
            if not name_valid:
                warning = f"{source} returned a missing name for {symbol}"
                entries[symbol].append(
                    self._unknown_metadata(
                        symbol,
                        name,
                        checked_at=checked_at,
                        warning=warning,
                        source=source,
                    )
                )
                continue
            entries[symbol].append(
                InstrumentMetadata(
                    symbol=symbol,
                    name=name,
                    exchange=exchange,
                    instrument_type=InstrumentType.A_SHARE,
                    settlement_cycle=rule.settlement_cycle,
                    price_limit_ratio=None,
                    metadata_source=source,
                    metadata_checked_at=checked_at,
                    rule_version=rule.rule_version,
                )
            )

    @staticmethod
    def _spot_names(frame: Any, *, warnings: list[str]) -> dict[str, str | None]:
        result: dict[str, str | None] = {}
        for _, row in frame.iterrows():
            identity = AkShareInstrumentDirectoryAdapter._identity(
                row, "代码", "名称", "akshare_etf_spot", warnings
            )
            if identity is None:
                continue
            symbol, name, name_valid = identity
            if not name_valid:
                result[symbol] = None
                continue
            if symbol in result and result[symbol] != name:
                warnings.append(f"ETF spot directory has conflicting names for {symbol}")
                result[symbol] = None
                continue
            result[symbol] = name
        return result

    def _append_sse_etfs(
        self,
        entries: dict[str, list[InstrumentMetadata]],
        frame: Any,
        *,
        spot_names: dict[str, str | None],
        subclasses: dict[str, str],
        checked_at: datetime,
        warnings: list[str],
    ) -> None:
        for _, row in frame.iterrows():
            identity = self._identity(
                row, "基金代码", "基金简称", self.SH_ETF_SOURCE, warnings
            )
            if identity is None:
                continue
            symbol, name, name_valid = identity
            spot_name = spot_names.get(symbol)
            if not name_valid or symbol not in spot_names or spot_name is None:
                warning = (
                    f"ETF {symbol} cannot be verified by the public ETF directory"
                )
                warnings.append(warning)
                entries[symbol].append(
                    self._unknown_metadata(
                        symbol,
                        spot_name or name,
                        checked_at=checked_at,
                        warning=warning,
                        source=self.SH_ETF_SOURCE,
                    )
                )
                continue
            if not self._compatible_etf_names(spot_name, name):
                warning = f"ETF {symbol} has conflicting directory names"
                warnings.append(warning)
                entries[symbol].append(
                    self._unknown_metadata(
                        symbol,
                        name,
                        checked_at=checked_at,
                        warning=warning,
                        source=self.SH_ETF_SOURCE,
                    )
                )
                continue
            rule = self._resolver.resolve_sse_etf(subclasses.get(symbol))
            if subclasses.get(symbol) == SSE_SUBCLASS_CONFLICT:
                rule = replace(
                    rule,
                    warning="SSE FUND_LIST contains conflicting subClass",
                )
            entries[symbol].append(
                self._etf_metadata(
                    symbol,
                    name,
                    exchange=Exchange.SH,
                    source=self.SH_ETF_SOURCE,
                    checked_at=checked_at,
                    settlement_cycle=rule.settlement_cycle,
                    rule_version=rule.rule_version,
                    warning=rule.warning,
                )
            )

    def _append_szse_etfs(
        self,
        entries: dict[str, list[InstrumentMetadata]],
        frame: Any,
        *,
        spot_names: dict[str, str | None],
        checked_at: datetime,
        warnings: list[str],
    ) -> None:
        for _, row in frame.iterrows():
            fund_category = self._optional_text(row, "基金类别")
            if fund_category != "ETF":
                continue
            identity = self._identity(
                row, "基金代码", "基金简称", self.SZ_ETF_SOURCE, warnings
            )
            if identity is None:
                continue
            symbol, name, name_valid = identity
            spot_name = spot_names.get(symbol)
            if not name_valid or symbol not in spot_names or spot_name is None:
                warning = (
                    f"ETF {symbol} cannot be verified by the public ETF directory"
                )
                warnings.append(warning)
                entries[symbol].append(
                    self._unknown_metadata(
                        symbol,
                        spot_name or name,
                        checked_at=checked_at,
                        warning=warning,
                        source=self.SZ_ETF_SOURCE,
                    )
                )
                continue
            if not self._compatible_etf_names(spot_name, name):
                warning = f"ETF {symbol} has conflicting directory names"
                warnings.append(warning)
                entries[symbol].append(
                    self._unknown_metadata(
                        symbol,
                        name,
                        checked_at=checked_at,
                        warning=warning,
                        source=self.SZ_ETF_SOURCE,
                    )
                )
                continue
            rule = self._resolver.resolve_szse_etf(
                fund_category,
                self._optional_text(row, "投资类别"),
            )
            entries[symbol].append(
                self._etf_metadata(
                    symbol,
                    name,
                    exchange=Exchange.SZ,
                    source=self.SZ_ETF_SOURCE,
                    checked_at=checked_at,
                    settlement_cycle=rule.settlement_cycle,
                    rule_version=rule.rule_version,
                    warning=rule.warning,
                )
            )

    @staticmethod
    def _etf_metadata(
        symbol: str,
        name: str,
        *,
        exchange: Exchange,
        source: str,
        checked_at: datetime,
        settlement_cycle: SettlementCycle,
        rule_version: str,
        warning: str,
    ) -> InstrumentMetadata:
        return InstrumentMetadata(
            symbol=symbol,
            name=name,
            exchange=exchange,
            instrument_type=InstrumentType.ETF,
            settlement_cycle=settlement_cycle,
            price_limit_ratio=None,
            metadata_source=source,
            metadata_checked_at=checked_at,
            rule_version=rule_version,
            warnings=[] if not warning else [warning],
        )

    @staticmethod
    def _unknown_metadata(
        symbol: str,
        name: str,
        *,
        checked_at: datetime,
        warning: str,
        source: str,
    ) -> InstrumentMetadata:
        return InstrumentMetadata(
            symbol=symbol,
            name=name,
            exchange=None,
            instrument_type=InstrumentType.UNKNOWN,
            settlement_cycle=SettlementCycle.UNKNOWN,
            price_limit_ratio=None,
            metadata_source=source,
            metadata_checked_at=checked_at,
            rule_version="unverified-v1",
            warnings=[warning],
        )

    @staticmethod
    def _identity(
        row: Any,
        code_field: str,
        name_field: str,
        source: str,
        warnings: list[str],
    ) -> tuple[str, str, bool] | None:
        symbol = str(row.get(code_field, "")).strip()
        name = str(row.get(name_field, "")).strip()
        if len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit():
            warnings.append(f"{source} returned an invalid instrument code")
            return None
        if not name or name.lower() == "nan":
            warnings.append(f"{source} returned a missing name for {symbol}")
            return symbol, symbol, False
        return symbol, name, True

    @staticmethod
    def _optional_text(row: Any, field: str) -> str | None:
        value = row.get(field)
        if value is None:
            return None
        text = str(value).strip()
        return None if not text or text.lower() == "nan" else text

    @staticmethod
    def _compatible_etf_names(first: str, second: str) -> bool:
        left = "".join(first.split()).upper()
        right = "".join(second.split()).upper()
        if left == right:
            return True
        shorter, longer = sorted((left, right), key=len)
        return "ETF" in shorter and longer.startswith(shorter)

    @staticmethod
    def _source_trade_date(frame: Any, field: str, *, fallback: date) -> date:
        values: list[date] = []
        for _, row in frame.iterrows():
            value = row.get(field)
            if value is None:
                continue
            if isinstance(value, datetime):
                values.append(value.date())
                continue
            if isinstance(value, date):
                values.append(value)
                continue
            text = str(value).strip()
            if not text or text.lower() in {"nan", "nat", "<na>"}:
                continue
            values.append(date.fromisoformat(text[:10]))
        return max(values, default=fallback)


def _urllib_json_get(
    url: str,
    headers: dict[str, str],
    timeout: float,
) -> object:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        content = response.read()
    try:
        return json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("SSE fund classification response is not valid JSON") from None
