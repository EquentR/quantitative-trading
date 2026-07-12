# Windows CSV and Market Snapshots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Windows CSV uploads and add traceable quote snapshots for the decision-enabled stock universe, exposed through shared CLI and HTTP workflows.

**Architecture:** Close uploaded temporary files before existing path-based repositories reopen them. Persist every internal `QuoteSnapshot` separately, then persist a `MarketInputSnapshot` that references the exact universe and quote rows used by one collection run; a shared service owns selection, failure completion, sanitization, and transaction boundaries.

**Tech Stack:** Python 3.11, FastAPI, Typer, Pydantic 2, SQLite, pytest.

---

### Task 1: Make CSV upload files reopenable on Windows

**Files:**
- Create: `src/quantitative_trading/api/uploads.py`
- Create: `tests/test_api_uploads.py`
- Modify: `src/quantitative_trading/api/routes/positions.py`
- Modify: `src/quantitative_trading/api/routes/watchlist.py`
- Test: `tests/test_api_positions.py`
- Test: `tests/test_api_watchlist.py`

- [ ] **Step 1: Preserve and run the existing Windows regression tests**

Run:

```powershell
& 'C:\Users\Equent\.conda\envs\quantitative-trading\python.exe' -m pytest -q `
  tests/test_api_positions.py::test_positions_csv_import_and_export `
  tests/test_api_positions.py::test_positions_csv_import_bad_header_returns_validation_error `
  tests/test_api_watchlist.py::test_watchlist_csv_import_accepts_common_true_values `
  tests/test_api_watchlist.py::test_watchlist_csv_import_bad_header_returns_validation_error
```

Expected: four failures with `PermissionError` when the repository reopens a `NamedTemporaryFile`.

- [ ] **Step 2: Add a failing cleanup test for a shared upload helper**

Create `tests/test_api_uploads.py`:

```python
from pathlib import Path

import pytest

from quantitative_trading.api.uploads import closed_temporary_upload


def test_closed_temporary_upload_is_reopenable_and_removed_after_error() -> None:
    captured_path: Path | None = None

    with pytest.raises(RuntimeError, match="stop"):
        with closed_temporary_upload(b"symbol,name\n600000,test\n", suffix=".csv") as path:
            captured_path = path
            assert path.read_bytes().startswith(b"symbol")
            raise RuntimeError("stop")

    assert captured_path is not None
    assert not captured_path.exists()
```

Run:

```powershell
& 'C:\Users\Equent\.conda\envs\quantitative-trading\python.exe' -m pytest -q tests/test_api_uploads.py
```

Expected: collection fails because `quantitative_trading.api.uploads` does not exist.

- [ ] **Step 3: Implement the closed temporary upload helper**

Create `src/quantitative_trading/api/uploads.py`:

```python
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import mkstemp


@contextmanager
def closed_temporary_upload(content: bytes, *, suffix: str) -> Iterator[Path]:
    descriptor, raw_path = mkstemp(suffix=suffix)
    path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
        yield path
    finally:
        path.unlink(missing_ok=True)
```

- [ ] **Step 4: Use the helper in both CSV routes**

In both route modules, remove `NamedTemporaryFile` and `Path as FilePath` imports, import `closed_temporary_upload`, and replace the open-file blocks with:

```python
with closed_temporary_upload(content, suffix=".csv") as path:
    with connection_scope(container.settings) as connection:
        service = LedgerService(PositionRepository(connection))
        return service.import_csv(path)
```

and:

```python
with closed_temporary_upload(content, suffix=".csv") as path:
    with connection_scope(container.settings) as connection:
        service = WatchPinnedService(WatchPinnedRepository(connection))
        return service.import_csv(path, source=WatchPinnedSource.MANUAL)
```

- [ ] **Step 5: Verify the focused CSV tests pass**

Run the command from Step 1 plus `tests/test_api_uploads.py`.

Expected: all five tests pass and no temporary-path traceback is emitted.

- [ ] **Step 6: Commit the CSV fix**

```powershell
git add src/quantitative_trading/api/uploads.py src/quantitative_trading/api/routes/positions.py src/quantitative_trading/api/routes/watchlist.py tests/test_api_uploads.py
git commit -m "fix: close csv uploads before import"
```

### Task 2: Define market input models and SQLite schema

**Files:**
- Modify: `src/quantitative_trading/market/models.py`
- Modify: `src/quantitative_trading/storage/sqlite.py`
- Modify: `tests/test_market_models.py`
- Modify: `tests/test_sqlite_storage.py`

- [ ] **Step 1: Add failing `MarketInputSnapshot` validation tests**

Append to `tests/test_market_models.py` tests that construct:

```python
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
```

Also assert naive `data_time`, naive `fetched_at`, invalid symbols, and non-positive reference IDs raise `ValidationError`.

Run:

```powershell
& 'C:\Users\Equent\.conda\envs\quantitative-trading\python.exe' -m pytest -q tests/test_market_models.py
```

Expected: import failure because `MarketInputSnapshot` does not exist.

- [ ] **Step 2: Add failing migration tests for quotes and their index**

Append tests to `tests/test_sqlite_storage.py` that call `migrate()` twice and assert SQLite contains:

```python
assert connection.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='quote_snapshots'"
).fetchone()[0] == "quote_snapshots"
assert connection.execute(
    "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_quote_snapshots_symbol_id'"
).fetchone()[0] == "idx_quote_snapshots_symbol_id"
```

Expected: failure because the table and index are absent.

- [ ] **Step 3: Implement `MarketInputSnapshot`**

Add to `market/models.py`:

```python
class MarketInputSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_snapshot_id: int = Field(gt=0)
    quote_snapshot_refs: dict[str, int]
    history_snapshot_refs: dict[str, int]
    money_flow_snapshot_refs: dict[str, int]
    intraday_strength_snapshot_refs: dict[str, int]
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
    def references_must_be_valid(cls, value: dict[str, int]) -> dict[str, int]:
        if any(len(symbol) != 6 or not symbol.isdigit() for symbol in value):
            raise ValueError("snapshot reference symbols must contain six digits")
        if any(reference_id <= 0 for reference_id in value.values()):
            raise ValueError("snapshot reference ids must be positive")
        return value
```

- [ ] **Step 4: Add the quote table and index**

Add to `storage/sqlite.py` before `MARKET_INPUT_SNAPSHOTS_SCHEMA_SQL`:

```python
QUOTE_SNAPSHOTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS quote_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ok', 'partial', 'failed', 'stale')),
  data_time TEXT,
  fetched_at TEXT NOT NULL,
  source TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  CHECK (symbol GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]')
);
CREATE INDEX IF NOT EXISTS idx_quote_snapshots_symbol_id
ON quote_snapshots(symbol, id DESC);
"""
```

Insert `QUOTE_SNAPSHOTS_SCHEMA_SQL` before `MARKET_INPUT_SNAPSHOTS_SCHEMA_SQL` in `SCHEMA_STATEMENTS`.

- [ ] **Step 5: Verify model and migration tests pass**

Run:

```powershell
& 'C:\Users\Equent\.conda\envs\quantitative-trading\python.exe' -m pytest -q tests/test_market_models.py tests/test_sqlite_storage.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit models and migration**

```powershell
git add src/quantitative_trading/market/models.py src/quantitative_trading/storage/sqlite.py tests/test_market_models.py tests/test_sqlite_storage.py
git commit -m "feat: define traceable market snapshots"
```

### Task 3: Persist quote and aggregate snapshots

**Files:**
- Create: `src/quantitative_trading/market/repository.py`
- Create: `tests/test_market_snapshot_repository.py`
- Modify: `src/quantitative_trading/universe/repository.py`
- Create: `tests/test_universe_repository.py`

- [ ] **Step 1: Write failing repository round-trip tests**

Create `tests/test_market_snapshot_repository.py` using a migrated temporary SQLite connection. Save OK, PARTIAL, STALE, and FAILED `QuoteSnapshot` objects and assert `get()` plus `latest_for_symbol()` return model-equal values. Save a `MarketInputSnapshot`, then assert `get()` and `latest()` round-trip the full payload.

The aggregate fixture must reference real quote IDs:

```python
market_snapshot = MarketInputSnapshot(
    universe_snapshot_id=universe_snapshot_id,
    quote_snapshot_refs={"600000": quote_id},
    history_snapshot_refs={},
    money_flow_snapshot_refs={},
    intraday_strength_snapshot_refs={},
    data_time=DATA_TIME,
    fetched_at=FETCHED_AT,
    warnings=[],
)
```

Run the file and expect import failure because the repository module is missing.

- [ ] **Step 2: Implement repositories with optional commits**

Create `market/repository.py` with:

```python
class QuoteSnapshotRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, snapshot: QuoteSnapshot, *, commit: bool = True) -> int:
        cursor = self.connection.execute(
            """INSERT INTO quote_snapshots
               (symbol, status, data_time, fetched_at, source, payload_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                snapshot.symbol,
                snapshot.status.value,
                None if snapshot.data_time is None else snapshot.data_time.isoformat(),
                snapshot.fetched_at.isoformat(),
                snapshot.source,
                snapshot.model_dump_json(),
            ),
        )
        if commit:
            self.connection.commit()
        return int(cursor.lastrowid)
```

Implement `get()` and `latest_for_symbol()` by validating `payload_json` through `QuoteSnapshot.model_validate_json()`.

Implement `MarketInputSnapshotRepository.save/get/latest` in the same module, writing `universe_snapshot_id`, `data_time`, `fetched_at`, JSON warnings, and internal payload JSON. Its `save` also accepts `commit: bool = True`.

- [ ] **Step 3: Allow universe snapshots to join a caller-owned transaction**

Change `UniverseSnapshotRepository.save` to:

```python
def save(self, snapshot: UniverseSnapshot, *, commit: bool = True) -> int:
    # existing INSERT
    if commit:
        self.connection.commit()
    return int(cursor.lastrowid)
```

Add a regression test proving `commit=False` can be rolled back while the default behavior remains committed.

- [ ] **Step 4: Verify repository tests pass**

Run:

```powershell
& 'C:\Users\Equent\.conda\envs\quantitative-trading\python.exe' -m pytest -q tests/test_market_snapshot_repository.py tests/test_universe_repository.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit repositories**

```powershell
git add src/quantitative_trading/market/repository.py src/quantitative_trading/universe/repository.py tests/test_market_snapshot_repository.py tests/test_universe_repository.py
git commit -m "feat: persist market snapshot references"
```

### Task 4: Capture decision-enabled quotes through one shared service

**Files:**
- Create: `src/quantitative_trading/market/snapshot_service.py`
- Create: `tests/test_market_snapshot_service.py`

- [ ] **Step 1: Write failing selection and success tests**

Create fixtures for one holding, one enabled watch item, one disabled watch item, and a fake provider recording calls. Assert:

```python
created = MarketSnapshotService(connection, provider, now=FETCHED_AT).capture()

assert provider.calls == [["000001", "600000"]]
assert set(created.snapshot.quote_snapshot_refs) == {"000001", "600000"}
assert created.snapshot.data_time == OLDEST_DATA_TIME
assert created.quotes["600000"].status is QuoteStatus.OK
```

Expected: import failure because the service does not exist.

- [ ] **Step 2: Add failing sparse, extra, exception, and empty-universe tests**

Cover these exact outcomes:

- missing requested symbol becomes a FAILED quote with `source="market_snapshot_service"`;
- extra provider symbol is absent from refs and adds a warning;
- provider exception text is sanitized using a message containing `api_key=supersecret`, `Bearer abc`, and `/tmp/private.db`;
- empty decision set does not invoke the provider and still persists an aggregate snapshot;
- duplicate holding/watch symbols are requested once.

- [ ] **Step 3: Implement `MarketSnapshotService`**

Create:

```python
from __future__ import annotations

import sqlite3
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


@dataclass(frozen=True)
class CreatedMarketInputSnapshot:
    snapshot_id: int
    snapshot: MarketInputSnapshot
    quotes: dict[str, QuoteSnapshot]


class MarketSnapshotService:
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
        positions = PositionRepository(self.connection).list()
        watchlist = WatchPinnedRepository(self.connection).list()
        members = build_universe(positions=positions, watchlist=watchlist, created_at=fetched_at)
        requested = sorted(member.symbol for member in members if member.plan_enabled)
        quotes, warnings = self._fetch_quotes(requested, fetched_at=fetched_at)

        universe = UniverseSnapshot(
            created_at=fetched_at,
            status=UniverseSnapshotStatus.OK,
            warnings=[],
            members=members,
        )

        try:
            universe_id = UniverseSnapshotRepository(self.connection).save(
                universe,
                commit=False,
            )
            quote_repository = QuoteSnapshotRepository(self.connection)
            quote_refs = {
                symbol: quote_repository.save(quotes[symbol], commit=False)
                for symbol in requested
            }
            snapshot = MarketInputSnapshot(
                universe_snapshot_id=universe_id,
                quote_snapshot_refs=quote_refs,
                history_snapshot_refs={},
                money_flow_snapshot_refs={},
                intraday_strength_snapshot_refs={},
                data_time=self._data_time(quotes),
                fetched_at=fetched_at,
                warnings=[
                    *warnings,
                    "历史 K 线快照本阶段未采集",
                    "资金流快照本阶段未采集",
                    "分时强弱快照本阶段未采集",
                ],
            )
            snapshot_id = MarketInputSnapshotRepository(self.connection).save(
                snapshot,
                commit=False,
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

        return CreatedMarketInputSnapshot(
            snapshot_id=snapshot_id,
            snapshot=snapshot,
            quotes=quotes,
        )

    def _fetch_quotes(
        self,
        requested: list[str],
        *,
        fetched_at: datetime,
    ) -> tuple[dict[str, QuoteSnapshot], list[str]]:
        if not requested:
            return {}, ["无决策启用标的，未调用行情数据源"]

        warnings: list[str] = []
        try:
            returned = self.provider.get_quotes(requested)
            provider_error = None
        except Exception as exc:
            returned = {}
            provider_error = safe_error_summary(exc)
            warnings.append(f"行情数据源调用失败: {provider_error}")

        requested_set = set(requested)
        extras = sorted(set(returned) - requested_set)
        if extras:
            warnings.append(f"行情数据源返回非请求标的，已忽略: {','.join(extras)}")

        quotes: dict[str, QuoteSnapshot] = {}
        for symbol in requested:
            quote = returned.get(symbol)
            if quote is None:
                reason = provider_error or "行情数据源未返回该标的"
                quote = self._missing_quote(symbol, fetched_at=fetched_at, reason=reason)
            elif quote.symbol != symbol:
                warnings.append(
                    f"{symbol}: 行情映射代码不匹配，返回 {quote.symbol}，已按失败记录"
                )
                quote = self._missing_quote(
                    symbol,
                    fetched_at=fetched_at,
                    reason="行情数据源返回的股票代码不匹配",
                )
            elif quote.warning:
                quote = quote.model_copy(
                    update={"warning": redact_sensitive_text(quote.warning)}
                )

            quotes[symbol] = quote
            if quote.status is not QuoteStatus.OK:
                warnings.append(
                    f"{symbol}: {quote.status.value}: {quote.warning or '行情数据不完整'}"
                )
        return quotes, warnings

    @staticmethod
    def _missing_quote(
        symbol: str,
        *,
        fetched_at: datetime,
        reason: str,
    ) -> QuoteSnapshot:
        return QuoteSnapshot(
            symbol=symbol,
            fetched_at=fetched_at,
            source="market_snapshot_service",
            status=QuoteStatus.FAILED,
            warning=redact_sensitive_text(reason),
        )

    @staticmethod
    def _data_time(quotes: dict[str, QuoteSnapshot]) -> datetime | None:
        data_times = [
            quote.data_time
            for quote in quotes.values()
            if quote.data_time is not None
        ]
        return min(data_times) if data_times else None
```

Only requested symbols enter `quotes` and refs. Provider exceptions and provider-supplied warnings are sanitized before persistence.

- [ ] **Step 4: Verify all service behaviors pass**

Run:

```powershell
& 'C:\Users\Equent\.conda\envs\quantitative-trading\python.exe' -m pytest -q tests/test_market_snapshot_service.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit the shared service**

```powershell
git add src/quantitative_trading/market/snapshot_service.py tests/test_market_snapshot_service.py
git commit -m "feat: capture decision universe quotes"
```

### Task 5: Add HTTP market snapshot endpoints

**Files:**
- Create: `src/quantitative_trading/api/routes/market.py`
- Create: `tests/test_api_market_snapshots.py`
- Modify: `src/quantitative_trading/api/app.py`

- [ ] **Step 1: Write failing API contract tests**

Use the existing authenticated client pattern and monkeypatch the market provider factory. Cover:

```python
create = client.post("/api/v1/market/snapshots", headers=headers)
latest = client.get("/api/v1/market/snapshots/latest", headers=headers)
detail = client.get(f"/api/v1/market/snapshots/{create.json()['snapshot_id']}", headers=headers)

assert create.status_code == 201
assert latest.json() == create.json()["snapshot"]
assert detail.json() == create.json()["snapshot"]
```

Also cover authentication, missing latest/detail returning `market_snapshot_not_found`, unsupported provider returning `validation_error`, and SQLite failures returning a sanitized `internal_error`.

Expected: 404 because no market router is mounted.

- [ ] **Step 2: Implement the market router**

Define:

```python
class CreatedMarketSnapshotResponse(BaseModel):
    snapshot_id: int
    snapshot: MarketInputSnapshot
```

Use `market_provider_from_settings` from `runtime.account_snapshot_job` so account and market snapshots interpret settings identically. `POST` creates a provider, opens `connection_scope`, calls `MarketSnapshotService.capture()`, and returns 201. `GET` routes use `MarketInputSnapshotRepository`.

Map errors to:

```python
ApiError(status_code=404, code="market_snapshot_not_found", message="market snapshot not found")
ApiError(status_code=500, code="internal_error", message="market snapshot storage failed")
```

Map `UnsupportedMarketProviderError` to the same unsupported-provider contract as the account route.

- [ ] **Step 3: Mount the router**

Import `market` in `api/app.py` and add:

```python
app.include_router(market.router, prefix="/api/v1")
```

- [ ] **Step 4: Verify API tests pass**

Run the new API test file plus `tests/test_api_account.py`.

Expected: all tests pass and account provider behavior is unchanged.

- [ ] **Step 5: Commit the HTTP API**

```powershell
git add src/quantitative_trading/api/routes/market.py src/quantitative_trading/api/app.py tests/test_api_market_snapshots.py
git commit -m "feat: expose market snapshot api"
```

### Task 6: Add the CLI market snapshot command

**Files:**
- Modify: `src/quantitative_trading/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write a failing CLI test**

Create a holding and enabled watch item, inject a fake `AkShareMarketProvider`, and run:

```python
result = run_cli(
    tmp_path,
    "market",
    "snapshot",
    env={"QT_ENABLE_MARKET_FETCH": "true", "QT_MARKET_PROVIDER": "akshare"},
)

assert result.exit_code == 0
assert "market_snapshot_id=1" in result.output
assert "requested=2" in result.output
assert "ok=2" in result.output
assert "failed=0" in result.output
```

Expected: Typer reports no such command `market`.

- [ ] **Step 2: Implement and register the command**

Add `market_app = typer.Typer()` and `app.add_typer(market_app, name="market")`. Implement:

```python
@market_app.command("snapshot")
def market_snapshot() -> None:
    with _database_scope() as (settings, connection):
        created = MarketSnapshotService(
            connection,
            _market_provider(settings),
        ).capture()
    counts = Counter(quote.status.value for quote in created.quotes.values())
    typer.echo(
        f"market_snapshot_id={created.snapshot_id} "
        f"universe_snapshot_id={created.snapshot.universe_snapshot_id} "
        f"requested={len(created.quotes)} "
        f"ok={counts['ok']} partial={counts['partial']} "
        f"stale={counts['stale']} failed={counts['failed']} "
        f"data_time={created.snapshot.data_time.isoformat() if created.snapshot.data_time else '-'}"
    )
    for warning in created.snapshot.warnings:
        typer.echo(f"warning={warning}")
```

- [ ] **Step 3: Verify CLI tests pass**

Run the focused new test and the full `tests/test_cli.py` file.

Expected: all CLI tests pass.

- [ ] **Step 4: Commit the CLI entry point**

```powershell
git add src/quantitative_trading/cli.py tests/test_cli.py
git commit -m "feat: add market snapshot cli"
```

### Task 7: Synchronize documentation

**Files:**
- Modify: `docs/data-sources.md`
- Modify: `docs/api.md`
- Modify: `README.md`
- Test: `tests/test_docs_examples.py`

- [ ] **Step 1: Add failing documentation assertions**

Extend `tests/test_docs_examples.py` to require:

```python
assert "qt market snapshot" in readme
assert "POST /api/v1/market/snapshots" in api_docs
assert "quote_snapshot_refs" in data_sources
```

Run the file and expect failures because the commands and endpoints are undocumented.

- [ ] **Step 2: Update the three documents**

Document these exact boundaries:

- only holdings and `plan_enabled` watch items are collected;
- each requested symbol produces an internal quote row, including failures;
- aggregate `data_time` is the oldest usable quote time and `fetched_at` is system acquisition time;
- history, money flow, and intraday refs are empty in this phase;
- CLI and authenticated API commands;
- snapshots do not trigger trading and are not yet consumed by planning or strategy.

- [ ] **Step 3: Verify documentation tests pass**

Run `tests/test_docs_examples.py` and `git diff --check`.

Expected: tests pass and no whitespace errors.

- [ ] **Step 4: Commit documentation**

```powershell
git add README.md docs/api.md docs/data-sources.md tests/test_docs_examples.py
git commit -m "docs: document traceable market snapshots"
```

### Task 8: Full verification

**Files:**
- Verify all modified files.

- [ ] **Step 1: Run the complete Python suite in the project Conda environment**

```powershell
$env:PYTHONUTF8='1'
& 'C:\Users\Equent\.conda\envs\quantitative-trading\python.exe' -m pytest -q
```

Expected: exit 0, including the four formerly failing Windows CSV tests.

- [ ] **Step 2: Run frontend regression tests on Node 24**

```powershell
nvm use 24.18.0
& .\src\web\node_modules\.bin\vitest.cmd run
& .\src\web\node_modules\.bin\vite.cmd build
```

Expected: 91 frontend tests pass and Vite build exits 0. Exact count may increase only if the existing frontend changed independently; this plan does not modify frontend source.

- [ ] **Step 3: Inspect repository safety and scope**

```powershell
git diff --check
git status --short
rg -n "api_key|Bearer|cookie|token|password" src/quantitative_trading/market tests/test_market_snapshot*
```

Expected: no uncommitted generated files, no whitespace errors, and only deliberate synthetic secrets inside sanitization tests.

- [ ] **Step 4: Review the implementation against the spec**

Confirm every acceptance criterion in `docs/superpowers/specs/2026-07-12-csv-market-snapshots-design.md` has a corresponding passing test. Confirm strategy, planning, recommendation, notification, and scheduler behavior were not changed.

- [ ] **Step 5: Record final status**

```powershell
git log --oneline --decorate -10
git status --short --branch
```

Expected: branch `codex/csv-market-snapshots`, clean worktree, and focused commits for CSV, models/storage, service, API, CLI, and docs.
