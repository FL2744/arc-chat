"""Safe, non-secret health checks for the local app and active workspace."""

from __future__ import annotations

import importlib.util
import platform
import sys
import uuid
from dataclasses import dataclass, asdict
from typing import Any

from aiohttp import ClientTimeout


@dataclass(frozen=True)
class DiagnosticCheck:
    id: str
    status: str
    summary: str
    details: str = ""


class Doctor:
    def __init__(self, bridge):
        self.bridge = bridge

    async def run(self, *, full: bool = False) -> dict[str, Any]:
        b = self.bridge
        profile = getattr(b, "profile", None)
        if getattr(b, "profile_error", ""):
            profile_check = DiagnosticCheck("profile", "fail", "Course profile is invalid", b.profile_error)
        elif profile and profile.workspace_backend == "arc_jupyter" and not profile.resolved_allocation():
            profile_check = DiagnosticCheck("profile", "attention", "Course allocation is not configured", "Set ARC_COURSE_ALLOCATION or select an authorized allocation in Advanced Mode.")
        else:
            profile_check = DiagnosticCheck("profile", "pass", "Course profile is valid")
        checks = [
            DiagnosticCheck("runtime", "pass" if sys.version_info >= (3, 10) else "fail", f"Python {platform.python_version()}"),
            DiagnosticCheck("aiohttp", "pass" if importlib.util.find_spec("aiohttp") else "fail", "HTTP runtime available"),
            DiagnosticCheck("playwright", "pass" if importlib.util.find_spec("playwright") else "fail", "Browser automation package available"),
            DiagnosticCheck("local_helper", "pass", "Loopback helper is running"),
            DiagnosticCheck("browser", "pass" if b.context else "attention", "ARC browser session is available" if b.context else "Open ARC / sign in when remote workspace is needed"),
            DiagnosticCheck("workspace", "pass" if b.kernel else "attention", "Jupyter workspace attached" if b.kernel else "No Jupyter workspace attached"),
            DiagnosticCheck("model_key", "pass" if b.key else "attention", "Model key is held in memory" if b.key else "Model key has not been entered"),
            profile_check,
        ]
        if full:
            checks.extend(await self._remote_checks())
        return {
            "version": getattr(b, "build", "unknown"),
            "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "state": getattr(getattr(b, "state_machine", None), "state", "unknown").value if getattr(getattr(b, "state_machine", None), "state", None) else "unknown",
            "profile": getattr(getattr(b, "profile", None), "id", "unknown"),
            "full": full,
            "checks": [asdict(check) for check in checks],
        }

    async def _remote_checks(self) -> list[DiagnosticCheck]:
        """Run explicit, bounded acceptance checks after the user asks for them."""
        b = self.bridge
        checks: list[DiagnosticCheck] = []
        pages = [p for p in (b.context.pages if b.context else []) if "ood.arc.vt.edu" in p.url]
        if not pages:
            checks.append(DiagnosticCheck("ood", "attention", "No visible ARC/OOD browser session", "Open ARC and complete VT login/MFA."))
        else:
            url = pages[-1].url.lower()
            authenticated = "login" not in url and "auth" not in url
            checks.append(DiagnosticCheck("ood", "pass" if authenticated else "attention", "Visible OOD session detected" if authenticated else "OOD login may still be required"))

        if b.kernel and b.http and b.model_config.get("endpoint") and b.key:
            try:
                endpoint = b.model_config["endpoint"].rstrip("/") + "/models"
                async with b.http.get(endpoint, headers={"Authorization": "Bearer " + b.key}, timeout=ClientTimeout(total=15), allow_redirects=False) as response:
                    status = "pass" if response.status < 400 else "attention"
                    checks.append(DiagnosticCheck("model_reachability", status, f"Model endpoint returned HTTP {response.status}"))
            except Exception as exc:
                checks.append(DiagnosticCheck("model_reachability", "attention", "Model endpoint could not be reached", type(exc).__name__))
        else:
            checks.append(DiagnosticCheck("model_reachability", "attention", "Model endpoint check skipped", "Attach a workspace and enter a model key first."))

        if not b.kernel:
            checks.append(DiagnosticCheck("jupyter_reachability", "attention", "Jupyter checks skipped", "Attach a workspace first."))
            return checks

        try:
            status = await b.workspace.status()
            checks.append(DiagnosticCheck("jupyter_reachability", "pass", "Jupyter kernel is reachable", status.get("state", "unknown")))
        except Exception as exc:
            checks.append(DiagnosticCheck("jupyter_reachability", "fail", "Jupyter kernel is not reachable", type(exc).__name__))
            return checks

        path = ".arc-chat-doctor-" + uuid.uuid4().hex[:10] + ".txt"
        try:
            await b.api("PUT", "api/contents/" + b.quote_path(path), {"type": "file", "format": "text", "content": "ARC Chat Doctor"})
            checks.append(DiagnosticCheck("remote_write", "pass", "Remote workspace write succeeded"))
        except Exception as exc:
            checks.append(DiagnosticCheck("remote_write", "fail", "Remote workspace write failed", type(exc).__name__))
        finally:
            try:
                await b.api("DELETE", "api/contents/" + b.quote_path(path))
            except Exception:
                pass

        if b.executing:
            checks.append(DiagnosticCheck("kernel_smoke", "attention", "Kernel smoke test skipped", "The kernel is busy; do not interrupt user code."))
        else:
            try:
                output = await b.workspace.execute('print("ARC Chat Doctor smoke test")')
                checks.append(DiagnosticCheck("kernel_smoke", "pass" if "ARC Chat Doctor" in output else "attention", "Kernel execution smoke test completed"))
            except Exception as exc:
                checks.append(DiagnosticCheck("kernel_smoke", "fail", "Kernel execution smoke test failed", type(exc).__name__))
        return checks
