from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SmtpSecurity(StrEnum):
    NONE = "none"
    STARTTLS = "starttls"
    SSL = "ssl"


class EmailDeliveryStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    RETRY = "retry"
    SENT = "sent"
    DEAD = "dead"


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("datetime must be timezone-aware")
    return value


class SmtpSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    username: str = Field(default="", max_length=255)
    password: str | None = Field(default=None, max_length=1024, repr=False)
    sender: str = Field(min_length=3, max_length=320)
    recipient: str = Field(min_length=3, max_length=320)
    security: SmtpSecurity = SmtpSecurity.STARTTLS
    enabled: bool = False


class SmtpSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    host: str
    port: int
    username: str
    password: str | None = Field(default=None, repr=False)
    sender: str
    recipient: str
    security: SmtpSecurity
    enabled: bool
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def updated_at_must_be_aware(cls, value: datetime) -> datetime:
        return _aware(value)  # type: ignore[return-value]


class SmtpSettingsPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: bool
    host: str = ""
    port: int = 587
    username: str = ""
    sender: str = ""
    recipient: str = ""
    security: SmtpSecurity = SmtpSecurity.STARTTLS
    enabled: bool = False
    password_configured: bool = False
    updated_at: datetime | None = None

    @field_validator("updated_at")
    @classmethod
    def updated_at_must_be_aware(cls, value: datetime | None) -> datetime | None:
        return _aware(value)


class EmailDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    delivery_id: str = Field(min_length=1)
    notification_id: str | None = None
    dedup_key: str = Field(min_length=1, max_length=500)
    recipient: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1)
    payload: dict[str, object] = Field(default_factory=dict)
    status: EmailDeliveryStatus = EmailDeliveryStatus.PENDING
    attempt_count: int = Field(default=0, ge=0)
    next_attempt_at: datetime | None = None
    lease_expires_at: datetime | None = None
    last_error: str = ""
    sent_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator(
        "next_attempt_at",
        "lease_expires_at",
        "sent_at",
        "created_at",
        "updated_at",
    )
    @classmethod
    def datetimes_must_be_aware(cls, value: datetime | None) -> datetime | None:
        return _aware(value)
