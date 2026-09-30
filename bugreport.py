"""'Report a Problem' payloads: user-reviewable, secret-redacted diagnostic fields."""

from __future__ import annotations

import platform
from typing import Any

from security import redact_text
from version import BUILD, VERSION


def build_report(*, stage: str = "", job_id: str = "", model: str = "", resource: str = "",
                 log_excerpt: str = "", traceback: str = "", config: dict[str, Any] | None = None,
                 secrets: list[str] | tuple[str, ...] = ()) -> dict[str, str]:
    """Return labelled fields the UI shows for review; the user can drop any of them.

    Config keys that look like credentials are omitted outright, and known secret
    values are redacted from free text.
    """
    from security import _SECRET_FIELD  # reuse the credential-name pattern

    safe_config = {k: v for k, v in (config or {}).items() if not _SECRET_FIELD.search(str(k))}
    fields = {
        "version": f"ARC Research {VERSION} (build {BUILD})",
        "operating_system": platform.platform(),
        "workflow_stage": stage,
        "job_id": job_id,
        "model": model,
        "resource": resource,
        "log_excerpt": redact_text(log_excerpt, secrets),
        "traceback": redact_text(traceback, secrets),
        "configuration": redact_text(repr(safe_config), secrets) if safe_config else "",
    }
    return {k: v for k, v in fields.items() if v}


def finalize(fields: dict[str, str], removed: set[str] = frozenset()) -> str:
    """Render the report after the user removed fields."""
    return "\n".join(f"## {key}\n{value}" for key, value in fields.items() if key not in removed)
