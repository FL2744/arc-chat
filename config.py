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


PROFILE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class CourseProfile:
    id: str
    name: str
    workspace_backend: str = "arc_jupyter"
    cluster: str = ""
    allocation: str = ""
    resource_profile: str = "classroom-small"
    model_provider: str = "arc_shared"
    model_policy: str = "instructor_default"
    advanced_mode: bool = False

    def __post_init__(self) -> None:
        if not PROFILE_ID.fullmatch(self.id):
            raise ValueError(f"Invalid course profile id: {self.id!r}")
        if not self.name.strip():
            raise ValueError("Course profile name cannot be empty.")
        if self.workspace_backend not in {"arc_jupyter", "local"}:
            raise ValueError(f"Unsupported workspace backend: {self.workspace_backend}")
        if self.model_provider not in {"arc_shared", "openai", "custom"}:
            raise ValueError(f"Unsupported model provider: {self.model_provider}")

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


# This profile is intentionally useful without embedding a person's allocation.
# An instructor can distribute ARC_COURSE_ALLOCATION or a profile file.
BUILTIN_PROFILES: dict[str, CourseProfile] = {
    "fl2744": CourseProfile(
        id="fl2744",
        name="FL 2744",
        cluster="Falcon",
        allocation="${ARC_COURSE_ALLOCATION}",
        resource_profile="classroom-small",
        model_provider="arc_shared",
        model_policy="instructor_default",
        advanced_mode=False,
    ),
    "default": CourseProfile(
        id="default",
        name="Personal / Advanced",
        resource_profile="user-selected",
        model_provider="arc_shared",
        model_policy="user_selected",
        advanced_mode=True,
    ),
}


def _profile_from_mapping(value: Mapping[str, Any]) -> CourseProfile:
    allowed = {"id", "name", "workspace_backend", "cluster", "allocation",
               "resource_profile", "model_provider", "model_policy", "advanced_mode"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("Unknown course profile fields: " + ", ".join(sorted(unknown)))
    return CourseProfile(**{key: value[key] for key in allowed if key in value})


def load_profiles(path: str | os.PathLike[str] | None = None) -> dict[str, CourseProfile]:
    """Load optional JSON profiles and overlay the safe built-ins."""

    profiles = dict(BUILTIN_PROFILES)
    selected_path = path or os.environ.get("ARC_CHAT_PROFILE_FILE")
    if not selected_path:
        return profiles
    profile_path = Path(selected_path).expanduser()
    with profile_path.open(encoding="utf-8") as stream:
        raw = json.load(stream)
    values = raw.get("profiles", raw) if isinstance(raw, Mapping) else raw
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
