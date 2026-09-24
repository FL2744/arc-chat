"""Safe resource resolution for multi-job and previous-workspace recovery."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from projects import ProjectRecord


ACTIVE_STATES = {"RUNNING", "COMPLETING"}
WAIT_STATES = {"PENDING", "CONFIGURING"}
TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "PREEMPTED", "NOT_IN_QUEUE"}


@dataclass(frozen=True)
class ResourceCandidate:
    resource_id: str
    state: str
    name: str = ""
    provider: str = "arc"
    kind: str = "job"
    project_id: str = ""
    node: str = ""
    reason: str = ""

    @classmethod
    def from_job(cls, value: Mapping[str, Any]) -> "ResourceCandidate":
        return cls(
            resource_id=str(value.get("job_id") or value.get("resource_id") or "").strip(),
            state=str(value.get("state") or "UNKNOWN").upper().strip(),
            name=str(value.get("name") or "").strip(),
            provider=str(value.get("provider") or "arc").strip() or "arc",
            kind=str(value.get("kind") or "job").strip() or "job",
            project_id=str(value.get("project_id") or "").strip(),
            node=str(value.get("node") or "").strip(),
            reason=str(value.get("reason") or "").strip(),
        )

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResolutionDecision:
    action: str
    resource_id: str
    confidence: str
    reason: str
    candidates: tuple[ResourceCandidate, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "resource_id": self.resource_id,
            "confidence": self.confidence,
            "reason": self.reason,
            "candidates": [item.public_dict() for item in self.candidates],
        }


class ResourceResolver:
    """Resolve a project to an existing resource without guessing across ties."""

    @staticmethod
    def _score(project: ProjectRecord, candidate: ResourceCandidate) -> int:
        if not candidate.resource_id or candidate.state in TERMINAL_STATES:
            return -1
        score = 0
        if candidate.resource_id == project.active_job_id:
            score += 120
        if candidate.resource_id in project.job_ids:
            score += 70
        if candidate.project_id and candidate.project_id == project.manifest.id:
            score += 70
        project_tokens = {project.manifest.id.lower(), project.manifest.name.lower().replace(" ", "-")}
        name = candidate.name.lower()
        if name and any(token and token in name for token in project_tokens):
            score += 20
        if candidate.state in ACTIVE_STATES:
            score += 30
        elif candidate.state in WAIT_STATES:
            score += 15
        return score

    def resolve(self, project: ProjectRecord, candidates: Iterable[ResourceCandidate | Mapping[str, Any]]) -> ResolutionDecision:
        normalized = tuple(
            item if isinstance(item, ResourceCandidate) else ResourceCandidate.from_job(item)
            for item in candidates
        )
        scored = sorted(
            ((self._score(project, item), item) for item in normalized),
            key=lambda pair: pair[0],
            reverse=True,
        )
        viable = [(score, item) for score, item in scored if score >= 0]
        if not viable:
            return ResolutionDecision("start", "", "high", "No reusable active resource matches this project.", normalized)

        top_score, top = viable[0]
        if top_score < 60:
            return ResolutionDecision("choose", "", "low", "Active ARC resources exist, but none is strongly linked to this project.", tuple(item for _, item in viable))

        tied = [item for score, item in viable if score >= top_score - 10]
        if len(tied) > 1:
            return ResolutionDecision("choose", "", "medium", "Several resources are similarly associated with this project; human selection is safer.", tuple(tied))

        action = "reuse" if top.state in ACTIVE_STATES else "wait" if top.state in WAIT_STATES else "choose"
        reason = (
            "Reusing the project's previously active ARC resource."
            if top.resource_id == project.active_job_id
            else "Reusing the uniquely linked ARC resource for this project."
        )
        return ResolutionDecision(action, top.resource_id, "high", reason, (top,))
