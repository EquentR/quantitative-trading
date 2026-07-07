from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from quantitative_trading.api.errors import ApiError, install_error_handlers


class Payload(BaseModel):
    symbol: str = Field(pattern=r"^\d{6}$")


def test_api_error_returns_uniform_shape() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom():
        raise ApiError(
            status_code=404,
            code="position_not_found",
            message="position not found",
            details={"symbol": "600000"},
        )

    response = TestClient(app).get("/boom")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "position_not_found",
            "message": "position not found",
            "details": {"symbol": "600000"},
        }
    }


def test_validation_error_returns_uniform_shape() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/payload")
    def payload(payload: Payload):
        return payload

    response = TestClient(app).post("/payload", json={"symbol": "bad"})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "request validation failed"
    assert body["error"]["details"]["errors"][0]["loc"] == ["body", "symbol"]


def test_api_error_masks_sensitive_detail_values() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/secret")
    def secret():
        raise ApiError(
            status_code=400,
            code="bad_request",
            message="bad request",
            details={
                "symbol": "600000",
                "token": "token-value",
                "access_token": "access-token-value",
                "password_hash": "hash-value",
                "nested": {"password": "plain-password"},
            },
        )

    response = TestClient(app).get("/secret")

    assert response.status_code == 400
    assert response.json()["error"]["details"] == {
        "symbol": "600000",
        "token": "[redacted]",
        "access_token": "[redacted]",
        "password_hash": "[redacted]",
        "nested": {"password": "[redacted]"},
    }


def test_validation_error_does_not_echo_sensitive_input() -> None:
    app = FastAPI()
    install_error_handlers(app)

    class LoginPayload(BaseModel):
        password: str = Field(min_length=20)

    @app.post("/login")
    def login(payload: LoginPayload):
        return payload

    response = TestClient(app).post("/login", json={"password": "plain-password"})

    assert response.status_code == 422
    assert "plain-password" not in response.text
