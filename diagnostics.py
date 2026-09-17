"""Safe, non-secret health checks for the local app and active workspace."""

from __future__ import annotations

import importlib.util
import platform
import sys
from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class DiagnosticCheck:
    id: str
    status: str
    summary: str
    details: str = ""


class Doctor:
    def __init__(self, bridge):
        self.bridge = bridge

    async def run(self) -> dict[str, Any]:
        b = self.bridge
        checks = [
            DiagnosticCheck("runtime", "pass" if sys.version_info >= (3, 10) else "fail", f"Python {platform.python_version()}"),
            DiagnosticCheck("aiohttp", "pass" if importlib.util.find_spec("aiohttp") else "fail", "HTTP runtime available"),
            DiagnosticCheck("playwright", "pass" if importlib.util.find_spec("playwright") else "fail", "Browser automation package available"),
            DiagnosticCheck("local_helper", "pass", "Loopback helper is running"),
            DiagnosticCheck("browser", "pass" if b.context else "attention", "ARC browser session is available" if b.context else "Open ARC / sign in when remote workspace is needed"),
            DiagnosticCheck("workspace", "pass" if b.kernel else "attention", "Jupyter workspace attached" if b.kernel else "No Jupyter workspace attached"),
            DiagnosticCheck("model_key", "pass" if b.key else "attention", "Model key is held in memory" if b.key else "Model key has not been entered"),
        ]
        return {
            "version": getattr(b, "build", "unknown"),
            "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "state": getattr(getattr(b, "state_machine", None), "state", "unknown").value if getattr(getattr(b, "state_machine", None), "state", None) else "unknown",
            "profile": getattr(getattr(b, "profile", None), "id", "unknown"),
            "checks": [asdict(check) for check in checks],
        }
