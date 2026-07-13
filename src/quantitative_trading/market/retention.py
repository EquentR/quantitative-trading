from __future__ import annotations

from datetime import date

from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.repositories import MinuteBarRepository


class MinuteBarRetentionService:
    RETAIN_TRADING_DAYS = 20

    def __init__(
        self,
        repository: MinuteBarRepository,
        calendar: XSHGTradingCalendar,
    ) -> None:
        self.repository = repository
        self.calendar = calendar

    def cleanup(self, as_of: date) -> int:
        retained = self.calendar.sessions_ending(as_of, self.RETAIN_TRADING_DAYS)
        return self.repository.delete_before(retained[0])
