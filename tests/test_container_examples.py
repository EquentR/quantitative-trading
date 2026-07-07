from pathlib import Path


def test_dockerfile_installs_project_and_uses_qt_entrypoint() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.11-slim" in dockerfile
    assert "python -m pip install --no-cache-dir ." in dockerfile
    assert 'ENTRYPOINT ["qt"]' in dockerfile
    assert 'CMD ["service", "check"]' in dockerfile


def test_compose_mounts_data_and_sets_database_path() -> None:
    compose = Path("compose.yaml").read_text(encoding="utf-8")

    assert "QT_DATABASE_PATH: /app/data/quant_trading.db" in compose
    assert "./data:/app/data" in compose
    assert 'command: ["service", "check"]' in compose


def test_dockerignore_excludes_local_state_and_secrets() -> None:
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")

    assert ".env" in dockerignore
    assert "data/" in dockerignore
    assert "*.db" in dockerignore
    assert ".venv/" in dockerignore


def test_bash_backend_script_bootstraps_environment_and_runs_service_check() -> None:
    script = Path("scripts/start-backend.sh").read_text(encoding="utf-8")

    assert "#!/usr/bin/env bash" in script
    assert 'python" -m pip install -e ".[dev]"' in script
    assert "QT_DATABASE_PATH" in script
    assert 'exec "$VENV_DIR/bin/qt" service check' in script


def test_env_example_contains_api_placeholders_only() -> None:
    text = Path(".env.example").read_text(encoding="utf-8")

    assert "QT_API_HOST=127.0.0.1" in text
    assert "QT_API_ACCESS_PASSWORD=" in text
    assert "QT_API_TOKEN_SECRET=" in text
    assert "QT_SERVICE_RUN_ON_START_WHEN_SCHEDULER_ENABLED=true" in text
    assert "local-password" not in text
