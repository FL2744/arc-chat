"""Stable local integration contracts for external ARC Chat applications.

The HTTP surface that uses these objects is intentionally read-mostly. External
applications may submit proposals for human review, but cannot execute code,
submit/cancel jobs, or start/stop model services through this interface.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


PROPOSAL_ID_RE = re.compile(r"^proposal-[0-9a-f]{16,32}$")
PROPOSAL_KINDS = {"workspace", "job", "model_service", "pipeline", "artifact_handoff"}


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        raise ValueError("Integration proposal payload is nested too deeply.")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > 4000:
            raise ValueError("Integration proposal text is too long.")
        return value
    if isinstance(value, list):
        if len(value) > 100:
            raise ValueError("Integration proposal list is too large.")
        return [_json_safe(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > 100:
            raise ValueError("Integration proposal object is too large.")
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ValueError("Integration proposal keys must be short text strings.")
            result[key] = _json_safe(item, depth=depth + 1)
        return result
    raise ValueError("Integration proposal contains a non-JSON value.")


@dataclass(frozen=True)
class IntegrationProposal:
    id: str
    kind: str
    summary: str
    source: str
    created_at: str
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, *, kind: str, summary: str, source: str = "external", payload: dict[str, Any] | None = None) -> "IntegrationProposal":
        if kind not in PROPOSAL_KINDS:
            raise ValueError("Unsupported integration proposal kind.")
        summary = str(summary or "").strip()
        source = str(source or "external").strip()
        if not summary or len(summary) > 500:
            raise ValueError("Integration proposal summary must be 1-500 characters.")
        if not source or len(source) > 120 or any(ord(ch) < 32 for ch in source):
            raise ValueError("Invalid integration proposal source.")
        safe_payload = _json_safe(payload or {})
        if not isinstance(safe_payload, dict):
            raise ValueError("Integration proposal payload must be an object.")
        return cls(
            id="proposal-" + uuid.uuid4().hex[:24],
            kind=kind,
            summary=summary,
            source=source,
            created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            payload=safe_payload,
        )

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProposalStore:
    def __init__(self, *, limit: int = 100):
        self.limit = max(1, min(1000, int(limit)))
        self._items: dict[str, IntegrationProposal] = {}

    def add(self, proposal: IntegrationProposal) -> IntegrationProposal:
        if not PROPOSAL_ID_RE.fullmatch(proposal.id):
            raise ValueError("Invalid proposal id.")
        self._items[proposal.id] = proposal
        while len(self._items) > self.limit:
            self._items.pop(next(iter(self._items)))
        return proposal

    def create(self, **kwargs: Any) -> IntegrationProposal:
        return self.add(IntegrationProposal.create(**kwargs))

    def list(self) -> list[IntegrationProposal]:
        return list(self._items.values())

    def dismiss(self, proposal_id: str) -> None:
        if not PROPOSAL_ID_RE.fullmatch(str(proposal_id)):
            raise ValueError("Invalid proposal id.")
        self._items.pop(str(proposal_id), None)
