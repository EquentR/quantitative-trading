# Manual Position Ledger SQLite Design

## Context

This project is a personal A-share short-term quantitative decision support system. It must not place real orders, control real trading clients, read real trading credentials, or describe outputs as guaranteed profits. All real position, cost, quantity, and available quantity data must come from the manual position ledger.

The first implementation slice is the manual position ledger closed loop. It provides the trusted position source that later universe building, strategy, risk control, recommendations, notifications, and audit logs will share.

## Goals

- Provide a Python project foundation for the manual ledger slice.
- Store current real positions in a local SQLite database.
- Expose a shared ledger service used by both CLI and background service code.
- Let the CLI add, update, remove, import, list, and export positions.
- Keep the background service read-only with respect to the real position ledger.
- Validate ledger data before it can enter downstream strategy or risk logic.
- Preserve timestamps needed by the recommendation contract.

## Non-Goals

- No automatic real order placement.
- No simulated clicking of real trading clients.
- No real account password, token, cookie, or API key handling.
- No strategy signal generation beyond what is needed to prove ledger access.
- No AkShare or Eastmoney adapter implementation in this slice.
- No full audit log or recommendation generation in this slice.

## Recommended Approach

Use SQLite from the start instead of a JSON file. The ledger will later need snapshots, audit references, manual feedback, import history, and replay queries. SQLite gives those future paths without forcing an early migration.

Use a small in-repo SQL migration mechanism for the first slice rather than Alembic. A single local database and a small number of tables do not justify a full migration framework yet. The design keeps migrations explicit so Alembic can be introduced later without changing repository and service boundaries.

## Architecture

The implementation will separate concerns into focused modules:

- `models`: pydantic models and enums for ledger records and command inputs.
- `storage`: SQLite connection handling and migrations.
- `ledger`: repository and service APIs for reading and mutating current positions.
- `cli`: Typer-based CLI commands that call the ledger service.
- `service`: background entry point that can check and read the ledger but cannot mutate it.

CLI and background service share the same repository and service classes. The CLI receives a read-write ledger service. The background service receives a read-only ledger service or only calls read methods.

## Data Model

The first table is `positions`.

```sql
CREATE TABLE positions (
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
```

Field rules:

- `symbol`: six-digit A-share code for the first slice.
- `name`: non-empty stock name.
- `quantity`: current manual holding quantity, zero or greater.
- `available_quantity`: quantity available for sale, zero or greater and not greater than `quantity`.
- `cost_price`: manual cost price, greater than zero.
- `opened_at`: ISO date string for the position open date.
- `updated_at`: timezone-aware ISO datetime set by the system on every mutation.
- `note`: optional manual note stored as an empty string when omitted.

Future tables can include `position_events`, `ledger_snapshots`, `audit_logs`, and `manual_feedback` without changing this slice's public service API.

## CLI Commands

The first CLI command group is `qt ledger`.

```text
qt ledger list
qt ledger add --symbol 600000 --name 浦发银行 --quantity 1000 --available-quantity 1000 --cost-price 9.50 --opened-at 2026-07-06
qt ledger update 600000 --quantity 1200 --available-quantity 1000 --cost-price 9.40 --note 手动调整
qt ledger remove 600000
qt ledger import positions.csv
qt ledger export
qt service check
```

The import CSV columns are:

```text
symbol,name,quantity,available_quantity,cost_price,opened_at,note
```

Import validates every row before writing. If any row is invalid, the import fails without partially mutating the ledger.

## Background Service Contract

The initial background command is `qt service check`. It verifies that configuration can be loaded, the SQLite database can be opened, migrations can be applied, and current positions can be listed.

The background service must not call ledger mutation methods. This is tested by using a read-only service interface in the service layer.

## Error Handling

Validation errors should be explicit and user-facing in CLI output. Storage errors should keep enough context to diagnose which operation failed, without exposing secrets or sensitive account data.

Duplicate `symbol` on add fails with a clear error. Updating or removing a missing symbol fails with a clear error. Importing invalid CSV data fails before any row is committed.

## Testing

Development follows TDD. Tests are written and observed failing before production code is added.

Required coverage for this slice:

- Model validation accepts complete valid positions.
- Model validation rejects invalid symbol, negative quantity, unavailable quantity greater than total quantity, missing name, and non-positive cost price.
- SQLite migrations create the `positions` table.
- Repository add, update, remove, get, and list operations persist correctly.
- CSV import is atomic on invalid data.
- CLI commands call the shared ledger service and return useful output.
- Background service check can read the ledger but has no write path.

## Documentation Impact

This design implements existing docs rather than changing strategy, risk, data source, or recommendation contracts. No project policy document needs a semantic update in this slice. If later implementation changes ledger fields or recommendation-visible context, `docs/data-sources.md` and `docs/recommendation-contract.md` must be updated in the same change.
