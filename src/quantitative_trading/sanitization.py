from __future__ import annotations

import re
from typing import Any


_SENSITIVE_ERROR_KEY = (
    r"(?:[A-Za-z0-9]+[_-])*(?:token|password|secret|cookie|api[_-]?key|apikey)"
    r"(?:[_-][A-Za-z0-9]+)*"
)
_SENSITIVE_RECORD_KEY_RE = re.compile(rf"(?i)\b{_SENSITIVE_ERROR_KEY}\b")
_SENSITIVE_RECORD_WORD_RE = re.compile(
    r"(?i)\b(?:token|password|secret|cookie|api[_-]?key|apikey)\b(?:\s*[:=]\s*[^\s,;&]+)?"
)


def redact_sensitive_text(text: str) -> str:
    summary = redact_sensitive_text_preserving_length(text)
    return summary[:300]


def redact_sensitive_text_preserving_length(text: str) -> str:
    summary = text.strip()
    summary = re.sub(
        r"(?i)\bAuthorization\s*:\s*Bearer\s+[^\s,;]+",
        "Authorization: Bearer [redacted]",
        summary,
    )
    summary = re.sub(
        r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^@\s/]+)@",
        r"\1[redacted]@",
        summary,
    )
    summary = re.sub(
        rf"(?i)([?&](?:{_SENSITIVE_ERROR_KEY})=)[^&#\s]+",
        r"\1[redacted]",
        summary,
    )
    summary = re.sub(
        rf"(?i)\b({_SENSITIVE_ERROR_KEY})\b\s*=\s*[^\s,;&]+",
        r"\1=[redacted]",
        summary,
    )
    summary = re.sub(
        rf"(?i)\b({_SENSITIVE_ERROR_KEY})\b\s*:\s*[^\s,;&]+",
        r"\1: [redacted]",
        summary,
    )
    summary = re.sub(
        rf"(?i)\b({_SENSITIVE_ERROR_KEY})\b\s+(?!\[redacted\])[^\s,;&]+",
        r"\1 [redacted]",
        summary,
    )
    summary = re.sub(r"(?i)\b[A-Z]:\\(?:[^\\\s]+\\)*[^\\\s]+", "[path]", summary)
    summary = re.sub(r"\\\\[^\\\s]+\\[^\\\s]+(?:\\[^\\\s]+)*", "[path]", summary)
    summary = re.sub(r"(?<!\w)/(?:[^/\s]+/)*[^/\s]+", "[path]", summary)
    return summary


def safe_error_summary(exc: Exception) -> str:
    summary = str(exc).strip() or exc.__class__.__name__
    return redact_sensitive_text(summary) or exc.__class__.__name__[:300]


def sanitize_sensitive_data(
    value: Any,
    *,
    configured_secret_texts: tuple[str, ...] = (),
) -> Any:
    if isinstance(value, dict):
        return {
            str(key): sanitize_sensitive_data(
                item,
                configured_secret_texts=configured_secret_texts,
            )
            for key, item in value.items()
            if not _SENSITIVE_RECORD_KEY_RE.search(str(key))
        }
    if isinstance(value, list):
        return [
            sanitize_sensitive_data(item, configured_secret_texts=configured_secret_texts)
            for item in value
        ]
    if isinstance(value, str):
        return _sanitize_record_text(value, configured_secret_texts=configured_secret_texts)
    return value


def _sanitize_record_text(
    value: str,
    *,
    configured_secret_texts: tuple[str, ...],
) -> str:
    sanitized = redact_sensitive_text_preserving_length(value)
    for secret in configured_secret_texts:
        sanitized = sanitized.replace(secret, "[redacted]")
    return _SENSITIVE_RECORD_WORD_RE.sub("[redacted]", sanitized)
