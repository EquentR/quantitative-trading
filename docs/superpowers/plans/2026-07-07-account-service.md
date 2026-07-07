# Account Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first working account valuation service with manual cash ledger, AkShare-backed quote adapter boundary, account snapshots, and a debuggable `qt service run` background loop.

**Architecture:** SQLite stores manual positions, manual cash state, cash transactions, and account snapshots. CLI, background runner, and future API entrypoints call shared services that return Pydantic models; Typer only formats output and never owns business calculations. Market data is behind a provider interface so unit tests use fake providers and AkShare remains isolated.

**Tech Stack:** Python 3.11+, uv, pytest, pydantic v2, typer, sqlite3 standard library, APScheduler for debug background scheduling, AkShare as the first live market provider.

---

## Scope Check

The spec covers several modules, but they form one coherent slice: account valuation requires cash state, position reads, market quotes, snapshots, and a runner. The plan keeps those pieces separate and lands them in small commits so each task is working, tested software.

## File Structure

- Modify: `pyproject.toml`  
  Add runtime dependencies for APScheduler and AkShare.
- Modify: `src/quantitative_trading/config.py`  
  Add log directory, market provider, market fetch flag, interval seconds, and Shanghai timezone settings.
- Modify: `src/quantitative_trading/storage/sqlite.py`  
  Add `cash_account`, `cash_transactions`, and `account_snapshots` schema.
- Create: `src/quantitative_trading/cash/__init__.py`  
  Cash package marker.
- Create: `src/quantitative_trading/cash/models.py`  
  Pydantic models and transaction type enum for cash state and transactions.
- Create: `src/quantitative_trading/cash/repository.py`  
  SQLite persistence for cash account state and cash transaction history.
- Create: `src/quantitative_trading/cash/service.py`  
  Read-write and read-only cash service interfaces.
- Create: `src/quantitative_trading/market/__init__.py`  
  Market package marker.
- Create: `src/quantitative_trading/market/models.py`  
  Quote status enum and `QuoteSnapshot`.
- Create: `src/quantitative_trading/market/providers.py`  
  Market provider protocol plus AkShare provider wrapper.
- Create: `src/quantitative_trading/account/__init__.py`  
  Account package marker.
- Create: `src/quantitative_trading/account/models.py`  
  `PositionValuation` and `AccountSnapshot` Pydantic models.
- Create: `src/quantitative_trading/account/repository.py`  
  Account snapshot persistence.
- Create: `src/quantitative_trading/account/service.py`  
  Account valuation calculation service.
- Create: `src/quantitative_trading/runtime/__init__.py`  
  Runtime package marker.
- Create: `src/quantitative_trading/runtime/service_runner.py`  
  Debug foreground background runner and single-cycle hooks.
- Modify: `src/quantitative_trading/cli.py`  
  Add `cash`, `account`, and `service run` commands; extend `service check`.
- Modify: `docs/project-spec.md`  
  Document manual cash account, account valuation, and debug service scope.
- Modify: `docs/trading-policy.md`  
  Document cash and net principal as buy-funding inputs.
- Modify: `docs/data-sources.md`  
  Document manual cash account as account funding authority and AkShare as quote-only source.
- Modify: `docs/recommendation-contract.md`  
  Document future account funding context for position constraints.
- Modify: `README.md`  
  Add cash/account/service run usage examples.
- Modify: `.env.example`  
  Add non-secret runtime configuration examples.
- Test: `tests/test_config.py`
- Test: `tests/test_sqlite_storage.py`
- Test: `tests/test_cash_models.py`
- Test: `tests/test_cash_repository.py`
- Test: `tests/test_cash_service.py`
- Test: `tests/test_account_models.py`
- Test: `tests/test_account_service.py`
- Test: `tests/test_account_repository.py`
- Test: `tests/test_market_models.py`
- Test: `tests/test_market_providers.py`
- Test: `tests/test_service_runner.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_docs_examples.py`

---

### Task 1: Runtime Configuration And Dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/quantitative_trading/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing configuration tests**

Create `tests/test_config.py`:

```python
from pathlib import Path

from quantitative_trading.config import Settings, load_settings


def test_settings_defaults_are_local_and_shanghai_time() -> None:
    settings = Settings()

    assert settings.database_path == Path("data/quant_trading.db")
    assert settings.log_dir == Path("data/logs")
    assert settings.market_provider == "akshare"
    assert settings.intraday_interval_seconds == 180
    assert settings.timezone == "Asia/Shanghai"
    assert settings.enable_market_fetch is True


def test_load_settings_reads_runtime_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("QT_DATABASE_PATH", str(tmp_path / "account.db"))
    monkeypatch.setenv("QT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("QT_MARKET_PROVIDER", "fake")
    monkeypatch.setenv("QT_INTRADAY_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("QT_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("QT_ENABLE_MARKET_FETCH", "false")

    settings = load_settings()

    assert settings.database_path == tmp_path / "account.db"
    assert settings.log_dir == tmp_path / "logs"
    assert settings.market_provider == "fake"
    assert settings.intraday_interval_seconds == 60
    assert settings.timezone == "Asia/Shanghai"
    assert settings.enable_market_fetch is False
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_config.py -q
```

Expected: FAIL because `Settings` does not expose the new runtime fields.

- [ ] **Step 3: Add dependencies**

Modify `pyproject.toml` dependencies:

```toml
dependencies = [
  "akshare>=1.15,<2",
  "apscheduler>=3.10,<4",
  "pydantic>=2.7,<3",
  "typer>=0.12,<1",
]
```

- [ ] **Step 4: Implement runtime settings**

Replace `src/quantitative_trading/config.py` with:

```python
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    database_path: Path = Field(default=Path("data/quant_trading.db"))
    log_dir: Path = Field(default=Path("data/logs"))
    market_provider: str = Field(default="akshare")
    intraday_interval_seconds: int = Field(default=180, ge=1)
    timezone: str = Field(default="Asia/Shanghai")
    enable_market_fetch: bool = Field(default=True)


def load_settings() -> Settings:
    return Settings(
        database_path=Path(os.environ.get("QT_DATABASE_PATH", "data/quant_trading.db")),
        log_dir=Path(os.environ.get("QT_LOG_DIR", "data/logs")),
        market_provider=os.environ.get("QT_MARKET_PROVIDER", "akshare"),
        intraday_interval_seconds=int(os.environ.get("QT_INTRADAY_INTERVAL_SECONDS", "180")),
        timezone=os.environ.get("QT_TIMEZONE", "Asia/Shanghai"),
        enable_market_fetch=_env_bool("QT_ENABLE_MARKET_FETCH", True),
    )
```

- [ ] **Step 5: Run configuration tests**

Run:

```bash
uv run pytest tests/test_config.py -q
```

Expected: PASS.

- [ ] **Step 6: Run existing tests**

Run:

```bash
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/quantitative_trading/config.py tests/test_config.py
git commit -m "feat: add runtime account service settings"
```

---

### Task 2: SQLite Schema For Cash And Snapshots

**Files:**
- Modify: `src/quantitative_trading/storage/sqlite.py`
- Modify: `tests/test_sqlite_storage.py`

- [ ] **Step 1: Add failing migration tests**

Append to `tests/test_sqlite_storage.py`:

```python
def test_migrate_creates_cash_account_table(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)
        columns = connection.execute("PRAGMA table_info(cash_account)").fetchall()

    assert [column["name"] for column in columns] == [
        "id",
        "cash_balance",
        "total_transfer_in",
        "total_transfer_out",
        "updated_at",
    ]


def test_migrate_creates_cash_transactions_table(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)
        columns = connection.execute("PRAGMA table_info(cash_transactions)").fetchall()

    assert [column["name"] for column in columns] == [
        "id",
        "type",
        "amount",
        "cash_before",
        "cash_after",
        "occurred_at",
        "note",
    ]


def test_migrate_creates_account_snapshots_table(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)
        columns = connection.execute("PRAGMA table_info(account_snapshots)").fetchall()

    assert [column["name"] for column in columns] == [
        "id",
        "status",
        "created_at",
        "cash_account_updated_at",
        "ledger_max_updated_at",
        "market_value",
        "total_assets",
        "total_pnl",
        "position_ratio",
        "payload_json",
    ]
```

- [ ] **Step 2: Run storage tests to verify they fail**

Run:

```bash
uv run pytest tests/test_sqlite_storage.py -q
```

Expected: FAIL because the new tables do not exist.

- [ ] **Step 3: Extend SQLite schema**

Append these tables to `SCHEMA_SQL` in `src/quantitative_trading/storage/sqlite.py`:

```python
CREATE TABLE IF NOT EXISTS cash_account (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  cash_balance REAL NOT NULL CHECK (cash_balance >= 0),
  total_transfer_in REAL NOT NULL CHECK (total_transfer_in >= 0),
  total_transfer_out REAL NOT NULL CHECK (total_transfer_out >= 0),
  updated_at TEXT NOT NULL,
  CHECK (total_transfer_in >= total_transfer_out)
);

CREATE TABLE IF NOT EXISTS cash_transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  amount REAL NOT NULL CHECK (amount > 0),
  cash_before REAL NOT NULL CHECK (cash_before >= 0),
  cash_after REAL NOT NULL CHECK (cash_after >= 0),
  occurred_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  CHECK (type IN ('initial_deposit', 'transfer_in', 'transfer_out', 'cash_adjustment'))
);

CREATE TABLE IF NOT EXISTS account_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  cash_account_updated_at TEXT,
  ledger_max_updated_at TEXT,
  market_value REAL,
  total_assets REAL,
  total_pnl REAL,
  position_ratio REAL,
  payload_json TEXT NOT NULL
);
```

- [ ] **Step 4: Run storage tests**

Run:

```bash
uv run pytest tests/test_sqlite_storage.py -q
```

Expected: PASS.

- [ ] **Step 5: Run full test suite**

Run:

```bash
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/quantitative_trading/storage/sqlite.py tests/test_sqlite_storage.py
git commit -m "feat: add account cash sqlite schema"
```

---

### Task 3: Cash Models

**Files:**
- Create: `src/quantitative_trading/cash/__init__.py`
- Create: `src/quantitative_trading/cash/models.py`
- Test: `tests/test_cash_models.py`

- [ ] **Step 1: Write failing model tests**

Create `tests/test_cash_models.py`:

```python
from datetime import datetime

import pytest
from pydantic import ValidationError

from quantitative_trading.cash.models import CashAccount, CashTransaction, CashTransactionType


def test_cash_account_derives_net_principal() -> None:
    account = CashAccount.model_validate(
        {
            "cash_balance": 48000,
            "total_transfer_in": 50000,
            "total_transfer_out": 2000,
            "updated_at": "2026-07-07T09:00:00+08:00",
        }
    )

    assert account.cash_balance == 48000
    assert account.net_principal == 48000


def test_cash_account_rejects_transfer_out_above_transfer_in() -> None:
    with pytest.raises(ValidationError):
        CashAccount.model_validate(
            {
                "cash_balance": 1000,
                "total_transfer_in": 1000,
                "total_transfer_out": 1001,
                "updated_at": "2026-07-07T09:00:00+08:00",
            }
        )


def test_cash_transaction_accepts_timezone_aware_time() -> None:
    transaction = CashTransaction.model_validate(
        {
            "id": 1,
            "type": "transfer_in",
            "amount": 1000,
            "cash_before": 5000,
            "cash_after": 6000,
            "occurred_at": "2026-07-07T09:00:00+08:00",
            "note": "银证转入",
        }
    )

    assert transaction.type is CashTransactionType.TRANSFER_IN
    assert isinstance(transaction.occurred_at, datetime)


def test_cash_transaction_rejects_zero_amount() -> None:
    with pytest.raises(ValidationError):
        CashTransaction.model_validate(
            {
                "type": "cash_adjustment",
                "amount": 0,
                "cash_before": 5000,
                "cash_after": 5000,
                "occurred_at": "2026-07-07T09:00:00+08:00",
                "note": "无变化",
            }
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_cash_models.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'quantitative_trading.cash'`.

- [ ] **Step 3: Implement cash models**

Create `src/quantitative_trading/cash/__init__.py`:

```python
"""Manual cash account ledger."""
```

Create `src/quantitative_trading/cash/models.py`:

```python
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator


class CashTransactionType(StrEnum):
    INITIAL_DEPOSIT = "initial_deposit"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    CASH_ADJUSTMENT = "cash_adjustment"


def _must_be_timezone_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value


class CashAccount(BaseModel):
    cash_balance: float = Field(ge=0)
    total_transfer_in: float = Field(ge=0)
    total_transfer_out: float = Field(ge=0)
    updated_at: datetime

    @computed_field
    @property
    def net_principal(self) -> float:
        return self.total_transfer_in - self.total_transfer_out

    @field_validator("updated_at")
    @classmethod
    def updated_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return _must_be_timezone_aware(value)

    @model_validator(mode="after")
    def transfer_out_cannot_exceed_transfer_in(self) -> "CashAccount":
        if self.total_transfer_out > self.total_transfer_in:
            raise ValueError("total_transfer_out cannot exceed total_transfer_in")
        return self


class CashTransaction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: int | None = None
    type: CashTransactionType
    amount: float = Field(gt=0)
    cash_before: float = Field(ge=0)
    cash_after: float = Field(ge=0)
    occurred_at: datetime
    note: str = ""

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return _must_be_timezone_aware(value)
```

- [ ] **Step 4: Run cash model tests**

Run:

```bash
uv run pytest tests/test_cash_models.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quantitative_trading/cash tests/test_cash_models.py
git commit -m "feat: add cash ledger models"
```

---

### Task 4: Cash Repository

**Files:**
- Create: `src/quantitative_trading/cash/repository.py`
- Test: `tests/test_cash_repository.py`

- [ ] **Step 1: Write failing repository tests**

Create `tests/test_cash_repository.py`:

```python
from datetime import UTC, datetime

import pytest

from quantitative_trading.cash.models import CashTransactionType
from quantitative_trading.cash.repository import CashAccountAlreadyInitializedError, CashAccountRepository
from quantitative_trading.config import Settings
from quantitative_trading.storage.sqlite import connect, migrate


def make_repository(tmp_path):
    settings = Settings(database_path=tmp_path / "account.db")
    connection_cm = connect(settings)
    connection = connection_cm.__enter__()
    migrate(connection)
    return CashAccountRepository(connection), connection_cm


def fixed_now() -> datetime:
    return datetime(2026, 7, 7, 9, 0, tzinfo=UTC)


def test_repository_initializes_account_and_transaction(tmp_path) -> None:
    repository, connection_cm = make_repository(tmp_path)
    try:
        account = repository.initialize(50000, now=fixed_now(), note="初始本金")

        transactions = repository.list_transactions()

        assert account.cash_balance == 50000
        assert account.total_transfer_in == 50000
        assert account.total_transfer_out == 0
        assert account.net_principal == 50000
        assert transactions[0].type is CashTransactionType.INITIAL_DEPOSIT
        assert transactions[0].cash_before == 0
        assert transactions[0].cash_after == 50000
    finally:
        connection_cm.__exit__(None, None, None)


def test_repository_rejects_duplicate_initialization(tmp_path) -> None:
    repository, connection_cm = make_repository(tmp_path)
    try:
        repository.initialize(50000, now=fixed_now(), note="初始本金")

        with pytest.raises(CashAccountAlreadyInitializedError):
            repository.initialize(1000, now=fixed_now(), note="重复初始化")
    finally:
        connection_cm.__exit__(None, None, None)


def test_repository_saves_new_state_and_transaction(tmp_path) -> None:
    repository, connection_cm = make_repository(tmp_path)
    try:
        repository.initialize(50000, now=fixed_now(), note="初始本金")

        account = repository.save_state_with_transaction(
            cash_balance=51000,
            total_transfer_in=51000,
            total_transfer_out=0,
            transaction_type=CashTransactionType.TRANSFER_IN,
            amount=1000,
            cash_before=50000,
            cash_after=51000,
            now=fixed_now(),
            note="银证转入",
        )

        transactions = repository.list_transactions()
        assert account.cash_balance == 51000
        assert account.net_principal == 51000
        assert [transaction.type for transaction in transactions] == [
            CashTransactionType.INITIAL_DEPOSIT,
            CashTransactionType.TRANSFER_IN,
        ]
    finally:
        connection_cm.__exit__(None, None, None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_cash_repository.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'quantitative_trading.cash.repository'`.

- [ ] **Step 3: Implement cash repository**

Create `src/quantitative_trading/cash/repository.py`:

```python
from __future__ import annotations

import sqlite3
from datetime import datetime

from quantitative_trading.cash.models import CashAccount, CashTransaction, CashTransactionType


class CashAccountAlreadyInitializedError(ValueError):
    pass


class CashAccountNotInitializedError(ValueError):
    pass


class CashAccountRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self) -> CashAccount | None:
        row = self.connection.execute(
            """
            SELECT cash_balance, total_transfer_in, total_transfer_out, updated_at
            FROM cash_account
            WHERE id = 1
            """
        ).fetchone()
        if row is None:
            return None
        return CashAccount.model_validate(dict(row))

    def initialize(self, cash: float, *, now: datetime, note: str) -> CashAccount:
        if self.get() is not None:
            raise CashAccountAlreadyInitializedError("cash account already initialized")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO cash_account (
                  id, cash_balance, total_transfer_in, total_transfer_out, updated_at
                ) VALUES (1, ?, ?, 0, ?)
                """,
                (cash, cash, now.isoformat()),
            )
            self._insert_transaction(
                transaction_type=CashTransactionType.INITIAL_DEPOSIT,
                amount=cash,
                cash_before=0,
                cash_after=cash,
                now=now,
                note=note,
            )
        account = self.get()
        if account is None:
            raise CashAccountNotInitializedError("cash account not initialized")
        return account

    def save_state_with_transaction(
        self,
        *,
        cash_balance: float,
        total_transfer_in: float,
        total_transfer_out: float,
        transaction_type: CashTransactionType,
        amount: float,
        cash_before: float,
        cash_after: float,
        now: datetime,
        note: str,
    ) -> CashAccount:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE cash_account
                SET cash_balance = ?, total_transfer_in = ?, total_transfer_out = ?, updated_at = ?
                WHERE id = 1
                """,
                (cash_balance, total_transfer_in, total_transfer_out, now.isoformat()),
            )
            if cursor.rowcount == 0:
                raise CashAccountNotInitializedError("cash account not initialized")
            self._insert_transaction(
                transaction_type=transaction_type,
                amount=amount,
                cash_before=cash_before,
                cash_after=cash_after,
                now=now,
                note=note,
            )
        account = self.get()
        if account is None:
            raise CashAccountNotInitializedError("cash account not initialized")
        return account

    def list_transactions(self, *, limit: int = 20) -> list[CashTransaction]:
        rows = self.connection.execute(
            """
            SELECT id, type, amount, cash_before, cash_after, occurred_at, note
            FROM cash_transactions
            ORDER BY id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [CashTransaction.model_validate(dict(row)) for row in rows]

    def _insert_transaction(
        self,
        *,
        transaction_type: CashTransactionType,
        amount: float,
        cash_before: float,
        cash_after: float,
        now: datetime,
        note: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO cash_transactions (
              type, amount, cash_before, cash_after, occurred_at, note
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (transaction_type.value, amount, cash_before, cash_after, now.isoformat(), note),
        )
```

- [ ] **Step 4: Run cash repository tests**

Run:

```bash
uv run pytest tests/test_cash_repository.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quantitative_trading/cash/repository.py tests/test_cash_repository.py
git commit -m "feat: add cash account repository"
```

---

### Task 5: Cash Service Rules

**Files:**
- Create: `src/quantitative_trading/cash/service.py`
- Test: `tests/test_cash_service.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_cash_service.py`:

```python
from datetime import UTC, datetime

import pytest

from quantitative_trading.cash.repository import CashAccountRepository
from quantitative_trading.cash.service import CashService, CashTransferError, ReadOnlyCashService
from quantitative_trading.config import Settings
from quantitative_trading.storage.sqlite import connect, migrate


def make_services(tmp_path):
    settings = Settings(database_path=tmp_path / "account.db")
    connection_cm = connect(settings)
    connection = connection_cm.__enter__()
    migrate(connection)
    repository = CashAccountRepository(connection)
    return CashService(repository), ReadOnlyCashService(repository), connection_cm


def fixed_now() -> datetime:
    return datetime(2026, 7, 7, 9, 0, tzinfo=UTC)


def test_cash_service_transfer_in_increases_cash_and_principal(tmp_path) -> None:
    service, _, connection_cm = make_services(tmp_path)
    try:
        service.initialize(50000, now=fixed_now(), note="初始本金")

        account = service.transfer_in(10000, now=fixed_now(), note="银证转入")

        assert account.cash_balance == 60000
        assert account.total_transfer_in == 60000
        assert account.net_principal == 60000
    finally:
        connection_cm.__exit__(None, None, None)


def test_cash_service_transfer_out_decreases_cash_and_principal(tmp_path) -> None:
    service, _, connection_cm = make_services(tmp_path)
    try:
        service.initialize(50000, now=fixed_now(), note="初始本金")

        account = service.transfer_out(5000, now=fixed_now(), note="银证转出")

        assert account.cash_balance == 45000
        assert account.total_transfer_out == 5000
        assert account.net_principal == 45000
    finally:
        connection_cm.__exit__(None, None, None)


def test_cash_service_rejects_transfer_out_above_cash(tmp_path) -> None:
    service, _, connection_cm = make_services(tmp_path)
    try:
        service.initialize(1000, now=fixed_now(), note="初始本金")

        with pytest.raises(CashTransferError, match="cannot exceed cash balance"):
            service.transfer_out(1001, now=fixed_now(), note="超额转出")
    finally:
        connection_cm.__exit__(None, None, None)


def test_cash_service_adjust_changes_only_cash(tmp_path) -> None:
    service, _, connection_cm = make_services(tmp_path)
    try:
        service.initialize(50000, now=fixed_now(), note="初始本金")

        account = service.adjust_cash(48000, now=fixed_now(), note="手动校准")

        assert account.cash_balance == 48000
        assert account.total_transfer_in == 50000
        assert account.total_transfer_out == 0
        assert account.net_principal == 50000
    finally:
        connection_cm.__exit__(None, None, None)


def test_read_only_cash_service_exposes_no_mutation_methods(tmp_path) -> None:
    _, read_only, connection_cm = make_services(tmp_path)
    try:
        assert not hasattr(read_only, "initialize")
        assert not hasattr(read_only, "transfer_in")
        assert not hasattr(read_only, "transfer_out")
        assert not hasattr(read_only, "adjust_cash")
    finally:
        connection_cm.__exit__(None, None, None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_cash_service.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'quantitative_trading.cash.service'`.

- [ ] **Step 3: Implement cash service**

Create `src/quantitative_trading/cash/service.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from quantitative_trading.cash.models import CashAccount, CashTransaction, CashTransactionType
from quantitative_trading.cash.repository import CashAccountNotInitializedError, CashAccountRepository


class CashTransferError(ValueError):
    pass


def current_time() -> datetime:
    return datetime.now(UTC)


class ReadOnlyCashService:
    def __init__(self, repository: CashAccountRepository) -> None:
        self._repository = repository

    def get_account(self) -> CashAccount | None:
        return self._repository.get()

    def list_transactions(self, *, limit: int = 20) -> list[CashTransaction]:
        return self._repository.list_transactions(limit=limit)


class CashService(ReadOnlyCashService):
    def initialize(self, cash: float, *, now: datetime | None = None, note: str = "") -> CashAccount:
        return self._repository.initialize(cash, now=now or current_time(), note=note)

    def transfer_in(self, amount: float, *, now: datetime | None = None, note: str = "") -> CashAccount:
        account = self._require_account()
        return self._repository.save_state_with_transaction(
            cash_balance=account.cash_balance + amount,
            total_transfer_in=account.total_transfer_in + amount,
            total_transfer_out=account.total_transfer_out,
            transaction_type=CashTransactionType.TRANSFER_IN,
            amount=amount,
            cash_before=account.cash_balance,
            cash_after=account.cash_balance + amount,
            now=now or current_time(),
            note=note,
        )

    def transfer_out(self, amount: float, *, now: datetime | None = None, note: str = "") -> CashAccount:
        account = self._require_account()
        if amount > account.cash_balance:
            raise CashTransferError("transfer-out amount cannot exceed cash balance")
        if amount > account.net_principal:
            raise CashTransferError("transfer-out amount cannot exceed net principal")
        return self._repository.save_state_with_transaction(
            cash_balance=account.cash_balance - amount,
            total_transfer_in=account.total_transfer_in,
            total_transfer_out=account.total_transfer_out + amount,
            transaction_type=CashTransactionType.TRANSFER_OUT,
            amount=amount,
            cash_before=account.cash_balance,
            cash_after=account.cash_balance - amount,
            now=now or current_time(),
            note=note,
        )

    def adjust_cash(self, cash: float, *, now: datetime | None = None, note: str) -> CashAccount:
        if not note.strip():
            raise CashTransferError("cash adjustment note is required")
        account = self._require_account()
        amount = abs(cash - account.cash_balance)
        if amount == 0:
            raise CashTransferError("cash adjustment must change cash balance")
        return self._repository.save_state_with_transaction(
            cash_balance=cash,
            total_transfer_in=account.total_transfer_in,
            total_transfer_out=account.total_transfer_out,
            transaction_type=CashTransactionType.CASH_ADJUSTMENT,
            amount=amount,
            cash_before=account.cash_balance,
            cash_after=cash,
            now=now or current_time(),
            note=note,
        )

    def _require_account(self) -> CashAccount:
        account = self.get_account()
        if account is None:
            raise CashAccountNotInitializedError("cash account not initialized")
        return account
```

- [ ] **Step 4: Run cash service tests**

Run:

```bash
uv run pytest tests/test_cash_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Run all cash tests**

Run:

```bash
uv run pytest tests/test_cash_models.py tests/test_cash_repository.py tests/test_cash_service.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/quantitative_trading/cash/service.py tests/test_cash_service.py
git commit -m "feat: add cash account service"
```

---

### Task 6: Market Quote Models And Provider Boundary

**Files:**
- Create: `src/quantitative_trading/market/__init__.py`
- Create: `src/quantitative_trading/market/models.py`
- Create: `src/quantitative_trading/market/providers.py`
- Test: `tests/test_market_models.py`
- Test: `tests/test_market_providers.py`

- [ ] **Step 1: Write failing market tests**

Create `tests/test_market_models.py`:

```python
from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus


def test_quote_snapshot_accepts_ok_quote() -> None:
    quote = QuoteSnapshot.model_validate(
        {
            "symbol": "600000",
            "name": "浦发银行",
            "current_price": 10.5,
            "change_pct": 1.2,
            "data_time": "2026-07-07T10:30:00+08:00",
            "fetched_at": "2026-07-07T10:30:03+08:00",
            "source": "akshare",
            "status": "ok",
            "warning": "",
        }
    )

    assert quote.status is QuoteStatus.OK
    assert quote.current_price == 10.5
```

Create `tests/test_market_providers.py`:

```python
from datetime import UTC, datetime

from quantitative_trading.market.models import QuoteStatus
from quantitative_trading.market.providers import DisabledMarketProvider


def test_disabled_market_provider_returns_failed_quotes() -> None:
    provider = DisabledMarketProvider(now=lambda: datetime(2026, 7, 7, 2, 30, tzinfo=UTC))

    quotes = provider.get_quotes(["600000"])

    assert quotes["600000"].status is QuoteStatus.FAILED
    assert quotes["600000"].warning == "market fetch disabled"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_market_models.py tests/test_market_providers.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'quantitative_trading.market'`.

- [ ] **Step 3: Implement market models and disabled provider**

Create `src/quantitative_trading/market/__init__.py`:

```python
"""Market data adapter boundary."""
```

Create `src/quantitative_trading/market/models.py`:

```python
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class QuoteStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    STALE = "stale"


class QuoteSnapshot(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str = Field(pattern=r"^\d{6}$")
    name: str = ""
    current_price: float | None = Field(default=None, gt=0)
    change_pct: float | None = None
    data_time: datetime | None = None
    fetched_at: datetime
    source: str
    status: QuoteStatus
    warning: str = ""

    @field_validator("data_time", "fetched_at")
    @classmethod
    def datetimes_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value
```

Create `src/quantitative_trading/market/providers.py`:

```python
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus


class MarketDataProvider(Protocol):
    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        raise NotImplementedError


class DisabledMarketProvider:
    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        fetched_at = self._now()
        return {
            symbol: QuoteSnapshot(
                symbol=symbol,
                fetched_at=fetched_at,
                source="disabled",
                status=QuoteStatus.FAILED,
                warning="market fetch disabled",
            )
            for symbol in symbols
        }
```

- [ ] **Step 4: Run market tests**

Run:

```bash
uv run pytest tests/test_market_models.py tests/test_market_providers.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quantitative_trading/market tests/test_market_models.py tests/test_market_providers.py
git commit -m "feat: add market data provider boundary"
```

---

### Task 7: Account Snapshot Models And Calculation

**Files:**
- Create: `src/quantitative_trading/account/__init__.py`
- Create: `src/quantitative_trading/account/models.py`
- Create: `src/quantitative_trading/account/service.py`
- Test: `tests/test_account_models.py`
- Test: `tests/test_account_service.py`

- [ ] **Step 1: Write failing account model tests**

Create `tests/test_account_models.py`:

```python
from quantitative_trading.account.models import AccountSnapshot, AccountSnapshotStatus


def test_account_snapshot_accepts_empty_cash_not_initialized_status() -> None:
    snapshot = AccountSnapshot.model_validate(
        {
            "cash_balance": None,
            "net_principal": None,
            "market_value": None,
            "position_cost": None,
            "floating_pnl": None,
            "floating_pnl_pct": None,
            "total_assets": None,
            "total_pnl": None,
            "total_pnl_pct": None,
            "position_ratio": None,
            "available_buying_cash": None,
            "positions": [],
            "status": "cash_not_initialized",
            "warnings": ["cash account not initialized"],
            "created_at": "2026-07-07T09:00:00+08:00",
        }
    )

    assert snapshot.status is AccountSnapshotStatus.CASH_NOT_INITIALIZED
```

- [ ] **Step 2: Write failing account service tests**

Create `tests/test_account_service.py`:

```python
from datetime import UTC, datetime

from quantitative_trading.account.models import AccountSnapshotStatus, PositionValuationStatus
from quantitative_trading.account.service import AccountService
from quantitative_trading.cash.repository import CashAccountRepository
from quantitative_trading.cash.service import CashService, ReadOnlyCashService
from quantitative_trading.config import Settings
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.ledger.service import LedgerService, ReadOnlyLedgerService
from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus
from quantitative_trading.storage.sqlite import connect, migrate


class FakeMarketProvider:
    def __init__(self, quotes):
        self.quotes = quotes

    def get_quotes(self, symbols):
        return {symbol: self.quotes[symbol] for symbol in symbols if symbol in self.quotes}


def fixed_now() -> datetime:
    return datetime(2026, 7, 7, 2, 0, tzinfo=UTC)


def make_account_service(tmp_path, quotes):
    settings = Settings(database_path=tmp_path / "account.db")
    connection_cm = connect(settings)
    connection = connection_cm.__enter__()
    migrate(connection)
    ledger_repository = PositionRepository(connection)
    cash_repository = CashAccountRepository(connection)
    ledger_service = LedgerService(ledger_repository)
    cash_service = CashService(cash_repository)
    account_service = AccountService(
        ledger=ReadOnlyLedgerService(ledger_repository),
        cash=ReadOnlyCashService(cash_repository),
        market=FakeMarketProvider(quotes),
        now=fixed_now,
    )
    return ledger_service, cash_service, account_service, connection_cm


def test_account_service_calculates_snapshot(tmp_path) -> None:
    quote_time = datetime(2026, 7, 7, 2, 0, tzinfo=UTC)
    quotes = {
        "600000": QuoteSnapshot(
            symbol="600000",
            name="浦发银行",
            current_price=10.5,
            change_pct=1.2,
            data_time=quote_time,
            fetched_at=quote_time,
            source="fake",
            status=QuoteStatus.OK,
        )
    }
    ledger_service, cash_service, account_service, connection_cm = make_account_service(tmp_path, quotes)
    try:
        cash_service.initialize(50000, now=fixed_now(), note="初始本金")
        ledger_service.add_position(
            PositionInput(
                symbol="600000",
                name="浦发银行",
                quantity=1000,
                available_quantity=800,
                cost_price=9.5,
                opened_at="2026-07-06",
            ),
            now=fixed_now(),
        )

        snapshot = account_service.create_snapshot()

        assert snapshot.status is AccountSnapshotStatus.OK
        assert snapshot.cash_balance == 50000
        assert snapshot.net_principal == 50000
        assert snapshot.market_value == 10500
        assert snapshot.position_cost == 9500
        assert snapshot.floating_pnl == 1000
        assert snapshot.total_assets == 60500
        assert snapshot.total_pnl == 10500
        assert round(snapshot.position_ratio, 4) == round(10500 / 60500, 4)
    finally:
        connection_cm.__exit__(None, None, None)


def test_account_service_marks_partial_when_quote_missing(tmp_path) -> None:
    ledger_service, cash_service, account_service, connection_cm = make_account_service(tmp_path, {})
    try:
        cash_service.initialize(50000, now=fixed_now(), note="初始本金")
        ledger_service.add_position(
            PositionInput(
                symbol="600000",
                name="浦发银行",
                quantity=1000,
                available_quantity=800,
                cost_price=9.5,
                opened_at="2026-07-06",
            ),
            now=fixed_now(),
        )

        snapshot = account_service.create_snapshot()

        assert snapshot.status is AccountSnapshotStatus.PARTIAL
        assert snapshot.positions[0].status is PositionValuationStatus.FAILED
        assert snapshot.market_value is None
    finally:
        connection_cm.__exit__(None, None, None)
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_account_models.py tests/test_account_service.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'quantitative_trading.account'`.

- [ ] **Step 4: Implement account models**

Create `src/quantitative_trading/account/__init__.py`:

```python
"""Account valuation core."""
```

Create `src/quantitative_trading/account/models.py`:

```python
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class PositionValuationStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    STALE = "stale"


class AccountSnapshotStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    MARKET_DATA_UNAVAILABLE = "market_data_unavailable"
    CASH_NOT_INITIALIZED = "cash_not_initialized"


class PositionValuation(BaseModel):
    symbol: str = Field(pattern=r"^\d{6}$")
    name: str
    quantity: int = Field(ge=0)
    available_quantity: int = Field(ge=0)
    cost_price: float = Field(gt=0)
    position_cost: float
    current_price: float | None = None
    market_value: float | None = None
    floating_pnl: float | None = None
    floating_pnl_pct: float | None = None
    ledger_updated_at: datetime
    quote_data_time: datetime | None = None
    quote_fetched_at: datetime | None = None
    status: PositionValuationStatus
    warning: str = ""


class AccountSnapshot(BaseModel):
    cash_balance: float | None = None
    net_principal: float | None = None
    market_value: float | None = None
    position_cost: float | None = None
    floating_pnl: float | None = None
    floating_pnl_pct: float | None = None
    total_assets: float | None = None
    total_pnl: float | None = None
    total_pnl_pct: float | None = None
    position_ratio: float | None = None
    available_buying_cash: float | None = None
    positions: list[PositionValuation]
    status: AccountSnapshotStatus
    warnings: list[str]
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value
```

- [ ] **Step 5: Implement account service**

Create `src/quantitative_trading/account/service.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from quantitative_trading.account.models import (
    AccountSnapshot,
    AccountSnapshotStatus,
    PositionValuation,
    PositionValuationStatus,
)
from quantitative_trading.cash.service import ReadOnlyCashService
from quantitative_trading.ledger.models import Position
from quantitative_trading.ledger.service import ReadOnlyLedgerService
from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus
from quantitative_trading.market.providers import MarketDataProvider


class AccountService:
    def __init__(
        self,
        *,
        ledger: ReadOnlyLedgerService,
        cash: ReadOnlyCashService,
        market: MarketDataProvider,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._ledger = ledger
        self._cash = cash
        self._market = market
        self._now = now or (lambda: datetime.now(UTC))

    def create_snapshot(self) -> AccountSnapshot:
        created_at = self._now()
        cash_account = self._cash.get_account()
        positions = self._ledger.list_positions()
        if cash_account is None:
            return AccountSnapshot(
                positions=[],
                status=AccountSnapshotStatus.CASH_NOT_INITIALIZED,
                warnings=["cash account not initialized"],
                created_at=created_at,
            )

        quotes = self._market.get_quotes([position.symbol for position in positions])
        valuations = [self._value_position(position, quotes.get(position.symbol)) for position in positions]
        usable_values = [valuation for valuation in valuations if valuation.market_value is not None]

        if positions and len(usable_values) == 0:
            status = AccountSnapshotStatus.PARTIAL
            market_value = None
            position_cost = None
        elif len(usable_values) < len(positions):
            status = AccountSnapshotStatus.PARTIAL
            market_value = sum(valuation.market_value or 0 for valuation in usable_values)
            position_cost = sum(valuation.position_cost for valuation in usable_values)
        else:
            status = AccountSnapshotStatus.OK
            market_value = sum(valuation.market_value or 0 for valuation in usable_values)
            position_cost = sum(valuation.position_cost for valuation in usable_values)

        floating_pnl = None if market_value is None or position_cost is None else market_value - position_cost
        floating_pnl_pct = None if not position_cost else floating_pnl / position_cost
        total_assets = None if market_value is None else cash_account.cash_balance + market_value
        total_pnl = None if total_assets is None else total_assets - cash_account.net_principal
        total_pnl_pct = None if not cash_account.net_principal or total_pnl is None else total_pnl / cash_account.net_principal
        position_ratio = None if not total_assets or market_value is None else market_value / total_assets

        warnings = [valuation.warning for valuation in valuations if valuation.warning]
        return AccountSnapshot(
            cash_balance=cash_account.cash_balance,
            net_principal=cash_account.net_principal,
            market_value=market_value,
            position_cost=position_cost,
            floating_pnl=floating_pnl,
            floating_pnl_pct=floating_pnl_pct,
            total_assets=total_assets,
            total_pnl=total_pnl,
            total_pnl_pct=total_pnl_pct,
            position_ratio=position_ratio,
            available_buying_cash=cash_account.cash_balance,
            positions=valuations,
            status=status,
            warnings=warnings,
            created_at=created_at,
        )

    def _value_position(self, position: Position, quote: QuoteSnapshot | None) -> PositionValuation:
        position_cost = position.quantity * position.cost_price
        if quote is None or quote.status in {QuoteStatus.FAILED, QuoteStatus.STALE} or quote.current_price is None:
            return PositionValuation(
                symbol=position.symbol,
                name=position.name,
                quantity=position.quantity,
                available_quantity=position.available_quantity,
                cost_price=position.cost_price,
                position_cost=position_cost,
                ledger_updated_at=position.updated_at,
                status=PositionValuationStatus.FAILED,
                warning="quote unavailable",
            )

        market_value = position.quantity * quote.current_price
        floating_pnl = market_value - position_cost
        floating_pnl_pct = None if position_cost == 0 else floating_pnl / position_cost
        return PositionValuation(
            symbol=position.symbol,
            name=position.name,
            quantity=position.quantity,
            available_quantity=position.available_quantity,
            cost_price=position.cost_price,
            position_cost=position_cost,
            current_price=quote.current_price,
            market_value=market_value,
            floating_pnl=floating_pnl,
            floating_pnl_pct=floating_pnl_pct,
            ledger_updated_at=position.updated_at,
            quote_data_time=quote.data_time,
            quote_fetched_at=quote.fetched_at,
            status=PositionValuationStatus.OK,
            warning=quote.warning,
        )
```

- [ ] **Step 6: Run account tests**

Run:

```bash
uv run pytest tests/test_account_models.py tests/test_account_service.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/quantitative_trading/account tests/test_account_models.py tests/test_account_service.py
git commit -m "feat: add account snapshot calculation"
```

---

### Task 8: Account Snapshot Repository

**Files:**
- Create: `src/quantitative_trading/account/repository.py`
- Test: `tests/test_account_repository.py`

- [ ] **Step 1: Write failing snapshot repository test**

Create `tests/test_account_repository.py`:

```python
from datetime import UTC, datetime

from quantitative_trading.account.models import AccountSnapshot, AccountSnapshotStatus
from quantitative_trading.account.repository import AccountSnapshotRepository
from quantitative_trading.config import Settings
from quantitative_trading.storage.sqlite import connect, migrate


def test_repository_saves_and_reads_latest_snapshot(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "account.db")
    connection_cm = connect(settings)
    connection = connection_cm.__enter__()
    migrate(connection)
    repository = AccountSnapshotRepository(connection)
    snapshot = AccountSnapshot(
        cash_balance=50000,
        net_principal=50000,
        market_value=10500,
        position_cost=9500,
        floating_pnl=1000,
        floating_pnl_pct=1000 / 9500,
        total_assets=60500,
        total_pnl=10500,
        total_pnl_pct=10500 / 50000,
        position_ratio=10500 / 60500,
        available_buying_cash=50000,
        positions=[],
        status=AccountSnapshotStatus.OK,
        warnings=[],
        created_at=datetime(2026, 7, 7, 2, 0, tzinfo=UTC),
    )
    try:
        saved_id = repository.save(snapshot, cash_account_updated_at=snapshot.created_at, ledger_max_updated_at=snapshot.created_at)

        latest = repository.latest()

        assert saved_id == 1
        assert latest is not None
        assert latest.status is AccountSnapshotStatus.OK
        assert latest.total_assets == 60500
    finally:
        connection_cm.__exit__(None, None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_account_repository.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'quantitative_trading.account.repository'`.

- [ ] **Step 3: Implement snapshot repository**

Create `src/quantitative_trading/account/repository.py`:

```python
from __future__ import annotations

import sqlite3
from datetime import datetime

from quantitative_trading.account.models import AccountSnapshot


class AccountSnapshotRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(
        self,
        snapshot: AccountSnapshot,
        *,
        cash_account_updated_at: datetime | None,
        ledger_max_updated_at: datetime | None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO account_snapshots (
              status,
              created_at,
              cash_account_updated_at,
              ledger_max_updated_at,
              market_value,
              total_assets,
              total_pnl,
              position_ratio,
              payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.status.value,
                snapshot.created_at.isoformat(),
                None if cash_account_updated_at is None else cash_account_updated_at.isoformat(),
                None if ledger_max_updated_at is None else ledger_max_updated_at.isoformat(),
                snapshot.market_value,
                snapshot.total_assets,
                snapshot.total_pnl,
                snapshot.position_ratio,
                snapshot.model_dump_json(),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def latest(self) -> AccountSnapshot | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM account_snapshots
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return AccountSnapshot.model_validate_json(row["payload_json"])
```

- [ ] **Step 4: Run snapshot repository tests**

Run:

```bash
uv run pytest tests/test_account_repository.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quantitative_trading/account/repository.py tests/test_account_repository.py
git commit -m "feat: persist account snapshots"
```

---

### Task 9: CLI Cash And Account Commands

**Files:**
- Modify: `src/quantitative_trading/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add failing CLI tests**

Append to `tests/test_cli.py`:

```python
def test_cash_init_show_and_transfer_commands(tmp_path) -> None:
    init_result = run_cli(tmp_path, "cash", "init", "--cash", "50000", "--note", "初始本金")
    show_result = run_cli(tmp_path, "cash", "show")
    transfer_result = run_cli(tmp_path, "cash", "transfer-in", "--amount", "10000", "--note", "银证转入")
    final_show_result = run_cli(tmp_path, "cash", "show")

    assert init_result.exit_code == 0
    assert "现金账户已初始化" in init_result.output
    assert show_result.exit_code == 0
    assert "现金余额=50000.00" in show_result.output
    assert "净本金=50000.00" in show_result.output
    assert transfer_result.exit_code == 0
    assert "银证转入 10000.00" in transfer_result.output
    assert "现金余额=60000.00" in final_show_result.output


def test_cash_transfer_out_rejects_excess_cash(tmp_path) -> None:
    run_cli(tmp_path, "cash", "init", "--cash", "1000", "--note", "初始本金")

    result = run_cli(tmp_path, "cash", "transfer-out", "--amount", "1001", "--note", "超额转出")

    assert result.exit_code != 0
    assert "cannot exceed cash balance" in result.output


def test_account_snapshot_reports_cash_not_initialized(tmp_path) -> None:
    result = run_cli(tmp_path, "account", "snapshot")

    assert result.exit_code == 0
    assert "cash_not_initialized" in result.output
```

- [ ] **Step 2: Run CLI tests to verify they fail**

Run:

```bash
uv run pytest tests/test_cli.py -q
```

Expected: FAIL because `cash` and `account` command groups do not exist.

- [ ] **Step 3: Add service factory helpers**

Modify `src/quantitative_trading/cli.py` imports:

```python
import json
from quantitative_trading.account.service import AccountService
from quantitative_trading.cash.repository import CashAccountRepository
from quantitative_trading.cash.service import CashService, CashTransferError, ReadOnlyCashService
from quantitative_trading.market.providers import DisabledMarketProvider
```

Add Typer apps near existing app setup:

```python
cash_app = typer.Typer()
account_app = typer.Typer()
app.add_typer(cash_app, name="cash")
app.add_typer(account_app, name="account")
```

Extend `_services()` to also create cash services:

```python
cash_repository = CashAccountRepository(connection)
return (
    connection_cm,
    LedgerService(repository),
    ReadOnlyLedgerService(repository),
    CashService(cash_repository),
    ReadOnlyCashService(cash_repository),
)
```

Update existing callers to unpack the extra return values.

- [ ] **Step 4: Add cash CLI commands**

Add to `src/quantitative_trading/cli.py`:

```python
@cash_app.command("init")
def init_cash(
    cash: Annotated[float, typer.Option("--cash")],
    note: Annotated[str, typer.Option("--note")] = "初始本金",
) -> None:
    connection_cm, _, _, cash_service, _ = _services()
    try:
        account = cash_service.initialize(cash, note=note)
        typer.echo(f"现金账户已初始化 现金余额={account.cash_balance:.2f} 净本金={account.net_principal:.2f}")
    finally:
        connection_cm.__exit__(None, None, None)


@cash_app.command("show")
def show_cash(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    connection_cm, _, _, _, cash_read_only = _services()
    try:
        account = cash_read_only.get_account()
        if account is None:
            typer.echo("资金账户未初始化")
            return
        if json_output:
            typer.echo(account.model_dump_json())
            return
        typer.echo(
            f"现金余额={account.cash_balance:.2f} "
            f"累计转入={account.total_transfer_in:.2f} "
            f"累计转出={account.total_transfer_out:.2f} "
            f"净本金={account.net_principal:.2f} "
            f"更新={account.updated_at.isoformat()}"
        )
    finally:
        connection_cm.__exit__(None, None, None)


@cash_app.command("transfer-in")
def transfer_in(
    amount: Annotated[float, typer.Option("--amount")],
    note: Annotated[str, typer.Option("--note")] = "",
) -> None:
    connection_cm, _, _, cash_service, _ = _services()
    try:
        account = cash_service.transfer_in(amount, note=note)
        typer.echo(f"银证转入 {amount:.2f} 现金余额={account.cash_balance:.2f}")
    finally:
        connection_cm.__exit__(None, None, None)


@cash_app.command("transfer-out")
def transfer_out(
    amount: Annotated[float, typer.Option("--amount")],
    note: Annotated[str, typer.Option("--note")] = "",
) -> None:
    connection_cm, _, _, cash_service, _ = _services()
    try:
        try:
            account = cash_service.transfer_out(amount, note=note)
        except CashTransferError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(f"银证转出 {amount:.2f} 现金余额={account.cash_balance:.2f}")
    finally:
        connection_cm.__exit__(None, None, None)
```

- [ ] **Step 5: Add account snapshot CLI command**

Add to `src/quantitative_trading/cli.py`:

```python
@account_app.command("snapshot")
def account_snapshot(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    connection_cm, _, ledger_read_only, _, cash_read_only = _services()
    try:
        account_service = AccountService(
            ledger=ledger_read_only,
            cash=cash_read_only,
            market=DisabledMarketProvider(),
        )
        snapshot = account_service.create_snapshot()
        if json_output:
            typer.echo(snapshot.model_dump_json())
            return
        typer.echo(
            f"账户状态={snapshot.status.value} "
            f"现金={snapshot.cash_balance if snapshot.cash_balance is not None else '-'} "
            f"总资产={snapshot.total_assets if snapshot.total_assets is not None else '-'}"
        )
        for warning in snapshot.warnings:
            typer.echo(f"警告: {warning}")
    finally:
        connection_cm.__exit__(None, None, None)
```

- [ ] **Step 6: Run CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 7: Run full suite**

Run:

```bash
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/quantitative_trading/cli.py tests/test_cli.py
git commit -m "feat: add cash and account cli commands"
```

---

### Task 10: Debug Service Runner

**Files:**
- Create: `src/quantitative_trading/runtime/__init__.py`
- Create: `src/quantitative_trading/runtime/service_runner.py`
- Modify: `src/quantitative_trading/cli.py`
- Test: `tests/test_service_runner.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing runner tests**

Create `tests/test_service_runner.py`:

```python
from datetime import UTC, datetime

from quantitative_trading.account.models import AccountSnapshot, AccountSnapshotStatus
from quantitative_trading.runtime.service_runner import DebugServiceRunner


class FakeAccountService:
    def __init__(self) -> None:
        self.calls = 0

    def create_snapshot(self) -> AccountSnapshot:
        self.calls += 1
        return AccountSnapshot(
            positions=[],
            status=AccountSnapshotStatus.CASH_NOT_INITIALIZED,
            warnings=["cash account not initialized"],
            created_at=datetime(2026, 7, 7, 2, 0, tzinfo=UTC),
        )


def test_debug_runner_runs_one_snapshot_cycle() -> None:
    service = FakeAccountService()
    runner = DebugServiceRunner(account_service=service)

    snapshot = runner.run_once(reason="test")

    assert service.calls == 1
    assert snapshot.status is AccountSnapshotStatus.CASH_NOT_INITIALIZED
```

- [ ] **Step 2: Add failing CLI test for service run help**

Append to `tests/test_cli.py`:

```python
def test_service_run_command_is_registered(tmp_path) -> None:
    result = run_cli(tmp_path, "service", "run", "--help")

    assert result.exit_code == 0
    assert "启动调试版后台服务" in result.output
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_service_runner.py tests/test_cli.py::test_service_run_command_is_registered -q
```

Expected: FAIL because `runtime.service_runner` and `service run` do not exist.

- [ ] **Step 4: Implement debug runner**

Create `src/quantitative_trading/runtime/__init__.py`:

```python
"""Runtime service helpers."""
```

Create `src/quantitative_trading/runtime/service_runner.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

from quantitative_trading.account.models import AccountSnapshot
from quantitative_trading.account.service import AccountService


class DebugServiceRunner:
    def __init__(self, *, account_service: AccountService, log_dir: Path | None = None) -> None:
        self._account_service = account_service
        self._log_dir = log_dir

    def run_once(self, *, reason: str) -> AccountSnapshot:
        snapshot = self._account_service.create_snapshot()
        if self._log_dir is not None:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self._log_dir / "account-snapshots.jsonl"
            with log_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps({"reason": reason, "snapshot": snapshot.model_dump(mode="json")}, ensure_ascii=False))
                file.write("\n")
        return snapshot

    def start(self, *, interval_seconds: int, timezone: str) -> None:
        scheduler = BlockingScheduler(timezone=timezone)
        scheduler.add_job(
            lambda: self.run_once(reason="intraday"),
            trigger="interval",
            seconds=interval_seconds,
            id="account_snapshot_intraday",
            max_instances=1,
            replace_existing=True,
        )
        scheduler.start()
```

- [ ] **Step 5: Register `service run` command**

Add to `src/quantitative_trading/cli.py`:

```python
from quantitative_trading.config import load_settings
from quantitative_trading.runtime.service_runner import DebugServiceRunner
```

Add command:

```python
@service_app.command("run", help="启动调试版后台服务")
def run_service(once: Annotated[bool, typer.Option("--once")] = False) -> None:
    settings = load_settings()
    connection_cm, _, ledger_read_only, _, cash_read_only = _services()
    try:
        account_service = AccountService(
            ledger=ledger_read_only,
            cash=cash_read_only,
            market=DisabledMarketProvider(),
        )
        runner = DebugServiceRunner(account_service=account_service, log_dir=settings.log_dir)
        snapshot = runner.run_once(reason="startup")
        typer.echo(f"后台服务已启动 账户状态={snapshot.status.value}")
        if once:
            return
        typer.echo(f"进入调试版后台轮询 interval={settings.intraday_interval_seconds}s timezone={settings.timezone}")
        runner.start(interval_seconds=settings.intraday_interval_seconds, timezone=settings.timezone)
    finally:
        connection_cm.__exit__(None, None, None)
```

- [ ] **Step 6: Run runner and CLI tests**

Run:

```bash
uv run pytest tests/test_service_runner.py tests/test_cli.py::test_service_run_command_is_registered -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/quantitative_trading/runtime src/quantitative_trading/cli.py tests/test_service_runner.py tests/test_cli.py
git commit -m "feat: add debug service runner"
```

---

### Task 11: AkShare Provider Wrapper

**Files:**
- Modify: `src/quantitative_trading/market/providers.py`
- Modify: `tests/test_market_providers.py`

- [ ] **Step 1: Add failing AkShare wrapper tests using fake module**

Append to `tests/test_market_providers.py`:

```python
import pandas as pd

from quantitative_trading.market.providers import AkShareMarketProvider


class FakeAkShare:
    @staticmethod
    def stock_zh_a_spot_em():
        return pd.DataFrame(
            [
                {"代码": "600000", "名称": "浦发银行", "最新价": 10.5, "涨跌幅": 1.2},
                {"代码": "000001", "名称": "平安银行", "最新价": 12.3, "涨跌幅": -0.5},
            ]
        )


def test_akshare_provider_maps_dataframe_to_quotes() -> None:
    provider = AkShareMarketProvider(
        akshare_module=FakeAkShare,
        now=lambda: datetime(2026, 7, 7, 2, 30, tzinfo=UTC),
    )

    quotes = provider.get_quotes(["600000"])

    assert quotes["600000"].name == "浦发银行"
    assert quotes["600000"].current_price == 10.5
    assert quotes["600000"].change_pct == 1.2
    assert quotes["600000"].source == "akshare"
    assert quotes["600000"].status is QuoteStatus.OK
```

- [ ] **Step 2: Add pandas test dependency**

Modify `pyproject.toml` dev dependencies:

```toml
dev = [
  "pandas>=2,<3",
  "pytest>=8,<9",
]
```

- [ ] **Step 3: Run provider tests to verify they fail**

Run:

```bash
uv run pytest tests/test_market_providers.py -q
```

Expected: FAIL because `AkShareMarketProvider` is not defined.

- [ ] **Step 4: Implement AkShare wrapper**

Append to `src/quantitative_trading/market/providers.py`:

```python
class AkShareMarketProvider:
    def __init__(self, *, akshare_module=None, now: Callable[[], datetime] | None = None) -> None:
        self._akshare = akshare_module
        self._now = now or (lambda: datetime.now(UTC))

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        fetched_at = self._now()
        try:
            akshare = self._akshare
            if akshare is None:
                import akshare as akshare  # type: ignore[no-redef]
            frame = akshare.stock_zh_a_spot_em()
        except Exception as exc:
            return {
                symbol: QuoteSnapshot(
                    symbol=symbol,
                    fetched_at=fetched_at,
                    source="akshare",
                    status=QuoteStatus.FAILED,
                    warning=f"akshare quote fetch failed: {exc}",
                )
                for symbol in symbols
            }

        quotes: dict[str, QuoteSnapshot] = {}
        for symbol in symbols:
            rows = frame[frame["代码"].astype(str) == symbol]
            if rows.empty:
                quotes[symbol] = QuoteSnapshot(
                    symbol=symbol,
                    fetched_at=fetched_at,
                    source="akshare",
                    status=QuoteStatus.FAILED,
                    warning="quote not found",
                )
                continue
            row = rows.iloc[0]
            quotes[symbol] = QuoteSnapshot(
                symbol=symbol,
                name=str(row.get("名称", "")),
                current_price=float(row["最新价"]),
                change_pct=float(row.get("涨跌幅", 0)),
                data_time=fetched_at,
                fetched_at=fetched_at,
                source="akshare",
                status=QuoteStatus.OK,
                warning="",
            )
        return quotes
```

- [ ] **Step 5: Run provider tests**

Run:

```bash
uv run pytest tests/test_market_providers.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/quantitative_trading/market/providers.py tests/test_market_providers.py
git commit -m "feat: add akshare quote provider"
```

---

### Task 12: Documentation Updates

**Files:**
- Modify: `docs/project-spec.md`
- Modify: `docs/trading-policy.md`
- Modify: `docs/data-sources.md`
- Modify: `docs/recommendation-contract.md`
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `tests/test_docs_examples.py`

- [ ] **Step 1: Add failing docs checks**

Append to `tests/test_docs_examples.py`:

```python
def test_docs_document_cash_and_account_service_scope() -> None:
    project_spec = Path("docs/project-spec.md").read_text(encoding="utf-8")
    data_sources = Path("docs/data-sources.md").read_text(encoding="utf-8")
    trading_policy = Path("docs/trading-policy.md").read_text(encoding="utf-8")
    recommendation_contract = Path("docs/recommendation-contract.md").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "手动资金账户" in project_spec
    assert "账户估值" in project_spec
    assert "手动资金账户" in data_sources
    assert "净本金" in trading_policy
    assert "资金上下文" in recommendation_contract
    assert "qt cash init" in readme
    assert "qt account snapshot" in readme
    assert "qt service run" in readme
```

- [ ] **Step 2: Run docs test to verify it fails**

Run:

```bash
uv run pytest tests/test_docs_examples.py::test_docs_document_cash_and_account_service_scope -q
```

Expected: FAIL because docs do not mention the new account service scope yet.

- [ ] **Step 3: Update docs**

Add concise Chinese paragraphs to the docs:

```markdown
手动资金账户是系统计算现金余额、净本金、可用买入资金和总仓位比例的人工维护权威源。初始化本金、模拟银证转入、模拟银证转出和现金校准必须通过 CLI 或未来 API 明确记录，后台服务不得自动修改资金账户。
```

```markdown
账户估值由手动持仓台账、手动资金账户和 AkShare 行情快照共同生成。AkShare 只提供价格和涨跌幅，不提供真实持仓、成本、数量、可用数量或账户现金。
```

```markdown
买入资金约束必须同时考虑现金余额、净本金、总资产和已持仓市值。现金余额是首版可用买入资金的基础口径；后续风控可以继续叠加单票上限、总仓位上限和单日新增买入上限。
```

```markdown
未来推荐消息涉及仓位约束时，应补充资金上下文，包括现金余额、净本金、总资产、持仓市值、仓位比例和账户估值数据时间。
```

Update `.env.example`:

```text
QT_DATABASE_PATH=data/quant_trading.db
QT_LOG_DIR=data/logs
QT_MARKET_PROVIDER=akshare
QT_INTRADAY_INTERVAL_SECONDS=180
QT_TIMEZONE=Asia/Shanghai
QT_ENABLE_MARKET_FETCH=true
MX_APIKEY=your_miaoxiang_api_key_here
```

Add README commands:

````markdown
## 资金账户与账户估值

```bash
qt cash init --cash 50000 --note 初始本金
qt cash transfer-in --amount 10000 --note 银证转入
qt cash transfer-out --amount 5000 --note 银证转出
qt cash adjust --cash 48000 --note 手动校准券商可用资金
qt cash show
qt account snapshot
qt service run --once
```
````

- [ ] **Step 4: Run docs tests**

Run:

```bash
uv run pytest tests/test_docs_examples.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/project-spec.md docs/trading-policy.md docs/data-sources.md docs/recommendation-contract.md README.md .env.example tests/test_docs_examples.py
git commit -m "docs: document account service scope"
```

---

## Final Verification

- [ ] Run full tests:

```bash
uv run pytest -q
```

Expected: PASS.

- [ ] Run CLI cash smoke commands:

```powershell
$env:QT_DATABASE_PATH = "data/dev-account.db"
uv run qt cash init --cash 50000 --note 初始本金
uv run qt cash show
uv run qt cash transfer-in --amount 10000 --note 银证转入
uv run qt cash transfer-out --amount 5000 --note 银证转出
uv run qt cash show
```

Expected output includes:

```text
现金账户已初始化
现金余额=50000.00
银证转入 10000.00
银证转出 5000.00
现金余额=55000.00
净本金=55000.00
```

- [ ] Run account snapshot smoke command:

```powershell
uv run qt account snapshot
```

Expected output includes `账户状态=`. With disabled provider wiring, missing quote warnings are acceptable until live provider selection is wired into the CLI.

- [ ] Run debug service once:

```powershell
uv run qt service run --once
```

Expected output includes `后台服务已启动` and writes `data/logs/account-snapshots.jsonl`.

- [ ] Confirm no secrets or real account data were introduced:

```bash
git status --short
git diff --cached
```

Expected: no unstaged implementation changes after commits, and no real credentials, tokens, cookies, API keys, account IDs, or sensitive holdings committed.

---

## Plan Self-Review

- Spec coverage: Tasks cover runtime settings, SQLite tables, cash account state and transactions, cash CLI, quote provider boundary, AkShare wrapper, account snapshot calculation, snapshot persistence, debug service runner, docs, and verification.
- Placeholder scan: The plan contains no unresolved marker text. Each implementation step includes concrete file paths, code blocks, commands, and expected results.
- Type consistency: The plan consistently uses `CashAccount`, `CashTransaction`, `CashAccountRepository`, `CashService`, `QuoteSnapshot`, `MarketDataProvider`, `AccountSnapshot`, `AccountService`, and `DebugServiceRunner`. The CLI service factory is explicitly updated when new services are added.
