"""Execution-provider and placement contracts for ARC Chat.

This module models capabilities rather than implementation vendors. ARC is the
first full remote provider; JupyterLite is the zero-install browser provider.
Persistent container/cloud providers can be registered later without changing
Student Mode's project/workspace vocabulary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ProviderDescriptor:
    id: str
    label: str
    modes: tuple[str, ...]
    status: str = "available"
    supports_gpu: bool = False
    supports_server_packages: bool = False
    supports_persistent_service: bool = False
    recommended_inline_mb: int | None = None
    launch_url: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.id or len(self.id) > 64:
            raise ValueError("Provider id must be a short non-empty string.")
        if self.status not in {"available", "experimental", "planned", "disabled"}:
            raise ValueError("Unsupported provider status.")
        allowed_modes = {"browser", "interactive", "batch", "service"}
        if not self.modes or not set(self.modes) <= allowed_modes:
            raise ValueError("Provider modes are invalid.")
        if self.recommended_inline_mb is not None and self.recommended_inline_mb < 1:
            raise ValueError("recommended_inline_mb must be positive.")

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["modes"] = list(self.modes)
        return data


@dataclass(frozen=True)
class PlacementRequest:
    mode: str = "interactive"
    preferred_provider: str = "auto"
    needs_gpu: bool = False
    requires_server_packages: bool = False
    persistent_service: bool = False
    estimated_input_mb: int | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"browser", "interactive", "batch", "service"}:
            raise ValueError("Unsupported execution mode.")
        if self.estimated_input_mb is not None and self.estimated_input_mb < 0:
            raise ValueError("estimated_input_mb cannot be negative.")


@dataclass(frozen=True)
class PlacementDecision:
    provider_id: str
    action: str
    reason: str
    confidence: str
    requires_review: bool = False

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProviderRegistry:
    def __init__(self, providers: Iterable[ProviderDescriptor] = ()):
        self._items: dict[str, ProviderDescriptor] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: ProviderDescriptor) -> ProviderDescriptor:
        self._items[provider.id] = provider
        return provider

    def get(self, provider_id: str) -> ProviderDescriptor:
        try:
            return self._items[provider_id]
        except KeyError as exc:
            raise ValueError(f"Unknown execution provider: {provider_id}") from exc

    def list(self) -> list[ProviderDescriptor]:
        return list(self._items.values())

    def public_dicts(self) -> list[dict[str, Any]]:
        return [item.public_dict() for item in self.list()]


class PlacementEngine:
    """Small deterministic policy engine; it never provisions resources itself."""

    def __init__(self, registry: ProviderRegistry):
        self.registry = registry

    @staticmethod
    def _fits_capabilities(provider: ProviderDescriptor, request: PlacementRequest) -> bool:
        if request.mode not in provider.modes:
            return False
        if request.needs_gpu and not provider.supports_gpu:
            return False
        if request.requires_server_packages and not provider.supports_server_packages:
            return False
        if request.persistent_service and not provider.supports_persistent_service:
            return False
        if (
            request.estimated_input_mb is not None
            and provider.recommended_inline_mb is not None
            and request.estimated_input_mb > provider.recommended_inline_mb
            and provider.id == "browser"
        ):
            return False
        return True

    @classmethod
    def _supports(cls, provider: ProviderDescriptor, request: PlacementRequest) -> bool:
        return cls._fits_capabilities(provider, request) and provider.status in {"available", "experimental"}

    def decide(self, request: PlacementRequest, *, allowed_providers: Iterable[str] = ()) -> PlacementDecision:
        allowed = tuple(allowed_providers)
        candidates = [p for p in self.registry.list() if not allowed or p.id in allowed]

        if request.preferred_provider != "auto":
            provider = self.registry.get(request.preferred_provider)
            if allowed and provider.id not in allowed:
                return PlacementDecision("", "review", "Preferred provider is not allowed for this project.", "high", True)
            if self._supports(provider, request):
                return PlacementDecision(provider.id, "use", f"Use requested provider {provider.label}.", "high")
            return PlacementDecision(provider.id, "review", f"{provider.label} does not satisfy the requested capabilities.", "high", True)

        browser = next((p for p in candidates if p.id == "browser"), None)
        if browser and request.mode in {"browser", "interactive"} and self._supports(browser, request):
            return PlacementDecision("browser", "use", "This workload can run immediately in the browser without an ARC job.", "high")

        arc = next((p for p in candidates if p.id == "arc"), None)
        if arc and request.mode in {"interactive", "batch"} and self._supports(arc, request):
            return PlacementDecision("arc", "use", "This workload needs managed research compute or server-side packages.", "high")

        persistent = next((p for p in candidates if p.supports_persistent_service and self._supports(p, request)), None)
        if persistent:
            review = persistent.status != "available"
            return PlacementDecision(
                persistent.id,
                "review" if review else "use",
                "A persistent application service is the appropriate execution class.",
                "medium" if review else "high",
                review,
            )

        planned = next((
            p for p in candidates
            if p.status == "planned" and self._fits_capabilities(p, request)
        ), None)
        if planned:
            return PlacementDecision(
                planned.id,
                "review",
                f"{planned.label} fits this workload, but the provider integration is not enabled yet.",
                "medium",
                True,
            )

        return PlacementDecision("", "review", "No configured provider satisfies this workload.", "high", True)


def default_provider_registry(*, jupyterlite_url: str = "") -> ProviderRegistry:
    return ProviderRegistry([
        ProviderDescriptor(
            id="browser",
            label="Browser / JupyterLite",
            modes=("browser", "interactive"),
            status="available",
            recommended_inline_mb=20,
            launch_url=jupyterlite_url,
            notes="Zero-install browser compute for small, browser-compatible workloads.",
        ),
        ProviderDescriptor(
            id="arc",
            label="Virginia Tech ARC",
            modes=("interactive", "batch"),
            status="available",
            supports_gpu=True,
            supports_server_packages=True,
            notes="ARC/Open OnDemand/Slurm research compute. Human review remains the resource-allocation boundary.",
        ),
        ProviderDescriptor(
            id="common-platform",
            label="VT IT Common Platform",
            modes=("service",),
            status="planned",
            supports_server_packages=True,
            supports_persistent_service=True,
            notes="Planned persistent application target; no deployment automation is enabled yet.",
        ),
        ProviderDescriptor(
            id="cloud",
            label="Managed cloud",
            modes=("batch", "service"),
            status="planned",
            supports_gpu=True,
            supports_server_packages=True,
            supports_persistent_service=True,
            notes="Future provider for workloads that do not fit browser, ARC, or institutional container hosting.",
        ),
    ])
