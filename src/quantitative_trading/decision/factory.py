from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime

from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.audit.service import AuditService
from quantitative_trading.config import Settings
from quantitative_trading.decision.workflow import DecisionWorkflow
from quantitative_trading.email.outbox import (
    EmailDeliveryRepository,
    EmailDeliveryService,
)
from quantitative_trading.email.repository import SmtpSettingsRepository
from quantitative_trading.email.service import SmtplibEmailSender, SmtpSettingsService
from quantitative_trading.market.adapters import (
    AkShareDailyBarProvider,
    AkShareIntradayProvider,
    AkShareMoneyFlowProvider,
)
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import DailyBar, DailyMoneyFlow, MinuteBar
from quantitative_trading.market.providers import (
    AkShareMarketProvider,
    DisabledMarketProvider,
)
from quantitative_trading.notification.dispatcher import NotificationDispatcher
from quantitative_trading.notification.jsonl import JsonlNotificationWriter
from quantitative_trading.notification.local_alert import LocalAlertDispatcher
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.notification.service import NotificationService


class DisabledHeavyMarketProvider:
    """Network-free provider used when public market fetching is disabled."""

    def get_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        adjustment: str,
    ) -> Sequence[DailyBar]:
        return ()

    def get_daily_money_flow(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> Sequence[DailyMoneyFlow]:
        return ()

    def get_minute_bars(
        self,
        symbol: str,
        trade_date: date,
        interval: str,
    ) -> Sequence[MinuteBar]:
        return ()


def build_decision_workflow(
    connection: sqlite3.Connection,
    settings: Settings,
    *,
    now: Callable[[], datetime] | None = None,
) -> DecisionWorkflow:
    clock = now or (lambda: datetime.now(UTC))
    calendar = XSHGTradingCalendar()
    provider_name = settings.market_provider.strip().lower()
    if provider_name != "akshare":
        raise ValueError(f"unsupported market provider: {settings.market_provider}")

    if settings.enable_market_fetch:
        quote_provider = AkShareMarketProvider(now=clock)
        daily_provider = AkShareDailyBarProvider(calendar=calendar, now=clock)
        money_flow_provider = AkShareMoneyFlowProvider(calendar=calendar, now=clock)
        intraday_provider = AkShareIntradayProvider(calendar=calendar, now=clock)
    else:
        quote_provider = DisabledMarketProvider(now=clock)
        disabled = DisabledHeavyMarketProvider()
        daily_provider = disabled
        money_flow_provider = disabled
        intraday_provider = disabled

    return DecisionWorkflow(
        connection,
        calendar=calendar,
        quote_provider=quote_provider,
        daily_provider=daily_provider,
        money_flow_provider=money_flow_provider,
        intraday_provider=intraday_provider,
        now=clock,
        stale_trading_minutes=settings.market_stale_trading_minutes,
        notification_dispatcher=build_notification_dispatcher(connection, settings),
    )


def build_notification_dispatcher(
    connection: sqlite3.Connection,
    settings: Settings,
) -> NotificationDispatcher:
    smtp_repository = SmtpSettingsRepository(connection)
    smtp_settings = smtp_repository.get()
    configured_secrets = (
        (smtp_settings.password,)
        if smtp_settings is not None and smtp_settings.password
        else ()
    )
    audit_repository = AuditLogRepository(connection)
    notification_service = NotificationService(NotificationRepository(connection))
    audit_service = AuditService(
        audit_repository,
        configured_secret_texts=configured_secrets,
    )
    jsonl_writer = JsonlNotificationWriter(
        settings,
        configured_secret_texts=configured_secrets,
    )
    local_alert_dispatcher = LocalAlertDispatcher(
        notification_service=notification_service,
        audit_service=audit_service,
        jsonl_writer=jsonl_writer,
        configured_secret_texts=configured_secrets,
    )
    email_service = EmailDeliveryService(
        EmailDeliveryRepository(connection),
        smtp_repository,
        SmtplibEmailSender(),
        audit_repository=audit_repository,
        dead_delivery_alert=local_alert_dispatcher.dispatch_dead_email,
    )
    return NotificationDispatcher(
        notification_service=notification_service,
        audit_service=audit_service,
        jsonl_writer=jsonl_writer,
        email_service=email_service,
        smtp_settings_service=SmtpSettingsService(smtp_repository),
        local_alert_dispatcher=local_alert_dispatcher,
    )
