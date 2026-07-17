from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Generic, TypeVar

from quantitative_trading.market.models import (
    CaptureRunAlreadyActiveError,
    DailyBar,
    DailyMoneyFlow,
    HistorySnapshot,
    IntradayStrengthSnapshot,
    MarketCaptureResult,
    MarketCaptureRun,
    MinuteBar,
    MoneyFlowSnapshot,
    MarketInputSnapshot,
)
from quantitative_trading.sanitization import redact_sensitive_text


@dataclass(frozen=True)
class StoredDailyBar:
    id: int
    bar: DailyBar


@dataclass(frozen=True)
class StoredHistorySnapshot:
    snapshot_id: int
    snapshot: HistorySnapshot


@dataclass(frozen=True)
class StoredMoneyFlow:
    id: int
    flow: DailyMoneyFlow


class DailyBarRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, bar: DailyBar, *, commit: bool = True) -> int:
        self.connection.execute(
            """INSERT OR IGNORE INTO daily_bars
               (content_hash, symbol, trade_date, adjustment,
                open, high, low, close, volume, amount,
                source, source_updated_at, fetched_at, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bar.content_hash,
                bar.symbol,
                bar.trade_date.isoformat(),
                bar.adjustment,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                bar.amount,
                bar.source,
                None if bar.source_updated_at is None else bar.source_updated_at.isoformat(),
                bar.fetched_at.isoformat(),
                bar.model_dump_json(exclude={"content_hash"}),
            ),
        )
        row = self.connection.execute(
            """SELECT id FROM daily_bars
               WHERE symbol=? AND trade_date=? AND adjustment=? AND content_hash=?""",
            (bar.symbol, bar.trade_date.isoformat(), bar.adjustment, bar.content_hash),
        ).fetchone()
        if commit:
            self.connection.commit()
        return int(row["id"])

    def get(self, fact_id: int) -> StoredDailyBar | None:
        row = self.connection.execute(
            "SELECT id, payload_json FROM daily_bars WHERE id=?", (fact_id,)
        ).fetchone()
        if row is None:
            return None
        return StoredDailyBar(
            int(row["id"]), DailyBar.model_validate_json(row["payload_json"])
        )

    def current(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        since: date | None = None,
        through: date | None = None,
    ) -> list[StoredDailyBar]:
        range_filter = ""
        params: list[object] = [symbol]
        if since is not None:
            range_filter += " AND trade_date>=?"
            params.append(since.isoformat())
        if through is not None:
            range_filter += " AND trade_date<=?"
            params.append(through.isoformat())
        query = f"""SELECT id, payload_json FROM daily_bars
                    WHERE id IN (
                      SELECT MAX(id) FROM daily_bars
                      WHERE symbol=? AND adjustment='forward'{range_filter}
                      GROUP BY trade_date
                    ) ORDER BY trade_date DESC"""
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = list(reversed(self.connection.execute(query, params).fetchall()))
        return [
            StoredDailyBar(
                int(row["id"]), DailyBar.model_validate_json(row["payload_json"])
            )
            for row in rows
        ]


class MoneyFlowRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, flow: DailyMoneyFlow, *, commit: bool = True) -> int:
        self.connection.execute(
            """INSERT OR IGNORE INTO daily_money_flows
               (content_hash, symbol, trade_date,
                main_net_amount, main_net_pct,
                super_large_net_amount, super_large_net_pct,
                large_net_amount, large_net_pct,
                medium_net_amount, medium_net_pct,
                small_net_amount, small_net_pct,
                source, source_updated_at, fetched_at, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                flow.content_hash,
                flow.symbol,
                flow.trade_date.isoformat(),
                flow.main_net_amount,
                flow.main_net_pct,
                flow.super_large_net_amount,
                flow.super_large_net_pct,
                flow.large_net_amount,
                flow.large_net_pct,
                flow.medium_net_amount,
                flow.medium_net_pct,
                flow.small_net_amount,
                flow.small_net_pct,
                flow.source,
                None if flow.source_updated_at is None else flow.source_updated_at.isoformat(),
                flow.fetched_at.isoformat(),
                flow.model_dump_json(exclude={"content_hash"}),
            ),
        )
        row = self.connection.execute(
            """SELECT id FROM daily_money_flows
               WHERE symbol=? AND trade_date=? AND content_hash=?""",
            (flow.symbol, flow.trade_date.isoformat(), flow.content_hash),
        ).fetchone()
        if commit:
            self.connection.commit()
        return int(row["id"])

    def get(self, fact_id: int) -> StoredMoneyFlow | None:
        row = self.connection.execute(
            "SELECT id, payload_json FROM daily_money_flows WHERE id=?", (fact_id,)
        ).fetchone()
        if row is None:
            return None
        return StoredMoneyFlow(
            int(row["id"]), DailyMoneyFlow.model_validate_json(row["payload_json"])
        )

    def current(self, symbol: str, *, limit: int | None = None) -> list[StoredMoneyFlow]:
        query = """SELECT id, payload_json FROM daily_money_flows
                   WHERE id IN (
                     SELECT MAX(id) FROM daily_money_flows
                     WHERE symbol=? GROUP BY trade_date
                   ) ORDER BY trade_date DESC"""
        params: list[object] = [symbol]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = list(reversed(self.connection.execute(query, params).fetchall()))
        return [
            StoredMoneyFlow(
                int(row["id"]),
                DailyMoneyFlow.model_validate_json(row["payload_json"]),
            )
            for row in rows
        ]


SnapshotT = TypeVar("SnapshotT", HistorySnapshot, MoneyFlowSnapshot)
MemberT = TypeVar("MemberT", StoredDailyBar, StoredMoneyFlow)


class _DatasetSnapshotRepository(Generic[SnapshotT, MemberT]):
    snapshot_table: str
    member_table: str
    member_column: str
    fact_table: str
    snapshot_model: type[SnapshotT]
    fact_model: type
    fact_value_name: str

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(
        self,
        snapshot: SnapshotT,
        member_ids: list[int],
        *,
        commit: bool = True,
    ) -> int:
        if snapshot.row_count != len(member_ids):
            raise sqlite3.IntegrityError("snapshot row_count does not match members")
        rows = []
        facts: list[DailyBar | DailyMoneyFlow] = []
        for member_id in member_ids:
            row = self.connection.execute(
                f"SELECT symbol, payload_json FROM {self.fact_table} WHERE id=?", (member_id,)
            ).fetchone()
            if row is None or row["symbol"] != snapshot.symbol:
                raise sqlite3.IntegrityError("snapshot member symbol mismatch")
            rows.append(row)
            model = DailyBar if self.fact_table == "daily_bars" else DailyMoneyFlow
            facts.append(model.model_validate_json(row["payload_json"]))
        dates = [fact.trade_date for fact in facts]
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise sqlite3.IntegrityError("snapshot member order is invalid")
        expected_start = None if not dates else dates[0]
        expected_end = None if not dates else dates[-1]
        if snapshot.data_start != expected_start or snapshot.data_end != expected_end:
            raise sqlite3.IntegrityError("snapshot range does not match members")
        hashes = [fact.content_hash for fact in facts]
        if snapshot.content_digest != content_digest(hashes):
            raise sqlite3.IntegrityError("snapshot content digest does not match members")
        if isinstance(snapshot, HistorySnapshot) and any(
            fact.adjustment != snapshot.adjustment
            for fact in facts
            if isinstance(fact, DailyBar)
        ):
            raise sqlite3.IntegrityError("history snapshot adjustment mismatch")
        started_transaction = not self.connection.in_transaction
        if started_transaction:
            self.connection.execute("BEGIN")
        self.connection.execute("SAVEPOINT dataset_snapshot_save")
        try:
            columns = (
                "run_id,symbol,data_start,data_end,row_count,content_digest,"
                "status,warning,fetched_at,payload_json"
            )
            values = [
                snapshot.run_id,
                snapshot.symbol,
                None if snapshot.data_start is None else snapshot.data_start.isoformat(),
                None if snapshot.data_end is None else snapshot.data_end.isoformat(),
                snapshot.row_count,
                snapshot.content_digest,
                snapshot.status.value,
                snapshot.warning,
                snapshot.fetched_at.isoformat(),
                snapshot.model_dump_json(),
            ]
            if isinstance(snapshot, HistorySnapshot):
                columns = (
                    "run_id,symbol,adjustment,data_start,data_end,row_count,"
                    "content_digest,status,warning,fetched_at,payload_json"
                )
                values.insert(2, snapshot.adjustment)
            cursor = self.connection.execute(
                f"INSERT INTO {self.snapshot_table} ({columns}) "
                f"VALUES ({','.join('?' for _ in values)})",
                values,
            )
            snapshot_id = int(cursor.lastrowid)
            self.connection.executemany(
                f"INSERT INTO {self.member_table} "
                f"(snapshot_id,sequence,{self.member_column}) VALUES (?,?,?)",
                [
                    (snapshot_id, sequence, member_id)
                    for sequence, member_id in enumerate(member_ids)
                ],
            )
            self.connection.execute("RELEASE SAVEPOINT dataset_snapshot_save")
            if commit:
                self.connection.commit()
            return snapshot_id
        except BaseException:
            self.connection.execute("ROLLBACK TO SAVEPOINT dataset_snapshot_save")
            self.connection.execute("RELEASE SAVEPOINT dataset_snapshot_save")
            if started_transaction:
                self.connection.rollback()
            raise

    def get(self, snapshot_id: int) -> SnapshotT | None:
        row = self.connection.execute(
            f"SELECT payload_json FROM {self.snapshot_table} WHERE id=?", (snapshot_id,)
        ).fetchone()
        return None if row is None else self.snapshot_model.model_validate_json(row["payload_json"])

    def members(self, snapshot_id: int) -> list[MemberT]:
        rows = self.connection.execute(
            f"""SELECT f.id, f.payload_json FROM {self.member_table} m
                JOIN {self.fact_table} f ON f.id=m.{self.member_column}
                WHERE m.snapshot_id=? ORDER BY m.sequence""",
            (snapshot_id,),
        ).fetchall()
        wrapper = StoredDailyBar if self.fact_table == "daily_bars" else StoredMoneyFlow
        model = DailyBar if self.fact_table == "daily_bars" else DailyMoneyFlow
        return [
            wrapper(int(row["id"]), model.model_validate_json(row["payload_json"]))
            for row in rows
        ]  # type: ignore[misc]


class HistorySnapshotRepository(_DatasetSnapshotRepository[HistorySnapshot, StoredDailyBar]):
    snapshot_table = "history_snapshots"
    member_table = "history_snapshot_members"
    member_column = "daily_bar_id"
    fact_table = "daily_bars"
    snapshot_model = HistorySnapshot

    def latest_usable_for_symbol(
        self,
        symbol: str,
        *,
        as_of: date,
        expected_rows: int,
    ) -> StoredHistorySnapshot | None:
        rows = self.connection.execute(
            """SELECT id, payload_json FROM history_snapshots
               WHERE symbol=? ORDER BY id DESC""",
            (symbol,),
        ).fetchall()
        for row in rows:
            snapshot_id = int(row["id"])
            snapshot = HistorySnapshot.model_validate_json(row["payload_json"])
            if not snapshot.is_usable(as_of=as_of, expected_rows=expected_rows):
                continue
            self._validate_usable_snapshot_members(snapshot_id, snapshot, symbol=symbol)
            return StoredHistorySnapshot(snapshot_id=snapshot_id, snapshot=snapshot)
        return None

    def usable_by_id_for_symbol(
        self,
        snapshot_id: int,
        symbol: str,
        *,
        as_of: date,
        expected_rows: int,
    ) -> StoredHistorySnapshot | None:
        row = self.connection.execute(
            "SELECT payload_json FROM history_snapshots WHERE id=? AND symbol=?",
            (snapshot_id, symbol),
        ).fetchone()
        if row is None:
            return None
        snapshot = HistorySnapshot.model_validate_json(row["payload_json"])
        if not snapshot.is_usable(as_of=as_of, expected_rows=expected_rows):
            return None
        self._validate_usable_snapshot_members(snapshot_id, snapshot, symbol=symbol)
        return StoredHistorySnapshot(snapshot_id=snapshot_id, snapshot=snapshot)

    def _validate_usable_snapshot_members(
        self,
        snapshot_id: int,
        snapshot: HistorySnapshot,
        *,
        symbol: str,
    ) -> None:
        sequences = [
            int(row["sequence"])
            for row in self.connection.execute(
                """SELECT sequence FROM history_snapshot_members
                   WHERE snapshot_id=? ORDER BY sequence""",
                (snapshot_id,),
            ).fetchall()
        ]
        if len(sequences) != snapshot.row_count:
            raise sqlite3.IntegrityError("history snapshot members do not match payload")
        if sequences != list(range(snapshot.row_count)):
            raise sqlite3.IntegrityError("history snapshot member sequence is invalid")
        members = self.members(snapshot_id)
        if snapshot.symbol != symbol or len(members) != snapshot.row_count:
            raise sqlite3.IntegrityError("history snapshot members do not match payload")
        dates = [member.bar.trade_date for member in members]
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise sqlite3.IntegrityError("history snapshot members are not ordered")
        if any(
            member.bar.symbol != snapshot.symbol
            or member.bar.adjustment != snapshot.adjustment
            for member in members
        ):
            raise sqlite3.IntegrityError("history snapshot members have invalid scope")
        if (
            snapshot.data_start != (None if not dates else dates[0])
            or snapshot.data_end != (None if not dates else dates[-1])
        ):
            raise sqlite3.IntegrityError("history snapshot members have invalid range")
        if snapshot.content_digest != content_digest(
            [member.bar.content_hash for member in members]
        ):
            raise sqlite3.IntegrityError("history snapshot members have invalid digest")


class MoneyFlowSnapshotRepository(_DatasetSnapshotRepository[MoneyFlowSnapshot, StoredMoneyFlow]):
    snapshot_table = "money_flow_snapshots"
    member_table = "money_flow_snapshot_members"
    member_column = "money_flow_id"
    fact_table = "daily_money_flows"
    snapshot_model = MoneyFlowSnapshot


class MinuteBarRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def upsert_many(self, bars: list[MinuteBar], *, commit: bool = True) -> int:
        self.connection.executemany(
            """INSERT INTO minute_bars
               (symbol,trade_date,minute,open,high,low,close,volume,amount,
                source,source_updated_at,fetched_at,payload_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(symbol,minute) DO UPDATE SET
                 trade_date=excluded.trade_date, open=excluded.open,
                 high=excluded.high, low=excluded.low, close=excluded.close,
                 volume=excluded.volume, amount=excluded.amount,
                 source=excluded.source,
                 source_updated_at=excluded.source_updated_at,
                 fetched_at=excluded.fetched_at, payload_json=excluded.payload_json""",
            [
                (
                    bar.symbol,
                    bar.trade_date.isoformat(),
                    bar.minute.isoformat(),
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                    bar.amount,
                    bar.source,
                    None if bar.source_updated_at is None else bar.source_updated_at.isoformat(),
                    bar.fetched_at.isoformat(),
                    bar.model_dump_json(),
                )
                for bar in bars
            ],
        )
        if commit:
            self.connection.commit()
        return len(bars)

    def for_trade_date(self, symbol: str, trade_date: date) -> list[MinuteBar]:
        rows = self.connection.execute(
            """SELECT payload_json FROM minute_bars
               WHERE symbol=? AND trade_date=? ORDER BY minute""",
            (symbol, trade_date.isoformat()),
        ).fetchall()
        return [MinuteBar.model_validate_json(row["payload_json"]) for row in rows]

    def trade_dates(self, symbol: str) -> list[date]:
        return [
            date.fromisoformat(row["trade_date"])
            for row in self.connection.execute(
                "SELECT DISTINCT trade_date FROM minute_bars WHERE symbol=? ORDER BY trade_date",
                (symbol,),
            ).fetchall()
        ]

    def delete_before(self, cutoff: date) -> int:
        cursor = self.connection.execute(
            "DELETE FROM minute_bars WHERE trade_date < ?", (cutoff.isoformat(),)
        )
        self.connection.commit()
        return cursor.rowcount


class IntradayStrengthSnapshotRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, snapshot: IntradayStrengthSnapshot) -> int:
        cursor = self.connection.execute(
            """INSERT INTO intraday_strength_snapshots
               (run_id,symbol,trade_date,label,confidence,degraded,direction_sum,
                minute_volume_ratio,last_minute,data_coverage,rule_version,source,
                data_time,fetched_at,payload_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                snapshot.run_id,
                snapshot.symbol,
                snapshot.trade_date.isoformat(),
                snapshot.label.value,
                snapshot.confidence.value,
                int(snapshot.degraded),
                snapshot.direction_sum,
                snapshot.minute_volume_ratio,
                None if snapshot.last_minute is None else snapshot.last_minute.isoformat(),
                snapshot.data_coverage,
                snapshot.rule_version,
                snapshot.source,
                snapshot.data_time.isoformat(),
                snapshot.fetched_at.isoformat(),
                snapshot.model_dump_json(),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def get(self, snapshot_id: int) -> IntradayStrengthSnapshot | None:
        row = self.connection.execute(
            "SELECT payload_json FROM intraday_strength_snapshots WHERE id=?", (snapshot_id,)
        ).fetchone()
        if row is None:
            return None
        return IntradayStrengthSnapshot.model_validate_json(row["payload_json"])

    def latest_for_symbol(self, symbol: str) -> IntradayStrengthSnapshot | None:
        row = self.connection.execute(
            """SELECT payload_json FROM intraday_strength_snapshots
               WHERE symbol=? ORDER BY id DESC LIMIT 1""",
            (symbol,),
        ).fetchone()
        if row is None:
            return None
        return IntradayStrengthSnapshot.model_validate_json(row["payload_json"])


class MarketCaptureRunRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get_or_create(self, run: MarketCaptureRun) -> tuple[MarketCaptureRun, bool]:
        existing = self._get_by_idempotency_key(run.idempotency_key)
        if existing is not None:
            return existing, False
        try:
            self.connection.execute(
                """INSERT INTO market_capture_runs (
                     run_id, workflow_type, mode, trade_date,
                     effective_trade_date, history_cutoff_date,
                     period_start, period_end, requested_symbol_scope_json,
                     lease_expires_at,
                     idempotency_key, status, started_at, finished_at,
                     requested_symbols, processed_symbols, provider_calls,
                     provider_duration_ms, rows_received, rows_written, cleaned_rows,
                     plan_count, recommendation_count, notification_count,
                     email_outbox_count, retry_count, warning_count, failure_count,
                     error_summary
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                self._values(run),
            )
            self.connection.commit()
        except sqlite3.IntegrityError:
            existing = self._get_by_idempotency_key(run.idempotency_key)
            if existing is not None:
                return existing, False
            active = self.active_for_workflow(run.workflow_type)
            if active is not None:
                raise CaptureRunAlreadyActiveError(active.run_id) from None
            raise
        return (
            run.model_copy(
                update={"error_summary": redact_sensitive_text(run.error_summary)}
            ),
            True,
        )

    def get(self, run_id: str) -> MarketCaptureRun | None:
        row = self.connection.execute(
            "SELECT * FROM market_capture_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        return None if row is None else self._from_row(row)

    def active_for_workflow(self, workflow_type: str) -> MarketCaptureRun | None:
        row = self.connection.execute(
            """SELECT * FROM market_capture_runs
               WHERE workflow_type=? AND status='running'
               ORDER BY started_at DESC LIMIT 1""",
            (workflow_type,),
        ).fetchone()
        return None if row is None else self._from_row(row)

    def fail_expired_workflow_runs(
        self,
        workflow_type: str,
        *,
        started_before: datetime,
        finished_at: datetime,
    ) -> int:
        error_summary = f"{workflow_type} run lease expired before retry"
        cursor = self.connection.execute(
            """UPDATE market_capture_runs SET
                 status='failed', finished_at=?, failure_count=failure_count+1,
                 error_summary=?
               WHERE workflow_type=? AND status='running' AND started_at <= ?""",
            (
                finished_at.isoformat(),
                error_summary,
                workflow_type,
                started_before.isoformat(),
            ),
        )
        self.connection.commit()
        return cursor.rowcount

    def update(self, run: MarketCaptureRun) -> None:
        cursor = self.connection.execute(
            """UPDATE market_capture_runs SET
                 workflow_type=?, mode=?, trade_date=?,
                 effective_trade_date=?, history_cutoff_date=?,
                 period_start=?, period_end=?, requested_symbol_scope_json=?,
                 lease_expires_at=?,
                 idempotency_key=?, status=?, started_at=?, finished_at=?,
                 requested_symbols=?, processed_symbols=?, provider_calls=?,
                 provider_duration_ms=?, rows_received=?, rows_written=?, cleaned_rows=?,
                 plan_count=?, recommendation_count=?, notification_count=?,
                 email_outbox_count=?, retry_count=?, warning_count=?, failure_count=?,
                 error_summary=?
               WHERE run_id=?""",
            (*self._values(run)[1:], run.run_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"market capture run not found: {run.run_id}")
        self.connection.commit()

    def update_claimed(
        self,
        run: MarketCaptureRun,
        *,
        claim_started_at: datetime,
    ) -> None:
        cursor = self.connection.execute(
            """UPDATE market_capture_runs SET
                 workflow_type=?, mode=?, trade_date=?,
                 effective_trade_date=?, history_cutoff_date=?,
                 period_start=?, period_end=?, requested_symbol_scope_json=?,
                 lease_expires_at=?,
                 idempotency_key=?, status=?, started_at=?, finished_at=?,
                 requested_symbols=?, processed_symbols=?, provider_calls=?,
                 provider_duration_ms=?, rows_received=?, rows_written=?, cleaned_rows=?,
                 plan_count=?, recommendation_count=?, notification_count=?,
                 email_outbox_count=?, retry_count=?, warning_count=?, failure_count=?,
                 error_summary=?
               WHERE run_id=? AND status='running' AND started_at=?""",
            (
                *self._values(run)[1:],
                run.run_id,
                claim_started_at.isoformat(),
            ),
        )
        self.connection.commit()
        if cursor.rowcount != 1:
            raise CaptureRunAlreadyActiveError(run.run_id)

    def claim_retry(
        self,
        run: MarketCaptureRun,
        *,
        started_at: datetime,
    ) -> MarketCaptureRun | None:
        cursor = self.connection.execute(
            """UPDATE market_capture_runs SET
                 status='running', started_at=?, finished_at=NULL,
                 retry_count=retry_count+1, error_summary=''
               WHERE run_id=? AND status=? AND started_at=? AND retry_count=?""",
            (
                started_at.isoformat(),
                run.run_id,
                run.status.value,
                run.started_at.isoformat(),
                run.retry_count,
            ),
        )
        self.connection.commit()
        return self.get(run.run_id) if cursor.rowcount == 1 else None

    def fail_if_running(
        self,
        run_id: str,
        *,
        finished_at: datetime,
        expected_started_at: datetime,
        error_summary: str,
    ) -> MarketCaptureRun | None:
        cursor = self.connection.execute(
            """UPDATE market_capture_runs SET
                 status='failed', finished_at=?, failure_count=failure_count+1,
                 error_summary=?
               WHERE run_id=? AND status='running' AND started_at=?""",
            (
                finished_at.isoformat(),
                redact_sensitive_text(error_summary),
                run_id,
                expected_started_at.isoformat(),
            ),
        )
        self.connection.commit()
        return self.get(run_id) if cursor.rowcount == 1 else None

    def fail_expired_intraday_runs(
        self,
        *,
        period_ended_by: datetime,
        lease_started_before: datetime,
        finished_at: datetime,
    ) -> int:
        error_summary = "intraday run lease expired before the next intraday period"
        cursor = self.connection.execute(
            """UPDATE market_capture_runs SET
                 status='failed', finished_at=?, failure_count=failure_count+1,
                 error_summary=?
               WHERE workflow_type='intraday' AND status='running'
                 AND period_end IS NOT NULL AND period_end <= ?
                 AND started_at <= ?""",
            (
                finished_at.isoformat(),
                error_summary,
                period_ended_by.isoformat(),
                lease_started_before.isoformat(),
            ),
        )
        self.connection.commit()
        return cursor.rowcount

    def _get_by_idempotency_key(self, key: str) -> MarketCaptureRun | None:
        row = self.connection.execute(
            "SELECT * FROM market_capture_runs WHERE idempotency_key=?", (key,)
        ).fetchone()
        return None if row is None else self._from_row(row)

    @staticmethod
    def _values(run: MarketCaptureRun) -> tuple[object, ...]:
        return (
            run.run_id,
            run.workflow_type,
            None if run.mode is None else run.mode.value,
            run.trade_date.isoformat(),
            None if run.effective_trade_date is None else run.effective_trade_date.isoformat(),
            None if run.history_cutoff_date is None else run.history_cutoff_date.isoformat(),
            None if run.period_start is None else run.period_start.isoformat(),
            None if run.period_end is None else run.period_end.isoformat(),
            json.dumps(run.requested_symbol_scope, separators=(",", ":")),
            None if run.lease_expires_at is None else run.lease_expires_at.isoformat(),
            run.idempotency_key,
            run.status.value,
            run.started_at.isoformat(),
            None if run.finished_at is None else run.finished_at.isoformat(),
            run.requested_symbols,
            run.processed_symbols,
            run.provider_calls,
            run.provider_duration_ms,
            run.rows_received,
            run.rows_written,
            run.cleaned_rows,
            run.plan_count,
            run.recommendation_count,
            run.notification_count,
            run.email_outbox_count,
            run.retry_count,
            run.warning_count,
            run.failure_count,
            redact_sensitive_text(run.error_summary),
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> MarketCaptureRun:
        return MarketCaptureRun.model_validate(
            {
                "run_id": row["run_id"],
                "workflow_type": row["workflow_type"],
                "mode": row["mode"],
                "trade_date": row["trade_date"],
                "effective_trade_date": row["effective_trade_date"],
                "history_cutoff_date": row["history_cutoff_date"],
                "period_start": row["period_start"],
                "period_end": row["period_end"],
                "requested_symbol_scope": json.loads(row["requested_symbol_scope_json"]),
                "lease_expires_at": row["lease_expires_at"],
                "idempotency_key": row["idempotency_key"],
                "status": row["status"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "requested_symbols": row["requested_symbols"],
                "processed_symbols": row["processed_symbols"],
                "provider_calls": row["provider_calls"],
                "provider_duration_ms": row["provider_duration_ms"],
                "rows_received": row["rows_received"],
                "rows_written": row["rows_written"],
                "cleaned_rows": row["cleaned_rows"],
                "plan_count": row["plan_count"],
                "recommendation_count": row["recommendation_count"],
                "notification_count": row["notification_count"],
                "email_outbox_count": row["email_outbox_count"],
                "retry_count": row["retry_count"],
                "warning_count": row["warning_count"],
                "failure_count": row["failure_count"],
                "error_summary": row["error_summary"],
            }
        )


class MarketCaptureResultRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def upsert(self, result: MarketCaptureResult) -> None:
        self.connection.execute(
            """INSERT INTO market_capture_results (
                 run_id,symbol,dataset,status,data_start,data_end,data_time,
                 fetched_at,expected_rows,actual_rows,source,warning,error_summary
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(run_id,symbol,dataset) DO UPDATE SET
                 status=excluded.status, data_start=excluded.data_start,
                 data_end=excluded.data_end, data_time=excluded.data_time,
                 fetched_at=excluded.fetched_at, expected_rows=excluded.expected_rows,
                 actual_rows=excluded.actual_rows, source=excluded.source,
                 warning=excluded.warning, error_summary=excluded.error_summary""",
            (
                result.run_id,
                result.symbol,
                result.dataset.value,
                result.status.value,
                None if result.data_start is None else result.data_start.isoformat(),
                None if result.data_end is None else result.data_end.isoformat(),
                None if result.data_time is None else result.data_time.isoformat(),
                result.fetched_at.isoformat(),
                result.expected_rows,
                result.actual_rows,
                result.source,
                redact_sensitive_text(result.warning),
                redact_sensitive_text(result.error_summary),
            ),
        )
        self.connection.commit()

    def list_for_run(self, run_id: str) -> list[MarketCaptureResult]:
        rows = self.connection.execute(
            """SELECT * FROM market_capture_results
               WHERE run_id=? ORDER BY symbol, dataset""",
            (run_id,),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> MarketCaptureResult:
        return MarketCaptureResult.model_validate(
            {
                "run_id": row["run_id"],
                "symbol": row["symbol"],
                "dataset": row["dataset"],
                "status": row["status"],
                "data_start": row["data_start"],
                "data_end": row["data_end"],
                "data_time": row["data_time"],
                "fetched_at": row["fetched_at"],
                "expected_rows": row["expected_rows"],
                "actual_rows": row["actual_rows"],
                "source": row["source"],
                "warning": row["warning"],
                "error_summary": row["error_summary"],
            }
        )


def validate_heavy_snapshot_references(
    connection: sqlite3.Connection,
    snapshot: MarketInputSnapshot,
) -> None:
    for refs, table, error in (
        (
            snapshot.history_snapshot_refs,
            "history_snapshots",
            "invalid history snapshot reference",
        ),
        (
            snapshot.money_flow_snapshot_refs,
            "money_flow_snapshots",
            "invalid money-flow snapshot reference",
        ),
        (
            snapshot.intraday_strength_snapshot_refs,
            "intraday_strength_snapshots",
            "invalid intraday-strength snapshot reference",
        ),
    ):
        for symbol, snapshot_id in refs.items():
            row = connection.execute(
                f"SELECT 1 FROM {table} WHERE id=? AND symbol=?",
                (snapshot_id, symbol),
            ).fetchone()
            if row is None:
                raise ValueError(error)


class ExtendedMarketInputSnapshotRepository:
    """Compatibility facade for workflow heavy-reference validation."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def validate_heavy_references(self, snapshot: MarketInputSnapshot) -> None:
        validate_heavy_snapshot_references(self.connection, snapshot)


def content_digest(content_hashes: list[str]) -> str:
    return hashlib.sha256("\n".join(content_hashes).encode("ascii")).hexdigest()
