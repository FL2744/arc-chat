"""Automatic, validated Slurm job names for ARC Research."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable

PREFIX = "arcr"
MAX_LENGTH = 80  # matches the JobSpec name limit
_VALID = re.compile(r"^[A-Za-z0-9_.-]{1,%d}$" % MAX_LENGTH)
_INVALID_RUN = re.compile(r"[^a-z0-9]+")


def sanitize(value: str) -> str:
    """Lowercase ``value`` and collapse anything outside [a-z0-9] into single hyphens."""
    return _INVALID_RUN.sub("-", str(value or "").lower()).strip("-")


def validate_job_name(name: str) -> str:
    if not _VALID.fullmatch(name or ""):
        raise ValueError(
            f"Job name '{name}' is not valid. Use 1-{MAX_LENGTH} letters, digits, '.', '_' or '-'."
        )
    return name


def generate_job_name(
    *,
    model: str = "",
    activity: str = "",
    project: str = "",
    existing: Iterable[str] = (),
    now: dt.datetime | None = None,
) -> str:
    """Derive a name like ``llama-70b-inference-0930`` and de-duplicate it.

    The model's org prefix (``meta-llama/``) is dropped. Duplicates of names in
    ``existing`` get ``-02``, ``-03`` ... suffixes.
    """
    now = now or dt.datetime.now()
    model_part = sanitize(str(model).rsplit("/", 1)[-1])
    parts = [p for p in (model_part or sanitize(project), sanitize(activity) or "run", now.strftime("%m%d")) if p]
    base = "-".join(parts)[:MAX_LENGTH - 3].strip("-") or PREFIX
    taken = set(existing)
    candidate, counter = base, 1
    while candidate in taken:
        counter += 1
        candidate = f"{base}-{counter:02d}"
    return validate_job_name(candidate)


class JobNamer:
    """Tracks an auto-generated name that follows context until the user locks it."""

    def __init__(self) -> None:
        self.locked = False
        self.name = ""

    def set_manual(self, name: str) -> str:
        self.name = validate_job_name(name)
        self.locked = True
        return self.name

    def unlock(self) -> None:
        self.locked = False

    def update(self, **context) -> str:
        if not self.locked:
            self.name = generate_job_name(**context)
        return self.name
