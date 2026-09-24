"""Canonical opaque identifiers used by hosted control-plane records.

Legacy local-helper IDs remain readable during recovery migration. New hosted
records use a type prefix and random UUID entropy, and never derive their ID
from a provider URL or provider-owned identifier.
"""

from __future__ import annotations

import re
import uuid


ID_PREFIXES = {
    "user": "usr",
    "project": "prj",
    "application": "app",
    "workspace": "ws",
    "provider_resource": "prsrc",
    "endpoint": "ep",
    "job": "job",
    "deployment": "dep",
    "artifact": "art",
    "audit_event": "audit",
    "platform_event": "event",
}

_CANONICAL_ID = re.compile(r"^(?P<prefix>[a-z]{2,8})_(?P<uuid>[0-9a-f]{32})$")


def new_id(kind: str) -> str:
    """Create a type-scoped opaque ID for a new domain record."""
    try:
        prefix = ID_PREFIXES[kind]
    except KeyError as exc:
        raise ValueError(f"Unknown identifier kind: {kind}") from exc
    return f"{prefix}_{uuid.uuid4().hex}"


def is_canonical_id(kind: str, value: object) -> bool:
    prefix = ID_PREFIXES.get(kind)
    match = _CANONICAL_ID.fullmatch(str(value or ""))
    return bool(prefix and match and match.group("prefix") == prefix)


def require_canonical_id(kind: str, value: object) -> str:
    rendered = str(value or "")
    if not is_canonical_id(kind, rendered):
        raise ValueError(f"Invalid canonical {kind} id.")
    return rendered
