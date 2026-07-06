# Manual Ledger SQLite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first working manual position ledger slice with SQLite storage, shared core logic, CLI maintenance commands, and a read-only service check entry.

**Architecture:** The codebase will use a Python `src/` package named `quantitative_trading`. CLI and background service commands both call shared ledger service/repository code; only the CLI receives mutation methods. SQLite schema creation is handled by a small in-repo migration helper.

**Tech Stack:** Python 3.11+, uv, pytest, pydantic v2, typer, sqlite3 standard library.

---

## File Structure

- Create: `pyproject.toml`  
  Defines package metadata, dependencies, pytest configuration, and the `qt` console script.
- Create: `src/quantitative_trading/__init__.py`  
  Package version and import smoke target.
- Create: `src/quantitative_trading/config.py`  
  Loads database path from environment with a safe local default.
- Create: `src/quantitative_trading/storage/sqlite.py`  
  SQLite connection helper and schema migration.
- Create: `src/quantitative_trading/ledger/models.py`  
  Pydantic models for current position data and input validation.
- Create: `src/quantitative_trading/ledger/repository.py`  
  SQLite CRUD and atomic CSV import transaction support.
- Create: `src/quantitative_trading/ledger/service.py`  
  Read-write and read-only ledger service interfaces.
- Create: `src/quantitative_trading/cli.py`  
  Typer CLI command tree: `ledger` and `service`.
- Create: `tests/test_package_import.py`  
  Package scaffold smoke test.
- Create: `tests/test_ledger_models.py`  
  Position model validation tests.
- Create: `tests/test_sqlite_storage.py`  
  Migration test.
- Create: `tests/test_ledger_repository.py`  
  Repository persistence and atomic import tests.
- Create: `tests/test_ledger_service.py`  
  Service boundary tests, including read-only service.
- Create: `tests/test_cli.py`  
  CLI command behavior tests.

---

### Task 1: Python Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/quantitative_trading/__init__.py`
- Test: `tests/test_package_import.py`

- [ ] **Step 1: Create project configuration**

Create `pyproject.toml`:

```toml
[project]
name = "quantitative-trading"
version = "0.1.0"
description = "Personal A-share short-term quantitative decision support system."
requires-python = ">=3.11"
dependencies = [
  "pydantic>=2.7,<3",
  "typer>=0.12,<1",
]

[project.optional-dependencies]
dev = [
  "pytest>=8,<9",
]

[project.scripts]
qt = "quantitative_trading.cli:app"

[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-q"
```

- [ ] **Step 2: Write the failing package import test**

Create `tests/test_package_import.py`:

```python
from quantitative_trading import __version__


def test_package_exposes_version() -> None:
    assert __version__ == "0.1.0"
```

- [ ] **Step 3: Run the test to verify it fails**

Run:

```bash
uv run pytest tests/test_package_import.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'quantitative_trading'`.

- [ ] **Step 4: Write minimal package implementation**

Create `src/quantitative_trading/__init__.py`:

```python
__version__ = "0.1.0"
```

- [ ] **Step 5: Run the test to verify it passes**

Run:

```bash
uv run pytest tests/test_package_import.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/quantitative_trading/__init__.py tests/test_package_import.py
git commit -m "chore: scaffold python project"
```

---

### Task 2: Ledger Position Models

**Files:**
- Create: `src/quantitative_trading/ledger/__init__.py`
- Create: `src/quantitative_trading/ledger/models.py`
- Test: `tests/test_ledger_models.py`

- [ ] **Step 1: Write failing model validation tests**

Create `tests/test_ledger_models.py`:

```python
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from quantitative_trading.ledger.models import Position, PositionInput


def valid_position_input() -> dict[str, object]:
    return {
        "symbol": "600000",
        "name": "浦发银行",
        "quantity": 1000,
        "available_quantity": 800,
        "cost_price": 9.5,
        "opened_at": "2026-07-06",
        "note": "首批台账",
    }


def test_position_input_accepts_complete_valid_position() -> None:
    position = PositionInput.model_validate(valid_position_input())

    assert position.symbol == "600000"
    assert position.name == "浦发银行"
    assert position.quantity == 1000
    assert position.available_quantity == 800
    assert position.cost_price == 9.5
    assert position.opened_at == date(2026, 7, 6)
    assert position.note == "首批台账"


@pytest.mark.parametrize("symbol", ["60000", "6000000", "SH600000", "abcdef"])
def test_position_input_rejects_invalid_symbol(symbol: str) -> None:
    data = valid_position_input()
    data["symbol"] = symbol

    with pytest.raises(ValidationError):
        PositionInput.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("quantity", -1),
        ("available_quantity", -1),
        ("cost_price", 0),
        ("cost_price", -1.2),
    ],
)
def test_position_input_rejects_invalid_required_values(field: str, value: object) -> None:
    data = valid_position_input()
    data[field] = value

    with pytest.raises(ValidationError):
        PositionInput.model_validate(data)


def test_position_input_rejects_available_quantity_above_quantity() -> None:
    data = valid_position_input()
    data["quantity"] = 100
    data["available_quantity"] = 200

    with pytest.raises(ValidationError):
        PositionInput.model_validate(data)


def test_position_requires_timezone_aware_updated_at() -> None:
    data = valid_position_input()
    data["updated_at"] = datetime(2026, 7, 6, 10, 30)

    with pytest.raises(ValidationError):
        Position.model_validate(data)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_ledger_models.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'quantitative_trading.ledger'`.

- [ ] **Step 3: Write minimal model implementation**

Create `src/quantitative_trading/ledger/__init__.py`:

```python
"""Manual position ledger core."""
```

Create `src/quantitative_trading/ledger/models.py`:

```python
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PositionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    quantity: int = Field(ge=0)
    available_quantity: int = Field(ge=0)
    cost_price: float = Field(gt=0)
    opened_at: date
    note: str = ""

    @model_validator(mode="after")
    def available_quantity_cannot_exceed_quantity(self) -> "PositionInput":
        if self.available_quantity > self.quantity:
            raise ValueError("available_quantity cannot exceed quantity")
        return self


class Position(PositionInput):
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def updated_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware")
        return value
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/test_ledger_models.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quantitative_trading/ledger tests/test_ledger_models.py
git commit -m "feat: add ledger position models"
```

---

### Task 3: SQLite Configuration And Migration

**Files:**
- Create: `src/quantitative_trading/config.py`
- Create: `src/quantitative_trading/storage/__init__.py`
- Create: `src/quantitative_trading/storage/sqlite.py`
- Test: `tests/test_sqlite_storage.py`

- [ ] **Step 1: Write failing migration test**

Create `tests/test_sqlite_storage.py`:

```python
import sqlite3

from quantitative_trading.config import Settings
from quantitative_trading.storage.sqlite import connect, migrate


def test_migrate_creates_positions_table(tmp_path) -> None:
    db_path = tmp_path / "ledger.db"
    settings = Settings(database_path=db_path)

    with connect(settings) as connection:
        migrate(connection)
        columns = connection.execute("PRAGMA table_info(positions)").fetchall()

    column_names = [column["name"] for column in columns]
    assert column_names == [
        "symbol",
        "name",
        "quantity",
        "available_quantity",
        "cost_price",
        "opened_at",
        "updated_at",
        "note",
    ]


def test_connection_enforces_foreign_keys(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "ledger.db")

    with connect(settings) as connection:
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert foreign_keys == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_sqlite_storage.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'quantitative_trading.config'`.

- [ ] **Step 3: Write minimal configuration and storage implementation**

Create `src/quantitative_trading/config.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    database_path: Path = Field(default=Path("data/quant_trading.db"))


def load_settings() -> Settings:
    raw_path = os.environ.get("QT_DATABASE_PATH")
    if raw_path:
        return Settings(database_path=Path(raw_path))
    return Settings()
```

Create `src/quantitative_trading/storage/__init__.py`:

```python
"""Storage helpers."""
```

Create `src/quantitative_trading/storage/sqlite.py`:

```python
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from quantitative_trading.config import Settings


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS positions (
  symbol TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity >= 0),
  available_quantity INTEGER NOT NULL CHECK (available_quantity >= 0),
  cost_price REAL NOT NULL CHECK (cost_price > 0),
  opened_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  CHECK (available_quantity <= quantity)
);
"""


@contextmanager
def connect(settings: Settings) -> Iterator[sqlite3.Connection]:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def migrate(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)
    connection.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/test_sqlite_storage.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quantitative_trading/config.py src/quantitative_trading/storage tests/test_sqlite_storage.py
git commit -m "feat: add sqlite ledger storage"
```

---

### Task 4: Ledger Repository

**Files:**
- Create: `src/quantitative_trading/ledger/repository.py`
- Test: `tests/test_ledger_repository.py`

- [ ] **Step 1: Write failing repository tests**

Create `tests/test_ledger_repository.py`:

```python
import csv
import sqlite3
from datetime import UTC, datetime

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import (
    DuplicatePositionError,
    MissingPositionError,
    PositionRepository,
)
from quantitative_trading.storage.sqlite import connect, migrate


def make_repository(tmp_path) -> PositionRepository:
    settings = Settings(database_path=tmp_path / "ledger.db")
    connection_cm = connect(settings)
    connection = connection_cm.__enter__()
    migrate(connection)
    repository = PositionRepository(connection)
    repository._connection_cm = connection_cm
    return repository


def close_repository(repository: PositionRepository) -> None:
    repository._connection_cm.__exit__(None, None, None)


def valid_input(symbol: str = "600000") -> PositionInput:
    return PositionInput.model_validate(
        {
            "symbol": symbol,
            "name": "浦发银行",
            "quantity": 1000,
            "available_quantity": 800,
            "cost_price": 9.5,
            "opened_at": "2026-07-06",
            "note": "首批台账",
        }
    )


def fixed_now() -> datetime:
    return datetime(2026, 7, 6, 10, 30, tzinfo=UTC)


def test_repository_adds_and_gets_position(tmp_path) -> None:
    repository = make_repository(tmp_path)
    try:
        repository.add(valid_input(), now=fixed_now())

        position = repository.get("600000")

        assert position is not None
        assert position.symbol == "600000"
        assert position.name == "浦发银行"
        assert position.quantity == 1000
        assert position.updated_at == fixed_now()
    finally:
        close_repository(repository)


def test_repository_rejects_duplicate_add(tmp_path) -> None:
    repository = make_repository(tmp_path)
    try:
        repository.add(valid_input(), now=fixed_now())

        with pytest.raises(DuplicatePositionError):
            repository.add(valid_input(), now=fixed_now())
    finally:
        close_repository(repository)


def test_repository_updates_existing_position(tmp_path) -> None:
    repository = make_repository(tmp_path)
    try:
        repository.add(valid_input(), now=fixed_now())
        updated = PositionInput.model_validate(
            {
                "symbol": "600000",
                "name": "浦发银行",
                "quantity": 1200,
                "available_quantity": 1000,
                "cost_price": 9.4,
                "opened_at": "2026-07-06",
                "note": "手动调整",
            }
        )

        repository.update(updated, now=fixed_now())

        position = repository.get("600000")
        assert position is not None
        assert position.quantity == 1200
        assert position.available_quantity == 1000
        assert position.cost_price == 9.4
        assert position.note == "手动调整"
    finally:
        close_repository(repository)


def test_repository_rejects_update_for_missing_position(tmp_path) -> None:
    repository = make_repository(tmp_path)
    try:
        with pytest.raises(MissingPositionError):
            repository.update(valid_input(), now=fixed_now())
    finally:
        close_repository(repository)


def test_repository_removes_existing_position(tmp_path) -> None:
    repository = make_repository(tmp_path)
    try:
        repository.add(valid_input(), now=fixed_now())

        repository.remove("600000")

        assert repository.get("600000") is None
    finally:
        close_repository(repository)


def test_repository_lists_positions_by_symbol(tmp_path) -> None:
    repository = make_repository(tmp_path)
    try:
        repository.add(valid_input("600001"), now=fixed_now())
        repository.add(valid_input("600000"), now=fixed_now())

        symbols = [position.symbol for position in repository.list()]

        assert symbols == ["600000", "600001"]
    finally:
        close_repository(repository)


def test_repository_import_csv_is_atomic_when_a_row_is_invalid(tmp_path) -> None:
    repository = make_repository(tmp_path)
    csv_path = tmp_path / "positions.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "symbol",
                "name",
                "quantity",
                "available_quantity",
                "cost_price",
                "opened_at",
                "note",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "symbol": "600000",
                "name": "浦发银行",
                "quantity": "1000",
                "available_quantity": "800",
                "cost_price": "9.5",
                "opened_at": "2026-07-06",
                "note": "",
            }
        )
        writer.writerow(
            {
                "symbol": "600001",
                "name": "邯郸钢铁",
                "quantity": "100",
                "available_quantity": "200",
                "cost_price": "4.2",
                "opened_at": "2026-07-06",
                "note": "非法行",
            }
        )

    try:
        with pytest.raises(ValueError):
            repository.import_csv(csv_path, now=fixed_now())

        assert repository.list() == []
    finally:
        close_repository(repository)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_ledger_repository.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'quantitative_trading.ledger.repository'`.

- [ ] **Step 3: Write minimal repository implementation**

Create `src/quantitative_trading/ledger/repository.py`:

```python
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from quantitative_trading.ledger.models import Position, PositionInput


class DuplicatePositionError(ValueError):
    pass


class MissingPositionError(ValueError):
    pass


class PositionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, position: PositionInput, *, now: datetime) -> Position:
        if self.get(position.symbol) is not None:
            raise DuplicatePositionError(f"position already exists: {position.symbol}")
        persisted = self._with_updated_at(position, now)
        self.connection.execute(
            """
            INSERT INTO positions (
              symbol, name, quantity, available_quantity, cost_price,
              opened_at, updated_at, note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._to_row(persisted),
        )
        self.connection.commit()
        return persisted

    def update(self, position: PositionInput, *, now: datetime) -> Position:
        if self.get(position.symbol) is None:
            raise MissingPositionError(f"position does not exist: {position.symbol}")
        persisted = self._with_updated_at(position, now)
        self.connection.execute(
            """
            UPDATE positions
            SET name = ?, quantity = ?, available_quantity = ?, cost_price = ?,
                opened_at = ?, updated_at = ?, note = ?
            WHERE symbol = ?
            """,
            (
                persisted.name,
                persisted.quantity,
                persisted.available_quantity,
                persisted.cost_price,
                persisted.opened_at.isoformat(),
                persisted.updated_at.isoformat(),
                persisted.note,
                persisted.symbol,
            ),
        )
        self.connection.commit()
        return persisted

    def remove(self, symbol: str) -> None:
        cursor = self.connection.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
        if cursor.rowcount == 0:
            raise MissingPositionError(f"position does not exist: {symbol}")
        self.connection.commit()

    def get(self, symbol: str) -> Position | None:
        row = self.connection.execute(
            "SELECT * FROM positions WHERE symbol = ?",
            (symbol,),
        ).fetchone()
        if row is None:
            return None
        return self._from_row(row)

    def list(self) -> list[Position]:
        rows = self.connection.execute("SELECT * FROM positions ORDER BY symbol").fetchall()
        return [self._from_row(row) for row in rows]

    def import_csv(self, path: Path, *, now: datetime) -> list[Position]:
        rows = self._read_csv(path)
        try:
            inputs = [PositionInput.model_validate(row) for row in rows]
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

        persisted = [self._with_updated_at(position, now) for position in inputs]
        with self.connection:
            self.connection.execute("DELETE FROM positions")
            self.connection.executemany(
                """
                INSERT INTO positions (
                  symbol, name, quantity, available_quantity, cost_price,
                  opened_at, updated_at, note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._to_row(position) for position in persisted],
            )
        return persisted

    def _read_csv(self, path: Path) -> list[dict[str, str]]:
        with path.open(newline="", encoding="utf-8") as file:
            return list(csv.DictReader(file))

    def _with_updated_at(self, position: PositionInput, now: datetime) -> Position:
        return Position.model_validate({**position.model_dump(), "updated_at": now})

    def _to_row(self, position: Position) -> tuple[object, ...]:
        return (
            position.symbol,
            position.name,
            position.quantity,
            position.available_quantity,
            position.cost_price,
            position.opened_at.isoformat(),
            position.updated_at.isoformat(),
            position.note,
        )

    def _from_row(self, row: sqlite3.Row) -> Position:
        return Position.model_validate(dict(row))
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/test_ledger_repository.py -q
```

Expected: PASS.

- [ ] **Step 5: Run repository tests again**

Run:

```bash
uv run pytest tests/test_ledger_repository.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/quantitative_trading/ledger/repository.py tests/test_ledger_repository.py
git commit -m "feat: add sqlite position repository"
```

---

### Task 5: Ledger Service Boundaries

**Files:**
- Create: `src/quantitative_trading/ledger/service.py`
- Test: `tests/test_ledger_service.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_ledger_service.py`:

```python
from datetime import UTC, datetime

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.ledger.service import LedgerService, ReadOnlyLedgerService
from quantitative_trading.storage.sqlite import connect, migrate


def make_services(tmp_path) -> tuple[LedgerService, ReadOnlyLedgerService, object]:
    settings = Settings(database_path=tmp_path / "ledger.db")
    connection_cm = connect(settings)
    connection = connection_cm.__enter__()
    migrate(connection)
    repository = PositionRepository(connection)
    return LedgerService(repository), ReadOnlyLedgerService(repository), connection_cm


def valid_input() -> PositionInput:
    return PositionInput.model_validate(
        {
            "symbol": "600000",
            "name": "浦发银行",
            "quantity": 1000,
            "available_quantity": 800,
            "cost_price": 9.5,
            "opened_at": "2026-07-06",
            "note": "",
        }
    )


def test_ledger_service_adds_position_with_current_time(tmp_path) -> None:
    service, _, connection_cm = make_services(tmp_path)
    try:
        now = datetime(2026, 7, 6, 10, 30, tzinfo=UTC)

        position = service.add_position(valid_input(), now=now)

        assert position.updated_at == now
        assert service.get_position("600000") is not None
    finally:
        connection_cm.__exit__(None, None, None)


def test_read_only_service_can_list_positions(tmp_path) -> None:
    service, read_only, connection_cm = make_services(tmp_path)
    try:
        service.add_position(valid_input(), now=datetime(2026, 7, 6, 10, 30, tzinfo=UTC))

        positions = read_only.list_positions()

        assert [position.symbol for position in positions] == ["600000"]
    finally:
        connection_cm.__exit__(None, None, None)


def test_read_only_service_exposes_no_mutation_methods(tmp_path) -> None:
    _, read_only, connection_cm = make_services(tmp_path)
    try:
        for method_name in ["add_position", "update_position", "remove_position", "import_csv"]:
            assert not hasattr(read_only, method_name)
    finally:
        connection_cm.__exit__(None, None, None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_ledger_service.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'quantitative_trading.ledger.service'`.

- [ ] **Step 3: Write minimal service implementation**

Create `src/quantitative_trading/ledger/service.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from quantitative_trading.ledger.models import Position, PositionInput
from quantitative_trading.ledger.repository import PositionRepository


def current_time() -> datetime:
    return datetime.now(UTC)


class ReadOnlyLedgerService:
    def __init__(self, repository: PositionRepository) -> None:
        self.repository = repository

    def get_position(self, symbol: str) -> Position | None:
        return self.repository.get(symbol)

    def list_positions(self) -> list[Position]:
        return self.repository.list()


class LedgerService(ReadOnlyLedgerService):
    def add_position(self, position: PositionInput, *, now: datetime | None = None) -> Position:
        return self.repository.add(position, now=now or current_time())

    def update_position(self, position: PositionInput, *, now: datetime | None = None) -> Position:
        return self.repository.update(position, now=now or current_time())

    def remove_position(self, symbol: str) -> None:
        self.repository.remove(symbol)

    def import_csv(self, path: Path, *, now: datetime | None = None) -> list[Position]:
        return self.repository.import_csv(path, now=now or current_time())
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/test_ledger_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quantitative_trading/ledger/service.py tests/test_ledger_service.py
git commit -m "feat: add ledger service boundaries"
```

---

### Task 6: CLI Commands

**Files:**
- Create: `src/quantitative_trading/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_cli.py`:

```python
from pathlib import Path

from typer.testing import CliRunner

from quantitative_trading.cli import app


runner = CliRunner()


def run_cli(tmp_path: Path, *args: str):
    db_path = tmp_path / "ledger.db"
    return runner.invoke(app, [*args], env={"QT_DATABASE_PATH": str(db_path)})


def test_ledger_add_and_list(tmp_path) -> None:
    add_result = run_cli(
        tmp_path,
        "ledger",
        "add",
        "--symbol",
        "600000",
        "--name",
        "浦发银行",
        "--quantity",
        "1000",
        "--available-quantity",
        "800",
        "--cost-price",
        "9.5",
        "--opened-at",
        "2026-07-06",
    )
    list_result = run_cli(tmp_path, "ledger", "list")

    assert add_result.exit_code == 0
    assert "已新增持仓 600000 浦发银行" in add_result.output
    assert list_result.exit_code == 0
    assert "600000" in list_result.output
    assert "浦发银行" in list_result.output
    assert "1000" in list_result.output


def test_ledger_update_and_remove(tmp_path) -> None:
    run_cli(
        tmp_path,
        "ledger",
        "add",
        "--symbol",
        "600000",
        "--name",
        "浦发银行",
        "--quantity",
        "1000",
        "--available-quantity",
        "800",
        "--cost-price",
        "9.5",
        "--opened-at",
        "2026-07-06",
    )

    update_result = run_cli(
        tmp_path,
        "ledger",
        "update",
        "600000",
        "--name",
        "浦发银行",
        "--quantity",
        "1200",
        "--available-quantity",
        "1000",
        "--cost-price",
        "9.4",
        "--opened-at",
        "2026-07-06",
        "--note",
        "手动调整",
    )
    remove_result = run_cli(tmp_path, "ledger", "remove", "600000")
    list_result = run_cli(tmp_path, "ledger", "list")

    assert update_result.exit_code == 0
    assert "已更新持仓 600000" in update_result.output
    assert remove_result.exit_code == 0
    assert "已删除持仓 600000" in remove_result.output
    assert "暂无持仓" in list_result.output


def test_ledger_import_and_export(tmp_path) -> None:
    csv_path = tmp_path / "positions.csv"
    csv_path.write_text(
        "symbol,name,quantity,available_quantity,cost_price,opened_at,note\n"
        "600000,浦发银行,1000,800,9.5,2026-07-06,首批\n",
        encoding="utf-8",
    )

    import_result = run_cli(tmp_path, "ledger", "import", str(csv_path))
    export_result = run_cli(tmp_path, "ledger", "export")

    assert import_result.exit_code == 0
    assert "已导入 1 条持仓" in import_result.output
    assert export_result.exit_code == 0
    assert "symbol,name,quantity,available_quantity,cost_price,opened_at,note" in export_result.output
    assert "600000,浦发银行,1000,800,9.5,2026-07-06,首批" in export_result.output


def test_service_check_reads_ledger(tmp_path) -> None:
    result = run_cli(tmp_path, "service", "check")

    assert result.exit_code == 0
    assert "服务检查通过" in result.output
    assert "当前持仓数量: 0" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_cli.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'quantitative_trading.cli'`.

- [ ] **Step 3: Write minimal CLI implementation**

Create `src/quantitative_trading/cli.py`:

```python
from __future__ import annotations

import csv
import sys
from pathlib import Path

import typer

from quantitative_trading.config import load_settings
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import DuplicatePositionError, MissingPositionError, PositionRepository
from quantitative_trading.ledger.service import LedgerService, ReadOnlyLedgerService
from quantitative_trading.storage.sqlite import connect, migrate


app = typer.Typer(help="A-share short-term decision support CLI.")
ledger_app = typer.Typer(help="维护手动持仓台账。")
service_app = typer.Typer(help="后台服务辅助命令。")
app.add_typer(ledger_app, name="ledger")
app.add_typer(service_app, name="service")


def _services():
    settings = load_settings()
    connection_cm = connect(settings)
    connection = connection_cm.__enter__()
    migrate(connection)
    repository = PositionRepository(connection)
    return connection_cm, LedgerService(repository), ReadOnlyLedgerService(repository)


def _position_input(
    symbol: str,
    name: str,
    quantity: int,
    available_quantity: int,
    cost_price: float,
    opened_at: str,
    note: str,
) -> PositionInput:
    return PositionInput.model_validate(
        {
            "symbol": symbol,
            "name": name,
            "quantity": quantity,
            "available_quantity": available_quantity,
            "cost_price": cost_price,
            "opened_at": opened_at,
            "note": note,
        }
    )


@ledger_app.command("add")
def add_position(
    symbol: str = typer.Option(...),
    name: str = typer.Option(...),
    quantity: int = typer.Option(...),
    available_quantity: int = typer.Option(..., "--available-quantity"),
    cost_price: float = typer.Option(..., "--cost-price"),
    opened_at: str = typer.Option(..., "--opened-at"),
    note: str = typer.Option("", "--note"),
) -> None:
    connection_cm, service, _ = _services()
    try:
        position = _position_input(symbol, name, quantity, available_quantity, cost_price, opened_at, note)
        service.add_position(position)
        typer.echo(f"已新增持仓 {symbol} {name}")
    except DuplicatePositionError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        connection_cm.__exit__(None, None, None)


@ledger_app.command("update")
def update_position(
    symbol: str,
    name: str = typer.Option(...),
    quantity: int = typer.Option(...),
    available_quantity: int = typer.Option(..., "--available-quantity"),
    cost_price: float = typer.Option(..., "--cost-price"),
    opened_at: str = typer.Option(..., "--opened-at"),
    note: str = typer.Option("", "--note"),
) -> None:
    connection_cm, service, _ = _services()
    try:
        position = _position_input(symbol, name, quantity, available_quantity, cost_price, opened_at, note)
        service.update_position(position)
        typer.echo(f"已更新持仓 {symbol}")
    except MissingPositionError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        connection_cm.__exit__(None, None, None)


@ledger_app.command("remove")
def remove_position(symbol: str) -> None:
    connection_cm, service, _ = _services()
    try:
        service.remove_position(symbol)
        typer.echo(f"已删除持仓 {symbol}")
    except MissingPositionError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        connection_cm.__exit__(None, None, None)


@ledger_app.command("list")
def list_positions() -> None:
    connection_cm, _, read_only = _services()
    try:
        positions = read_only.list_positions()
        if not positions:
            typer.echo("暂无持仓")
            return
        for position in positions:
            typer.echo(
                f"{position.symbol} {position.name} "
                f"数量={position.quantity} 可用={position.available_quantity} "
                f"成本={position.cost_price} 更新={position.updated_at.isoformat()}"
            )
    finally:
        connection_cm.__exit__(None, None, None)


@ledger_app.command("import")
def import_positions(path: Path) -> None:
    connection_cm, service, _ = _services()
    try:
        positions = service.import_csv(path)
        typer.echo(f"已导入 {len(positions)} 条持仓")
    finally:
        connection_cm.__exit__(None, None, None)


@ledger_app.command("export")
def export_positions() -> None:
    connection_cm, _, read_only = _services()
    try:
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(["symbol", "name", "quantity", "available_quantity", "cost_price", "opened_at", "note"])
        for position in read_only.list_positions():
            writer.writerow(
                [
                    position.symbol,
                    position.name,
                    position.quantity,
                    position.available_quantity,
                    position.cost_price,
                    position.opened_at.isoformat(),
                    position.note,
                ]
            )
    finally:
        connection_cm.__exit__(None, None, None)


@service_app.command("check")
def service_check() -> None:
    connection_cm, _, read_only = _services()
    try:
        positions = read_only.list_positions()
        typer.echo("服务检查通过")
        typer.echo(f"当前持仓数量: {len(positions)}")
    finally:
        connection_cm.__exit__(None, None, None)
```

- [ ] **Step 4: Run CLI tests to verify they pass**

Run:

```bash
uv run pytest tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 5: Run all tests**

Run:

```bash
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/quantitative_trading/cli.py tests/test_cli.py
git commit -m "feat: add manual ledger cli"
```

---

### Task 7: Documentation And Verification

**Files:**
- Modify: `README.md`
- Modify: `.env.example`

- [ ] **Step 1: Write a failing docs smoke test**

Create `tests/test_docs_examples.py`:

```python
from pathlib import Path


def test_readme_documents_manual_ledger_commands() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "qt ledger add" in readme
    assert "qt ledger list" in readme
    assert "qt service check" in readme
    assert "不会自动下单" in readme
```

- [ ] **Step 2: Run docs test to verify it fails**

Run:

```bash
uv run pytest tests/test_docs_examples.py -q
```

Expected: FAIL because `README.md` does not exist or lacks the command examples.

- [ ] **Step 3: Add README usage documentation**

Create `README.md`:

````markdown
# quantitative-trading

个人 A 股短线量化决策辅助系统。系统只输出可解释建议，不会自动下单，不控制真实交易客户端，不绕过人工确认。

## 手动持仓台账

首版以手动持仓台账作为真实持仓、成本价、持仓数量和可用数量的唯一权威源。

设置本地 SQLite 路径：

```bash
set QT_DATABASE_PATH=data/quant_trading.db
```

新增持仓：

```bash
qt ledger add --symbol 600000 --name 浦发银行 --quantity 1000 --available-quantity 1000 --cost-price 9.50 --opened-at 2026-07-06
```

查看持仓：

```bash
qt ledger list
```

后台服务检查：

```bash
qt service check
```
````

Update `.env.example`:

```text
# Copy to .env for local development if needed.
# Do not commit real API keys.
QT_DATABASE_PATH=data/quant_trading.db
MX_APIKEY=your_miaoxiang_api_key_here
```

- [ ] **Step 4: Run docs test to verify it passes**

Run:

```bash
uv run pytest tests/test_docs_examples.py -q
```

Expected: PASS.

- [ ] **Step 5: Run full verification**

Run:

```bash
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add README.md .env.example tests/test_docs_examples.py
git commit -m "docs: document manual ledger commands"
```

---

## Final Verification

- [ ] Run all tests:

```bash
uv run pytest -q
```

Expected: PASS.

- [ ] Run CLI smoke commands against a temporary database:

```bash
$env:QT_DATABASE_PATH = "data/dev-ledger.db"
uv run qt service check
uv run qt ledger add --symbol 600000 --name 浦发银行 --quantity 1000 --available-quantity 1000 --cost-price 9.50 --opened-at 2026-07-06
uv run qt ledger list
```

Expected:

```text
服务检查通过
当前持仓数量: 0
已新增持仓 600000 浦发银行
600000 浦发银行 数量=1000 可用=1000 成本=9.5
```

- [ ] Confirm no secrets or real account data were introduced:

```bash
git diff --cached
git status --short
```

Expected: only source, tests, README, and `.env.example` changes. No real credentials, account data, token, cookie, or API key values.

---

## Plan Self-Review

- Spec coverage: The plan covers SQLite storage, shared service/repository, CLI add/update/remove/import/list/export, service check read path, validation, atomic import, and tests.
- Placeholder scan: The plan contains no unresolved marker steps. Each code-writing step includes concrete file content or concrete command examples.
- Type consistency: The plan consistently uses `PositionInput`, `Position`, `PositionRepository`, `LedgerService`, and `ReadOnlyLedgerService`. CLI and service commands use the same shared service layer.
