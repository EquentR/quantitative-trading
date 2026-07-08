import csv
from io import StringIO

from tests.test_api_positions import authenticated_client


def watchlist_payload(symbol: str = "600000") -> dict[str, object]:
    return {
        "symbol": symbol,
        "name": "浦发银行",
        "rank": 1,
        "plan_enabled": False,
        "note": "观察",
    }


def test_watchlist_crud_and_plan_switch(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

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


def test_watchlist_import_export_and_sync(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    import_response = client.post(
        "/api/v1/watchlist/pinned/import",
        json={"items": [watchlist_payload("600000"), watchlist_payload("000001")]},
        headers=headers,
    )
    export_response = client.get("/api/v1/watchlist/pinned/export-csv", headers=headers)
    sync_response = client.post(
        "/api/v1/watchlist/pinned/sync",
        json={
            "items": [
                {
                    "symbol": "600000",
                    "name": "浦发银行同步",
                    "rank": 2,
                    "plan_enabled": False,
                    "note": "同步备注不应覆盖",
                }
            ]
        },
        headers=headers,
    )

    assert import_response.status_code == 200
    assert [item["symbol"] for item in import_response.json()] == ["000001", "600000"]
    assert export_response.status_code == 200
    reader = csv.DictReader(StringIO(export_response.text))
    rows = list(reader)
    assert rows[0]["symbol"] == "000001"
    assert rows[0]["plan_enabled"] == "false"
    assert sync_response.status_code == 200
    synced = {item["symbol"]: item for item in sync_response.json()}
    assert synced["600000"]["name"] == "浦发银行同步"
    assert synced["600000"]["source"] == "manual_synced"
    assert synced["600000"]["note"] == "观察"


def test_watchlist_csv_import_accepts_common_true_values(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    csv_text = (
        "symbol,name,rank,plan_enabled,note\n"
        "600000,浦发银行,1,yes,观察\n"
    )

    import_response = client.post(
        "/api/v1/watchlist/pinned/import-csv",
        files={"file": ("watchlist.csv", csv_text, "text/csv")},
        headers=headers,
    )

    assert import_response.status_code == 200
    assert import_response.json()[0]["plan_enabled"] is True


def test_watchlist_csv_import_bad_header_returns_validation_error(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
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
