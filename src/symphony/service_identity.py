"""Shared managed-service identity protocol values."""

from __future__ import annotations

import re

SERVICE_INSTANCE_ENV = "SYMPHONY_SERVICE_INSTANCE_ID"
# Per-launch capability accepted only by the exact health route. Managed
# service start generates it with 256 bits of randomness.
SERVICE_INSTANCE_HEADER = "X-Symphony-Service-Instance"
MAX_SERVICE_INSTANCE_ID_LENGTH = 128
MIN_SERVICE_CAPABILITY_LENGTH = 32
_SERVICE_CAPABILITY_RE = re.compile(
    rf"^[A-Za-z0-9_-]{{{MIN_SERVICE_CAPABILITY_LENGTH},{MAX_SERVICE_INSTANCE_ID_LENGTH}}}$"
)


def normalize_service_instance_id(value: object) -> str | None:
    """Return a bounded non-blank instance ID or ``None``."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_SERVICE_INSTANCE_ID_LENGTH
        or value.strip() != value
    ):
        return None
    return value


def normalize_service_probe_credential(value: object) -> str | None:
    """Return a health capability with the accepted URL-safe shape."""
    normalized = normalize_service_instance_id(value)
    if normalized is None or _SERVICE_CAPABILITY_RE.fullmatch(normalized) is None:
        return None
    return normalized
