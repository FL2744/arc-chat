"""Secret redaction and safe diagnostic helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any


REDACTED = "[redacted]"
_SECRET_FIELD = re.compile(
    r"(?:password|passphrase|secret|token|cookie|credential|api[_-]?key|"
    r"ssh[_-]?key|private[_-]?key|authorization|auth[_-]?header)",
    re.IGNORECASE,
)


def validate_public_metadata(value: Any, *, label: str = "metadata", max_keys: int = 64) -> dict[str, Any]:
    """Validate the small, non-secret scalar metadata persisted by registries."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object.")
    if len(value) > max_keys:
        raise ValueError(f"{label} has too many fields.")
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > 80:
            raise ValueError(f"{label} keys must be short strings.")
        if _SECRET_FIELD.search(key):
            raise ValueError(f"{label} cannot contain credential-like fields.")
        if not isinstance(item, (str, int, float, bool, type(None))):
            raise ValueError(f"{label} values must be scalar JSON values.")
        if isinstance(item, str) and len(item) > 1000:
            raise ValueError(f"{label} text values are too long.")
        result[key] = item
    return result


def redact_text(value: object, secrets: Iterable[str]) -> str:
    text = str(value)
    # Longest-first prevents a shorter token from leaving a suffix of a larger
    # secret visible. Ignore tiny values to avoid destroying normal prose.
    clean = sorted({s for s in secrets if isinstance(s, str) and len(s) >= 4}, key=len, reverse=True)
    for secret in clean:
        text = text.replace(secret, REDACTED)
    return text


def redact_structure(value: Any, secrets: Iterable[str]) -> Any:
    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, Mapping):
        return {str(k): redact_structure(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_structure(v, secrets) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_structure(v, secrets) for v in value)
    return value

