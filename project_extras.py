"""Project-scoped notes and saved workload configurations (non-secret, persisted as JSON)."""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from workload import WorkloadSpec

MAX_NOTES = 200
MAX_CONFIGS = 100


class ProjectExtras:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.data: dict[str, dict[str, list[dict[str, Any]]]] = {}
        if self.path and self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data = loaded
            except (OSError, ValueError):
                pass

    def _bucket(self, project: str) -> dict[str, list[dict[str, Any]]]:
        return self.data.setdefault(project or "default", {"notes": [], "configs": []})

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle)
        os.replace(tmp, self.path)

    def add_note(self, project: str, text: str) -> dict[str, Any]:
        text = str(text or "").strip()
        if not text or len(text) > 4000:
            raise ValueError("Notes must be 1-4000 characters.")
        note = {"time": dt.datetime.now(dt.timezone.utc).isoformat(), "text": text}
        notes = self._bucket(project)["notes"]
        notes.append(note)
        del notes[:-MAX_NOTES]
        self._save()
        return note

    def notes(self, project: str) -> list[dict[str, Any]]:
        return list(self._bucket(project)["notes"])

    def save_config(self, project: str, name: str, spec: WorkloadSpec) -> None:
        name = str(name or "").strip()
        if not name or len(name) > 80:
            raise ValueError("Configuration name must be 1-80 characters.")
        configs = [c for c in self._bucket(project)["configs"] if c["name"] != name]
        configs.append({"name": name, "spec": spec.to_dict()})
        self._bucket(project)["configs"] = configs[-MAX_CONFIGS:]
        self._save()

    def configs(self, project: str) -> list[dict[str, Any]]:
        return list(self._bucket(project)["configs"])

    def load_config(self, project: str, name: str) -> WorkloadSpec:
        for config in self._bucket(project)["configs"]:
            if config["name"] == name:
                return WorkloadSpec.from_dict(config["spec"])
        raise ValueError(f"No saved configuration named '{name}'.")
