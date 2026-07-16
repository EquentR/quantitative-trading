from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from quantitative_trading.api.dependencies import ApiContainer
from quantitative_trading.instrument.adapters import AkShareInstrumentDirectoryAdapter
from quantitative_trading.instrument.directory import (
    InstrumentDirectoryService,
    latest_completed_directory_trade_date,
)
from quantitative_trading.instrument.repository import (
    InstrumentCatalogStateRepository,
    InstrumentRepository,
)
from quantitative_trading.market.calendar import XSHGTradingCalendar


def instrument_directory_service(connection, container: ApiContainer):  # noqa: ANN001, ANN201
    adapter = (
        container.instrument_directory_adapter or AkShareInstrumentDirectoryAdapter()
    )
    return InstrumentDirectoryService(
        InstrumentRepository(connection),
        InstrumentCatalogStateRepository(connection),
        adapter,
        timezone=ZoneInfo(container.settings.timezone),
    )


def instrument_directory_trade_date(container: ApiContainer):  # noqa: ANN201
    return latest_completed_directory_trade_date(
        datetime.now(ZoneInfo(container.settings.timezone)),
        XSHGTradingCalendar(),
    )
