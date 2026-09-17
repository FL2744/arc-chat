"""Secret redaction and safe diagnostic helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


REDACTED = "[redacted]"


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

