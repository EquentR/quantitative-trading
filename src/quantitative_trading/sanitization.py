from __future__ import annotations

import re


_SENSITIVE_ERROR_KEY = (
    r"(?:[A-Za-z0-9]+[_-])*(?:token|password|secret|cookie|api[_-]?key|apikey)"
    r"(?:[_-][A-Za-z0-9]+)*"
)


def redact_sensitive_text(text: str) -> str:
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
    summary = re.sub(r"(?<!\w)/(?:[^/\s]+/)*[^/\s]+", "[path]", summary)
    return summary[:300]


def safe_error_summary(exc: Exception) -> str:
    summary = str(exc).strip() or exc.__class__.__name__
    return redact_sensitive_text(summary) or exc.__class__.__name__[:300]
