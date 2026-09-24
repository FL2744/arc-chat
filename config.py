"""Configuration and course-profile loading for ARC Chat.

Profiles deliberately contain policy, not secrets.  A profile may reference an
environment variable for an allocation, but a personal allocation is never
committed as an application default.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit


PROFILE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
PROFILE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CourseProfile:
    id: str
    name: str
    workspace_backend: str = "arc_jupyter"
    project_id: str = ""
    cluster: str = ""
    allocation: str = ""
    resource_profile: str = "classroom-small"
    jupyterlite_url: str = ""
    allowed_providers: tuple[str, ...] = ("browser", "arc")
    model_provider: str = "arc_shared"
    model_policy: str = "instructor_default"
    allowed_models: tuple[str, ...] = ()
    advanced_mode: bool = False

    def __post_init__(self) -> None:
        if not PROFILE_ID.fullmatch(self.id):
            raise ValueError(f"Invalid course profile id: {self.id!r}")
        if not self.name.strip():
            raise ValueError("Course profile name cannot be empty.")
        if self.workspace_backend not in {"arc_jupyter", "local"}:
            raise ValueError(f"Unsupported workspace backend: {self.workspace_backend}")
        if self.project_id and not PROFILE_ID.fullmatch(self.project_id):
            raise ValueError(f"Invalid project id: {self.project_id!r}")
        providers = tuple(str(item).strip() for item in self.allowed_providers)
        known_providers = {"browser", "arc", "common-platform", "cloud"}
        if not providers or any(item not in known_providers for item in providers):
            raise ValueError("allowed_providers contains an unsupported execution provider.")
        object.__setattr__(self, "allowed_providers", providers)
        if self.jupyterlite_url:
            parsed = urlsplit(self.jupyterlite_url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("jupyterlite_url must be a public HTTPS URL without embedded credentials.")
        if self.model_provider not in {"arc_shared", "arc_dedicated", "openai", "custom"}:
            raise ValueError(f"Unsupported model provider: {self.model_provider}")
        if any(not isinstance(model, str) or not model.strip() for model in self.allowed_models):
            raise ValueError("allowed_models must contain non-empty model IDs.")

    def resolved_allocation(self, environ: Mapping[str, str] | None = None) -> str:
        """Return the configured allocation, resolving ``${VAR}`` safely."""

        environ = os.environ if environ is None else environ
        value = self.allocation.strip()
        if not value:
            return ""
        match = re.fullmatch(r"\$\{([A-Z_][A-Z0-9_]*)\}", value)
        if match:
            return environ.get(match.group(1), "").strip()
        return value

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data.get("allocation"):
            data["allocation_configured"] = True
            data["allocation"] = ""
        return data

    def allowed_provider_names(self, advanced: bool) -> set[str]:
        """Return protocol provider names permitted by this profile."""
        if advanced and self.model_policy == "user_selected":
            return {"arc", "arc_dedicated", "openai", "custom", "managed"}
        provider = {"arc_shared": "arc", "arc_dedicated": "arc_dedicated", "openai": "openai", "custom": "custom"}[self.model_provider]
        return {provider}

    def allows_model(self, provider: str, model: str, advanced: bool) -> bool:
        if provider not in self.allowed_provider_names(advanced):
            return False
        return not self.allowed_models or model in self.allowed_models


# This profile is intentionally useful without embedding a person's allocation.
# An instructor can distribute ARC_COURSE_ALLOCATION or a profile file.
BUILTIN_PROFILES: dict[str, CourseProfile] = {
    "fl2744": CourseProfile(
        id="fl2744",
        name="FL 2744",
        project_id="fl2744",
        cluster="Falcon",
        jupyterlite_url="https://fl2744.github.io/jupyterlite/lab/index.html",
        allowed_providers=("browser", "arc"),
        allocation="${ARC_COURSE_ALLOCATION}",
        resource_profile="classroom-small",
        model_provider="arc_shared",
        model_policy="instructor_default",
        allowed_models=("gpt-oss-120b",),
        advanced_mode=False,
    ),
    "default": CourseProfile(
        id="default",
        name="Personal / Advanced",
        project_id="personal",
        resource_profile="user-selected",
        allowed_providers=("browser", "arc", "common-platform", "cloud"),
        model_provider="arc_shared",
        model_policy="user_selected",
        advanced_mode=True,
    ),
}


def _profile_from_mapping(value: Mapping[str, Any]) -> CourseProfile:
    allowed = {"id", "name", "workspace_backend", "project_id", "cluster", "allocation",
               "resource_profile", "jupyterlite_url", "allowed_providers",
               "model_provider", "model_policy", "allowed_models", "advanced_mode"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("Unknown course profile fields: " + ", ".join(sorted(unknown)))
    data = {key: value[key] for key in allowed if key in value}
    if isinstance(data.get("allowed_providers"), list):
        data["allowed_providers"] = tuple(data["allowed_providers"])
    if isinstance(data.get("allowed_models"), list):
        data["allowed_models"] = tuple(data["allowed_models"])
    return CourseProfile(**data)


def load_profiles(path: str | os.PathLike[str] | None = None) -> dict[str, CourseProfile]:
    """Load optional JSON profiles and overlay the safe built-ins."""

    profiles = dict(BUILTIN_PROFILES)
    selected_path = path or os.environ.get("ARC_CHAT_PROFILE_FILE")
    if not selected_path:
        return profiles
    profile_path = Path(selected_path).expanduser()
    with profile_path.open(encoding="utf-8") as stream:
        raw = json.load(stream)
    if isinstance(raw, Mapping) and "profiles" in raw:
        version = raw.get("version", PROFILE_SCHEMA_VERSION)
        if version != PROFILE_SCHEMA_VERSION:
            raise ValueError(f"Unsupported course profile schema version {version!r}; expected {PROFILE_SCHEMA_VERSION}.")
        values = raw["profiles"]
    else:
        # Keep the original list-only form readable for older local configs.
        values = raw
    if not isinstance(values, list):
        raise ValueError("Course profile configuration must contain a profiles list.")
    for item in values:
        if not isinstance(item, Mapping):
            raise ValueError("Each course profile must be an object.")
        profile = _profile_from_mapping(item)
        profiles[profile.id] = profile
    return profiles


def get_profile(profile_id: str | None = None, path: str | os.PathLike[str] | None = None) -> CourseProfile:
    profiles = load_profiles(path)
    requested = profile_id or os.environ.get("ARC_CHAT_PROFILE", "fl2744")
    if requested not in profiles:
        raise ValueError(f"Unknown course profile {requested!r}. Available: {', '.join(sorted(profiles))}")
    return profiles[requested]
