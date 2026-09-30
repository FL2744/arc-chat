"""Environment-variable lookup with legacy ARC Chat compatibility.

ARC Research reads ``ARC_RESEARCH_<NAME>`` first and falls back to the legacy
``ARC_CHAT_<NAME>`` variable so existing local configuration keeps working.
"""

from __future__ import annotations

import os

NEW_PREFIX = "ARC_RESEARCH_"
LEGACY_PREFIX = "ARC_CHAT_"


def getenv(name: str, default: str | None = None) -> str | None:
    """Look up ``name`` (without prefix) under the new, then legacy, prefix."""
    for prefix in (NEW_PREFIX, LEGACY_PREFIX):
        value = os.environ.get(prefix + name)
        if value is not None:
            return value
    return default
