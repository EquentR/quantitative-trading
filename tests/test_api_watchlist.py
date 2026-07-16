import csv
from datetime import UTC, date, datetime
from io import StringIO

from fastapi.testclient import TestClient

from tests.test_api_positions import authenticated_client
from quantitative_trading.api.app import create_app
from quantitative_trading.config import Settings
from quantitative_trading.instrument.adapters import InstrumentDirectorySnapshot
from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentMetadata,
    InstrumentType,
    SettlementCycle,
)
from quantitative_trading.instrument.repository import InstrumentRepository
from quantitative_trading.storage.sqlite import connect


def watchlist_payload(symbol: str = "600000") -> dict[str, object]:
    return {
        "symbol": symbol,
        "name": "浦发银行",
        "rank": 1,
        "plan_enabled": False,
        "note": "观察",
    }


def seed_verified_a_share(tmp_path, symbol: str, name: str) -> None:
    with connect(Settings(database_path=tmp_path / "api.db")) as connection:
        existing = InstrumentRepository(connection).list_active()
        InstrumentRepository(connection).replace_catalog(
            [
                *existing,
                InstrumentMetadata(
                    symbol=symbol,
                    name=name,
                    exchange=Exchange.SH,
                    instrument_type=InstrumentType.A_SHARE,
                    settlement_cycle=SettlementCycle.T1,
                    metadata_source="test-directory",
                    metadata_checked_at=datetime(2026, 7, 15, 2, 0, tzinfo=UTC),
                    rule_version="test-rules-v1",
                ),
            ]
        )


class ImportDirectoryAdapter:
    sources = ("test-directory",)

    def __init__(self) -> None:
        self.calls: list[date] = []

    def fetch(self, trade_date: date) -> InstrumentDirectorySnapshot:
        self.calls.append(trade_date)
        return InstrumentDirectorySnapshot(
            items=[
                InstrumentMetadata(
                    symbol="600000",
                    name="浦发银行",
                    exchange=Exchange.SH,
                    instrument_type=InstrumentType.A_SHARE,
                    settlement_cycle=SettlementCycle.T1,
                    metadata_source="test-directory",
                    metadata_checked_at=datetime(2026, 7, 15, 2, 0, tzinfo=UTC),
                    rule_version="test-rules-v1",
                )
            ],
            source_trade_dates={"test-directory": trade_date},
            warnings=[],
        )


class UnavailableImportDirectoryAdapter:
    sources = ("test-directory",)

    def fetch(self, trade_date: date) -> InstrumentDirectorySnapshot:
        del trade_date
        raise RuntimeError("directory offline")


def import_client(tmp_path) -> tuple[TestClient, dict[str, str], ImportDirectoryAdapter]:
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    adapter = ImportDirectoryAdapter()
    client = TestClient(create_app(settings, instrument_directory_adapter=adapter))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return client, headers, adapter


def unavailable_import_client(tmp_path) -> tuple[TestClient, dict[str, str]]:
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    client = TestClient(
        create_app(
            settings,
            instrument_directory_adapter=UnavailableImportDirectoryAdapter(),
        )
    )
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    return client, {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_watchlist_crud_and_plan_switch(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    seed_verified_a_share(tmp_path, "600000", "浦发银行")

    payload = watchlist_payload()
    create_response = client.post("/api/v1/watchlist/pinned", json=payload, headers=headers)
    assert create_response.status_code == 201
    assert create_response.json()["plan_enabled"] is False

    update_response = client.put(
        "/api/v1/watchlist/pinned/600000",
        json={**payload, "plan_enabled": True},
        headers=headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["plan_enabled"] is True

    list_response = client.get("/api/v1/watchlist/pinned", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()[0]["symbol"] == "600000"

    delete_response = client.delete("/api/v1/watchlist/pinned/600000", headers=headers)
    assert delete_response.status_code == 204


def test_watchlist_import_and_export(tmp_path) -> None:
    client, headers, _adapter = import_client(tmp_path)

    import_response = client.post(
        "/api/v1/watchlist/pinned/import",
        json={"items": [watchlist_payload("600000"), watchlist_payload("000001")]},
        headers=headers,
    )
    export_response = client.get("/api/v1/watchlist/pinned/export-csv", headers=headers)
    assert import_response.status_code == 200
    assert [item["symbol"] for item in import_response.json()["items"]] == [
        "000001",
        "600000",
    ]
    assert export_response.status_code == 200
    reader = csv.DictReader(StringIO(export_response.text))
    rows = list(reader)
    assert rows[0]["symbol"] == "000001"
    assert rows[0]["plan_enabled"] == "false"


def test_watchlist_json_import_envelope_resolves_directory_and_warns_for_unknown(
    tmp_path,
) -> None:
    client, headers, adapter = import_client(tmp_path)

    response = client.post(
        "/api/v1/watchlist/pinned/import",
        json={
            "items": [
                {**watchlist_payload("600000"), "plan_enabled": True},
                {**watchlist_payload("123456"), "plan_enabled": True, "rank": 2},
            ]
        },
        headers=headers,
    )

    assert response.status_code == 200
    assert adapter.calls
    body = response.json()
    assert [item["symbol"] for item in body["items"]] == ["600000", "123456"]
    assert body["items"][0]["plan_enabled"] is True
    assert body["items"][1]["plan_enabled"] is False
    assert body["warnings"] == [
        "123456 instrument metadata is unavailable or unverified; plan remains disabled"
    ]


def test_watchlist_csv_import_envelope_returns_validation_warnings(tmp_path) -> None:
    client, headers, _adapter = import_client(tmp_path)
    csv_text = (
        "symbol,name,rank,plan_enabled,note\n"
        "123456,未知证券,1,true,观察\n"
    )

    response = client.post(
        "/api/v1/watchlist/pinned/import-csv",
        files={"file": ("watchlist.csv", csv_text, "text/csv")},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["plan_enabled"] is False
    assert response.json()["warnings"] == [
        "123456 instrument metadata is unavailable or unverified; plan remains disabled"
    ]


def test_watchlist_import_envelope_degrades_safely_when_directory_is_unavailable(
    tmp_path,
) -> None:
    client, headers = unavailable_import_client(tmp_path)

    response = client.post(
        "/api/v1/watchlist/pinned/import",
        json={"items": [{**watchlist_payload("123456"), "plan_enabled": True}]},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["plan_enabled"] is False
    assert response.json()["warnings"] == [
        "instrument directory is unavailable; unverified imported items remain disabled",
        "123456 instrument metadata is unavailable or unverified; plan remains disabled",
    ]


def test_legacy_watchlist_sync_is_retired_for_any_authenticated_body(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    client.post(
        "/api/v1/watchlist/pinned",
        json=watchlist_payload(),
        headers=headers,
    )

    responses = [
        client.post("/api/v1/watchlist/pinned/sync", headers=headers),
        client.post(
            "/api/v1/watchlist/pinned/sync",
            content=b"{not-json",
            headers={**headers, "Content-Type": "application/json"},
        ),
        client.post(
            "/api/v1/watchlist/pinned/sync",
            json={"items": [watchlist_payload("000001")]},
            headers=headers,
        ),
    ]

    assert [response.status_code for response in responses] == [410, 410, 410]
    for response in responses:
        assert response.json()["error"]["code"] == "watchlist_sync_payload_retired"
        assert response.json()["error"]["details"] == {
            "preview": "/api/v1/instruments/eastmoney-candidates",
            "selection": "/api/v1/watchlist/pinned/select",
        }
    items = client.get("/api/v1/watchlist/pinned", headers=headers).json()
    assert [item["symbol"] for item in items] == ["600000"]


def test_watchlist_csv_import_accepts_common_true_values(tmp_path) -> None:
    client, headers, _adapter = import_client(tmp_path)
    csv_text = (
        "symbol,name,rank,plan_enabled,note\n"
        "600000,浦发银行,1,yes,观察\n"
    )

    import_response = client.post(
        "/api/v1/watchlist/pinned/import-csv?response=legacy",
        files={"file": ("watchlist.csv", csv_text, "text/csv")},
        headers=headers,
    )

    assert import_response.status_code == 200
    assert import_response.json()[0]["plan_enabled"] is True


def test_watchlist_csv_import_bad_header_returns_validation_error(tmp_path) -> None:
    client, headers, _adapter = import_client(tmp_path)
    csv_text = (
        "symbol,name,rank,plan_enabled,note,extra\n"
        "600000,浦发银行,1,true,观察,x\n"
    )

    response = client.post(
        "/api/v1/watchlist/pinned/import-csv",
        files={"file": ("watchlist.csv", csv_text, "text/csv")},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_watchlist_update_path_body_symbol_mismatch_returns_validation_error(
    tmp_path,
) -> None:
    client, headers = authenticated_client(tmp_path)

    response = client.put(
        "/api/v1/watchlist/pinned/600000",
        json=watchlist_payload("000001"),
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
