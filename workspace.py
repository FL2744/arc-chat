"""Execution-backend seam for ARC Chat.

The current implementation delegates to the proven bridge transport. Keeping
the public workspace contract here lets a local backend and future managed ARC
workspaces be added without coupling the UI to Jupyter details.
"""

from __future__ import annotations

from typing import Any, Protocol


class Workspace(Protocol):
    async def start(self, url: str = "", kernel_name: str = "python3") -> str: ...
    async def status(self) -> dict[str, Any]: ...
    async def execute(self, code: str) -> str: ...
    async def interrupt(self) -> None: ...
    async def list_files(self, path: str = "") -> list[dict[str, Any]]: ...
    async def stop(self) -> str: ...


class JupyterWorkspace:
    """Jupyter workspace adapter used by the orchestrating bridge."""

    backend = "arc_jupyter"

    def __init__(self, bridge):
        self.bridge = bridge

    async def start(self, url: str = "", kernel_name: str = "python3") -> str:
        return await self.bridge.attach(url, kernel_name)

    async def status(self) -> dict[str, Any]:
        if not self.bridge.kernel:
            return {"state": "disconnected", "kernel": None, "notebook": None}
        info = await self.bridge.api("GET", "api/kernels/" + self.bridge.kernel)
        return {
            "state": info.get("execution_state", "unknown"),
            "kernel": self.bridge.kernel,
            "session": self.bridge.session,
            "notebook": self.bridge.notebook_path,
            "base": self.bridge.base,
        }

    async def execute(self, code: str) -> str:
        return await self.bridge.execute(code)

    async def interrupt(self) -> None:
        if self.bridge.kernel:
            await self.bridge.api("POST", f"api/kernels/{self.bridge.kernel}/interrupt")

    async def list_files(self, path: str = "") -> list[dict[str, Any]]:
        listing = await self.bridge.api("GET", "api/contents/" + self.bridge.quote_path(path))
        return listing["content"]

    async def stop(self) -> str:
        return await self.bridge.stop_workspace()
