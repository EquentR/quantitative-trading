from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
import pytest

from quantitative_trading.api.app import create_app
from quantitative_trading.api.auth import (
    AuthAlreadyConfiguredError,
    AuthService,
    AuthSetupRequiredError,
    InvalidCredentialsError,
    InvalidTokenError,
)
from quantitative_trading.config import Settings
from quantitative_trading.storage.api_auth import ApiAuthRepository
from quantitative_trading.storage.sqlite import connect, migrate


NOW = datetime(2026, 7, 7, 2, 0, tzinfo=UTC)


def test_auth_status_reports_setup_required_without_password(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "api.db")
    client = TestClient(create_app(settings))

    response = client.get("/api/v1/service/status")

    assert response.status_code == 200
    assert response.json()["auth_status"] == "setup_required"


def test_startup_password_blocks_public_setup_takeover(tmp_path) -> None:
    settings = Settings(
        database_path=tmp_path / "api.db",
        api_access_password="startup-password",
    )
    client = TestClient(create_app(settings))

    status_response = client.get("/api/v1/service/status")
    setup_response = client.post(
        "/api/v1/auth/setup-password",
        json={"password": "attacker-password"},
    )
    startup_login_response = client.post(
        "/api/v1/auth/login",
        json={"password": "startup-password"},
    )
    attacker_login_response = client.post(
        "/api/v1/auth/login",
        json={"password": "attacker-password"},
    )

    assert status_response.status_code == 200
    assert status_response.json()["auth_status"] == "configured"
    assert setup_response.status_code == 409
    assert setup_response.json()["error"]["code"] == "auth_already_configured"
    assert startup_login_response.status_code == 200
    assert attacker_login_response.status_code == 401


def test_setup_password_then_login_and_me(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "api.db")
    client = TestClient(create_app(settings))

    setup_response = client.post(
        "/api/v1/auth/setup-password",
        json={"password": "local-password"},
    )
    login_response = client.post(
        "/api/v1/auth/login",
        json={"password": "local-password"},
    )
    token = login_response.json()["access_token"]
    me_response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert setup_response.status_code == 200
    assert setup_response.json() == {"auth_status": "configured"}
    assert login_response.status_code == 200
    assert login_response.json()["token_type"] == "bearer"
    assert me_response.status_code == 200
    assert me_response.json() == {"user": "local"}


def test_protected_endpoint_requires_auth_after_setup(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "api.db")
    client = TestClient(create_app(settings))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})

    response = client.get("/api/v1/positions")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_login_before_setup_returns_setup_required_error(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "api.db")
    client = TestClient(create_app(settings))

    response = client.post("/api/v1/auth/login", json={"password": "local-password"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "auth_setup_required"


def test_login_with_wrong_password_returns_unauthorized(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "api.db")
    client = TestClient(create_app(settings))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})

    response = client.post("/api/v1/auth/login", json={"password": "wrong-password"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_setup_password_twice_returns_conflict(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "api.db")
    client = TestClient(create_app(settings))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})

    response = client.post("/api/v1/auth/setup-password", json={"password": "other-password"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "auth_already_configured"


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Basic token",
        "Bearer",
        "Bearer not-a-token",
    ],
)
def test_me_rejects_missing_or_malformed_authorization(tmp_path, authorization: str | None) -> None:
    settings = Settings(database_path=tmp_path / "api.db")
    client = TestClient(create_app(settings))
    headers = {} if authorization is None else {"Authorization": authorization}

    response = client.get("/api/v1/auth/me", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert "not-a-token" not in response.text


def test_protected_endpoint_rejects_expired_token(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "api.db", api_token_ttl_seconds=60)
    with connect(settings) as connection:
        migrate(connection)
        service = AuthService(ApiAuthRepository(connection), token_ttl_seconds=60)
        service.setup_password("local-password", now=NOW)
        login = service.login("local-password", now=NOW)
    client = TestClient(create_app(settings))

    response = client.get(
        "/api/v1/positions",
        headers={"Authorization": f"Bearer {login.access_token}"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_logout_is_client_side_acknowledgement(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "api.db")
    client = TestClient(create_app(settings))

    response = client.post("/api/v1/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_app_migrates_once_not_per_request(monkeypatch, tmp_path) -> None:
    import quantitative_trading.api.dependencies as api_dependencies

    calls = []
    real_migrate = api_dependencies.migrate

    def counting_migrate(connection):
        calls.append("migrate")
        real_migrate(connection)

    monkeypatch.setattr(api_dependencies, "migrate", counting_migrate)
    settings = Settings(database_path=tmp_path / "api.db")

    client = TestClient(create_app(settings))
    client.get("/api/v1/service/status")
    client.get("/api/v1/service/status")

    assert calls == ["migrate"]


def test_auth_repository_starts_unconfigured(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = ApiAuthRepository(connection)

        state = repository.get()

    assert state.password_hash is None
    assert state.token_secret != ""
    assert state.updated_at.tzinfo is not None


def test_auth_repository_get_reuses_generated_token_secret(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = ApiAuthRepository(connection)

        initial = repository.get()
        loaded = repository.get()

    assert loaded.token_secret == initial.token_secret


def test_auth_repository_saves_password_hash_without_losing_token_secret(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = ApiAuthRepository(connection)
        initial = repository.get()

        saved = repository.save_password_hash("hash-value", now=NOW)

    assert saved.password_hash == "hash-value"
    assert saved.token_secret == initial.token_secret
    assert saved.updated_at == NOW


def test_auth_repository_saves_token_secret_without_losing_password_hash(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = ApiAuthRepository(connection)
        repository.save_password_hash("hash-value", now=NOW)

        saved = repository.save_token_secret("rotated-secret", now=NOW)

    assert saved.password_hash == "hash-value"
    assert saved.token_secret == "rotated-secret"
    assert saved.updated_at == NOW


def test_auth_state_repr_masks_password_hash_and_token_secret(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = ApiAuthRepository(connection)

        state = repository.save_password_hash("hash-value", now=NOW)

    representation = repr(state)
    assert "hash-value" not in representation
    assert state.token_secret not in representation


def test_auth_repository_rejects_naive_update_time(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = ApiAuthRepository(connection)

        with pytest.raises(ValueError, match="timezone-aware"):
            repository.save_password_hash("hash-value", now=datetime(2026, 7, 7, 2, 0))


def test_auth_service_reports_setup_required_without_password(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        service = AuthService(ApiAuthRepository(connection), token_ttl_seconds=3600)

        assert service.status() == "setup_required"


def test_auth_service_setup_hashes_password_and_login_returns_token(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        service = AuthService(ApiAuthRepository(connection), token_ttl_seconds=3600)

        service.setup_password("local-password", now=NOW)
        login = service.login("local-password", now=NOW)
        claims = service.verify_token(login.access_token, now=NOW)

    assert login.token_type == "bearer"
    assert login.expires_at == NOW + timedelta(seconds=3600)
    assert claims.user == "local"


def test_auth_service_rejects_login_when_password_is_not_configured(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        service = AuthService(ApiAuthRepository(connection), token_ttl_seconds=3600)

        with pytest.raises(AuthSetupRequiredError):
            service.login("local-password", now=NOW)


def test_auth_service_rejects_expired_token(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        service = AuthService(ApiAuthRepository(connection), token_ttl_seconds=60)
        service.setup_password("local-password", now=NOW)
        login = service.login("local-password", now=NOW)

        with pytest.raises(InvalidTokenError):
            service.verify_token(login.access_token, now=NOW + timedelta(seconds=61))


def test_auth_service_stored_password_takes_precedence_over_startup_password(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = ApiAuthRepository(connection)
        AuthService(
            repository,
            token_ttl_seconds=3600,
        ).setup_password("stored-password", now=NOW)
        service = AuthService(
            repository,
            token_ttl_seconds=3600,
            startup_password="startup-password",
        )

        with pytest.raises(InvalidCredentialsError):
            service.login("startup-password", now=NOW)
        login = service.login("stored-password", now=NOW)

    assert login.token_type == "bearer"


def test_auth_service_startup_password_blocks_anonymous_setup(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        service = AuthService(
            ApiAuthRepository(connection),
            token_ttl_seconds=3600,
            startup_password="startup-password",
        )

        with pytest.raises(AuthAlreadyConfiguredError):
            service.setup_password("attacker-password", now=NOW)


def test_auth_service_login_uses_startup_password_before_setup(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        service = AuthService(
            ApiAuthRepository(connection),
            token_ttl_seconds=3600,
            startup_password="startup-password",
        )

        login = service.login("startup-password", now=NOW)
        claims = service.verify_token(login.access_token, now=NOW)

    assert claims.user == "local"


def test_auth_service_configured_token_secret_is_idempotent(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = ApiAuthRepository(connection)

        AuthService(
            repository,
            token_ttl_seconds=3600,
            configured_token_secret="configured-secret",
        )
        initial = repository.get()
        AuthService(
            repository,
            token_ttl_seconds=3600,
            configured_token_secret="configured-secret",
        )
        loaded = repository.get()

    assert loaded.token_secret == "configured-secret"
    assert loaded.updated_at == initial.updated_at


def test_auth_service_setup_stores_password_hash_not_plaintext(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = ApiAuthRepository(connection)
        service = AuthService(repository, token_ttl_seconds=3600)

        service.setup_password("local-password", now=NOW)
        state = repository.get()

    assert state.password_hash is not None
    assert state.password_hash != "local-password"
    assert state.password_hash.startswith("pbkdf2_sha256$")


def test_auth_service_rejects_second_setup(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        service = AuthService(ApiAuthRepository(connection), token_ttl_seconds=3600)
        service.setup_password("local-password", now=NOW)

        with pytest.raises(AuthAlreadyConfiguredError):
            service.setup_password("other-password", now=NOW)


def test_auth_service_rejects_invalid_credentials(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        service = AuthService(ApiAuthRepository(connection), token_ttl_seconds=3600)
        service.setup_password("local-password", now=NOW)

        with pytest.raises(InvalidCredentialsError):
            service.login("wrong-password", now=NOW)


def test_auth_service_rejects_malformed_stored_password_hash(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = ApiAuthRepository(connection)
        repository.save_password_hash("not-a-valid-hash", now=NOW)
        service = AuthService(repository, token_ttl_seconds=3600)

        with pytest.raises(InvalidCredentialsError):
            service.login("local-password", now=NOW)


def test_auth_service_rejects_malformed_token(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        service = AuthService(ApiAuthRepository(connection), token_ttl_seconds=3600)
        service.setup_password("local-password", now=NOW)

        with pytest.raises(InvalidTokenError):
            service.verify_token("not-a-token", now=NOW)


def test_auth_service_rejects_non_ascii_malformed_token(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        service = AuthService(ApiAuthRepository(connection), token_ttl_seconds=3600)
        service.setup_password("local-password", now=NOW)
        login = service.login("local-password", now=NOW)
        payload_part = login.access_token.split(".", 1)[0]

        with pytest.raises(InvalidTokenError):
            service.verify_token(f"{payload_part}.令牌", now=NOW)
