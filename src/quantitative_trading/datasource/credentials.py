from __future__ import annotations


def redact_secret(secret: str | None) -> str:
    if secret is None or secret.strip() == "":
        return "missing"
    return "configured"
