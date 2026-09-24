"""Evidence-based resolution of provider resources to project workspaces.

Resolution never relies on DOM ordering or on a resource being the only active
job. Automatic reuse requires either an exact persisted relationship or a
unique, strong match across independent metadata fields.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from projects import ProjectRecord
from workspaces import WorkspaceRecord


ACTIVE_STATES = {"RUNNING", "COMPLETING", "READY", "BUSY", "IDLE"}
WAIT_STATES = {"PENDING", "CONFIGURING", "QUEUED", "SUBMITTED"}
TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED", "CANCELED", "TIMEOUT", "PREEMPTED", "NOT_IN_QUEUE", "STOPPED", "DELETED"}
STRONG_EVIDENCE = {"owner", "allocation", "application_type", "job_name", "start_time", "cluster", "provider"}


@dataclass(frozen=True)
class ResourceExpectation:
    """Known user intent used as evidence; unset fields do not become guesses."""

    workspace_id: str = ""
    owner_id: str = ""
    allocation: str = ""
    application_type: str = ""
    job_name: str = ""
    expected_state: str = ""
    cluster: str = ""
    provider: str = ""
    created_after: str = ""


@dataclass(frozen=True)
class ResourceCandidate:
    # resource_id is the provider's external identifier for the local ARC
    # helper. Hosted APIs should expose provider_resource_id (opaque) instead.
    resource_id: str
    state: str
    name: str = ""
    provider: str = "arc"
    kind: str = "job"
    project_id: str = ""
    node: str = ""
    reason: str = ""
    workspace_id: str = ""
    owner_id: str = ""
    allocation: str = ""
    application_type: str = ""
    created_at: str = ""
    cluster: str = ""
    provider_resource_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_job(cls, value: Mapping[str, Any]) -> "ResourceCandidate":
        return cls(
            resource_id=str(value.get("resource_id") or value.get("job_id") or "").strip(),
            state=str(value.get("state") or "UNKNOWN").upper().strip(),
            name=str(value.get("name") or value.get("job_name") or "").strip(),
            provider=str(value.get("provider") or "arc").strip() or "arc",
            kind=str(value.get("kind") or value.get("application_type") or "job").strip() or "job",
            project_id=str(value.get("project_id") or "").strip(),
            node=str(value.get("node") or "").strip(),
            reason=str(value.get("reason") or "").strip(),
            workspace_id=str(value.get("workspace_id") or "").strip(),
            owner_id=str(value.get("owner_id") or value.get("user_id") or "").strip(),
            allocation=str(value.get("allocation") or value.get("account") or "").strip(),
            application_type=str(value.get("application_type") or value.get("kind") or "").strip(),
            created_at=str(value.get("created_at") or value.get("started_at") or "").strip(),
            cluster=str(value.get("cluster") or "").strip(),
            provider_resource_id=str(value.get("provider_resource_id") or "").strip(),
            metadata=value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {},
        )

    def public_dict(self, *, include_provider_identifier: bool = True) -> dict[str, Any]:
        if include_provider_identifier:
            return asdict(self)
        # The hosted gateway returns a deliberately small projection. Provider
        # metadata, scheduler IDs, owner IDs, and node/reason strings are not
        # client-facing fields.
        return {
            "resource_id": self.provider_resource_id,
            "provider_resource_id": self.provider_resource_id,
            "state": self.state,
            "name": self.name,
            "provider": self.provider,
            "kind": self.kind,
            "project_id": self.project_id,
            "workspace_id": self.workspace_id,
            "created_at": self.created_at,
            "cluster": self.cluster,
        }


@dataclass(frozen=True)
class ResolutionDecision:
    action: str
    resource_id: str
    confidence: str
    reason: str
    candidates: tuple[ResourceCandidate, ...] = ()
    evidence: tuple[str, ...] = ()

    def public_dict(self, *, include_provider_identifiers: bool = True) -> dict[str, Any]:
        return {
            "action": self.action,
            "resource_id": self.resource_id,
            "confidence": self.confidence,
            "reason": self.reason,
            "candidates": [item.public_dict(include_provider_identifier=include_provider_identifiers) for item in self.candidates],
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class _Score:
    value: int
    evidence: tuple[str, ...]
    explicit: bool = False


class ResourceResolver:
    """Resolve a project to an existing resource without guessing across ties."""

    @staticmethod
    def _same(expected: str, actual: str) -> bool:
        return bool(expected and actual and expected.casefold() == actual.casefold())

    @staticmethod
    def _after(candidate_time: str, cutoff: str) -> bool | None:
        if not candidate_time or not cutoff:
            return None
        try:
            left = datetime.fromisoformat(candidate_time.replace("Z", "+00:00"))
            right = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
            if left.tzinfo is None:
                left = left.replace(tzinfo=timezone.utc)
            if right.tzinfo is None:
                right = right.replace(tzinfo=timezone.utc)
            return left >= right
        except (TypeError, ValueError):
            return None

    @classmethod
    def _score(
        cls,
        project: ProjectRecord,
        candidate: ResourceCandidate,
        workspaces: tuple[WorkspaceRecord, ...],
        expected: ResourceExpectation,
    ) -> _Score:
        if not candidate.resource_id or candidate.state in TERMINAL_STATES:
            return _Score(-1, ())

        # A known identity/provider/account mismatch is negative evidence. Do
        # not let project tags or job names override it.
        if expected.owner_id and candidate.owner_id and not cls._same(expected.owner_id, candidate.owner_id):
            return _Score(-1, ())
        if expected.provider and not cls._same(expected.provider, candidate.provider):
            return _Score(-1, ())
        if expected.cluster and candidate.cluster and not cls._same(expected.cluster, candidate.cluster):
            return _Score(-1, ())
        if expected.allocation and candidate.allocation and not cls._same(expected.allocation, candidate.allocation):
            return _Score(-1, ())

        score = 0
        evidence: list[str] = []
        explicit = False

        linked_ids = set(project.job_ids + project.endpoint_ids + project.artifact_ids + project.deployment_ids)
        if project.active_job_id:
            linked_ids.add(project.active_job_id)
        if candidate.resource_id in linked_ids or (
            candidate.provider_resource_id and candidate.provider_resource_id in linked_ids
        ):
            score += 150
            evidence.append("exact_linked_resource_id")
            explicit = True

        project_workspace_ids = set(project.workspace_ids)
        if expected.workspace_id and candidate.workspace_id == expected.workspace_id:
            score += 150
            evidence.append("exact_workspace_id")
            explicit = True
        elif candidate.workspace_id and candidate.workspace_id in project_workspace_ids:
            score += 130
            evidence.append("project_workspace_id")
            explicit = True

        for workspace in workspaces:
            associated_ids = set(workspace.job_ids + workspace.provider_resource_ids + workspace.endpoint_ids)
            candidate_ids = {candidate.resource_id, candidate.provider_resource_id}
            candidate_ids.discard("")
            if candidate.workspace_id == workspace.id or candidate_ids.intersection(associated_ids):
                score += 130
                evidence.append("persisted_workspace_association")
                explicit = True
                break

        if candidate.project_id and candidate.project_id == project.manifest.id:
            score += 120
            evidence.append("explicit_project_tag")
            explicit = True

        if expected.owner_id and cls._same(expected.owner_id, candidate.owner_id):
            score += 15
            evidence.append("owner")
        if expected.allocation and cls._same(expected.allocation, candidate.allocation):
            score += 15
            evidence.append("allocation")
        if expected.application_type and cls._same(expected.application_type, candidate.application_type or candidate.kind):
            score += 14
            evidence.append("application_type")
        if expected.job_name and cls._same(expected.job_name, candidate.name):
            score += 14
            evidence.append("job_name")
        elif candidate.name:
            # A project-looking name is a hint only, never a strong link.
            project_tokens = {project.manifest.id.casefold(), project.manifest.name.casefold().replace(" ", "-")}
            if any(token and token in candidate.name.casefold() for token in project_tokens):
                score += 3
                evidence.append("name_hint")
        if expected.cluster and cls._same(expected.cluster, candidate.cluster):
            score += 10
            evidence.append("cluster")
        if expected.provider and cls._same(expected.provider, candidate.provider):
            score += 8
            evidence.append("provider")
        if expected.created_after:
            is_after = cls._after(candidate.created_at, expected.created_after)
            if is_after is True:
                score += 10
                evidence.append("start_time")
            elif is_after is False:
                score -= 10
        if expected.expected_state:
            if cls._same(expected.expected_state, candidate.state):
                score += 5
                evidence.append("expected_state")
            else:
                score -= 5
        if candidate.state in ACTIVE_STATES:
            score += 8
        elif candidate.state in WAIT_STATES:
            score += 5

        return _Score(score, tuple(evidence), explicit)

    def resolve(
        self,
        project: ProjectRecord,
        candidates: Iterable[ResourceCandidate | Mapping[str, Any]],
        *,
        workspaces: Iterable[WorkspaceRecord] = (),
        expected: ResourceExpectation | None = None,
    ) -> ResolutionDecision:
        normalized = tuple(
            item if isinstance(item, ResourceCandidate) else ResourceCandidate.from_job(item)
            for item in candidates
        )
        workspace_records = tuple(workspaces)
        intent = expected or ResourceExpectation()
        scored = [
            (self._score(project, item, workspace_records, intent), item)
            for item in normalized
        ]
        viable = sorted(((match, item) for match, item in scored if match.value >= 0), key=lambda pair: pair[0].value, reverse=True)
        if not viable:
            return ResolutionDecision("start", "", "high", "No reusable active resource matches this project.", normalized)

        top_match, top = viable[0]
        strong_count = len(set(top_match.evidence) & STRONG_EVIDENCE)
        top_is_auto = top_match.explicit or strong_count >= 4
        if not top_is_auto:
            return ResolutionDecision(
                "choose", "", "none",
                "Resources exist, but the available metadata does not establish a safe project match.",
                tuple(item for _, item in viable),
                top_match.evidence,
            )

        # Any second plausible candidate within a small margin needs a choice,
        # even when both carry project tags or exact workspace evidence.
        plausible = [item for match, item in viable if match.value >= top_match.value - 8 and (match.explicit or len(set(match.evidence) & STRONG_EVIDENCE) >= 4)]
        if len(plausible) > 1:
            return ResolutionDecision(
                "choose", "", "low",
                "Several resources match this project; choose the intended workspace.",
                tuple(plausible),
                top_match.evidence,
            )

        action = "reuse" if top.state in ACTIVE_STATES else "wait" if top.state in WAIT_STATES else "choose"
        confidence = "high" if top_match.explicit or strong_count >= 5 else "medium"
        if action == "choose":
            return ResolutionDecision("choose", "", "low", "The linked resource is not in a reusable state.", (top,), top_match.evidence)
        reason = (
            "Reusing an exact persisted project/workspace resource association."
            if top_match.explicit
            else "Reusing the unique resource identified by strong user, allocation, application, and provider metadata."
        )
        return ResolutionDecision(action, top.resource_id, confidence, reason, (top,), top_match.evidence)
