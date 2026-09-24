"""Provider-neutral control-plane facade shared by local ARC Chat and future hosted gateways."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from apps import ApplicationManifest, ApplicationRegistry, DeploymentPlan, load_applications
from config import CourseProfile
from projects import ProjectManifest, ProjectRecord, ProjectRegistry
from providers import PlacementDecision, PlacementEngine, PlacementRequest, ProviderRegistry, default_provider_registry
from resolver import ResolutionDecision, ResourceCandidate, ResourceResolver
from workspaces import WorkspaceRecord, WorkspaceRegistry


class ControlPlane:
    """Own project policy, placement, and resource resolution without doing I/O.

    Authentication, ARC/OOD calls, browser launch, Slurm submission, and hosted
    deployment remain adapters outside this class. This makes the same policy
    layer reusable by the current loopback helper and a future VT-hosted gateway.
    """

    def __init__(
        self,
        *,
        projects: ProjectRegistry,
        providers: ProviderRegistry,
        applications: ApplicationRegistry | None = None,
        workspaces: WorkspaceRegistry | None = None,
        placement: PlacementEngine | None = None,
        resolver: ResourceResolver | None = None,
    ):
        self.projects = projects
        self.providers = providers
        self.applications = applications or ApplicationRegistry()
        self.workspaces = workspaces or WorkspaceRegistry()
        self.placement = placement or PlacementEngine(providers)
        self.resolver = resolver or ResourceResolver()

    @classmethod
    def for_profile(
        cls,
        profile: CourseProfile,
        *,
        projects: ProjectRegistry | None = None,
        workspaces: WorkspaceRegistry | None = None,
    ) -> "ControlPlane":
        registry = projects or ProjectRegistry()
        project_id = profile.project_id or profile.id
        project = registry.ensure(ProjectManifest(
            id=project_id,
            name=profile.name,
            kind="personal" if profile.advanced_mode else "course",
            audience="private" if profile.advanced_mode else "course",
            course_profile=profile.id,
            allowed_providers=profile.allowed_providers,
        ))
        registry.set_current(project.manifest.id)
        providers = default_provider_registry(jupyterlite_url=profile.jupyterlite_url)
        applications = load_applications()
        return cls(
            projects=registry,
            providers=providers,
            applications=applications,
            workspaces=workspaces or WorkspaceRegistry(),
        )

    def current_project(self) -> ProjectRecord | None:
        return self.projects.current()

    def plan(self, request: PlacementRequest) -> PlacementDecision:
        project = self.current_project()
        allowed = project.manifest.allowed_providers if project else ()
        return self.placement.decide(request, allowed_providers=allowed)

    def ensure_workspace(
        self,
        *,
        workspace_id: str,
        provider_id: str,
        kind: str = "interactive",
        state: str = "new",
        display_name: str = "",
        metadata: dict[str, Any] | None = None,
        make_current: bool = True,
    ) -> WorkspaceRecord:
        project = self.current_project()
        if not project:
            raise ValueError("Select a project before creating a workspace record.")
        if provider_id not in project.manifest.allowed_providers:
            raise ValueError("Workspace provider is not allowed for the current project.")
        self.providers.get(provider_id)
        record = self.workspaces.ensure(
            workspace_id=workspace_id,
            project_id=project.manifest.id,
            provider_id=provider_id,
            kind=kind,
            state=state,
            display_name=display_name,
            metadata=metadata,
        )
        project.link("workspace", record.id, active=make_current)
        if make_current:
            self.workspaces.set_current(record.id)
        return record

    def plan_application(self, application: str | ApplicationManifest) -> DeploymentPlan:
        manifest = self.applications.get(application) if isinstance(application, str) else application
        project = self.projects.get(manifest.project_id)
        decision = self.placement.decide(
            manifest.placement_request(),
            allowed_providers=project.manifest.allowed_providers,
        )
        return DeploymentPlan.from_decision(manifest, decision)

    def resolve(self, candidates: Iterable[ResourceCandidate | Mapping[str, Any]]) -> ResolutionDecision:
        project = self.current_project()
        if not project:
            return ResolutionDecision(
                action="choose",
                resource_id="",
                confidence="low",
                reason="No project is selected, so resources cannot be safely associated.",
                candidates=tuple(
                    item if isinstance(item, ResourceCandidate) else ResourceCandidate.from_job(item)
                    for item in candidates
                ),
            )
        return self.resolver.resolve(project, candidates)

    def snapshot(self) -> dict[str, Any]:
        project = self.current_project()
        return {
            "project": project.public_dict() if project else None,
            "workspace": self.workspaces.current().public_dict() if self.workspaces.current() else None,
            "workspaces": [item.public_dict() for item in self.workspaces.list(project_id=project.manifest.id if project else "")],
            "providers": self.providers.public_dicts(),
            "applications": self.applications.public_dicts(project_id=project.manifest.id if project else ""),
        }
