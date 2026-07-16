from datetime import UTC, date, datetime

import pandas as pd

from quantitative_trading.instrument.adapters import AkShareInstrumentDirectoryAdapter
from quantitative_trading.instrument.models import InstrumentMetadata


class _SseClassifications:
    def fetch(self) -> dict[str, str]:
        return {"512480": "03"}


class _EtfNameVariantDirectory:
    def __init__(self, trade_date: date) -> None:
        self.trade_date = trade_date

    def stock_info_sh_name_code(self, *, symbol: str) -> pd.DataFrame:
        if symbol == "主板A股":
            return pd.DataFrame([{"证券代码": "600000", "证券简称": "浦发银行"}])
        return pd.DataFrame([{"证券代码": "688001", "证券简称": "华兴源创"}])

    def stock_info_sz_name_code(self, *, symbol: str) -> pd.DataFrame:
        return pd.DataFrame([{"A股代码": "000001", "A股简称": "平安银行"}])

    def fund_etf_spot_em(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"代码": "512480", "名称": "半导体ETF国联安"},
                {"代码": "159915", "名称": "创业板ETF"},
            ]
        )

    def fund_etf_scale_sse(self, *, date: str) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "基金代码": "512480",
                    "基金简称": "半导体",
                    "ETF类型": "跨市",
                    "统计日期": self.trade_date,
                }
            ]
        )

    def fund_etf_scale_szse(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "基金代码": "159915",
                    "基金简称": "创业板ETF",
                    "基金类别": "ETF",
                    "投资类别": "股票基金",
                    "上市日期": date(2011, 12, 9),
                }
            ]
        )


def etf_name_variant_metadata(
    *,
    now: datetime = datetime(2026, 7, 15, 2, 0, tzinfo=UTC),
    trade_date: date = date(2026, 7, 14),
) -> InstrumentMetadata:
    snapshot = AkShareInstrumentDirectoryAdapter(
        akshare_module=_EtfNameVariantDirectory(trade_date),
        sse_fund_classification_source=_SseClassifications(),
        now=lambda: now,
    ).fetch(trade_date)
    return next(item for item in snapshot.items if item.symbol == "512480")
