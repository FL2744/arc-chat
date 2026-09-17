"""Open OnDemand/browser adapter boundary."""

from __future__ import annotations


class OODBrowserAdapter:
    """Visible VT/OOD workflow exposed as a small orchestration interface."""

    backend = "ood_browser"

    def __init__(self, bridge):
        self.bridge = bridge

    async def open(self) -> str:
        return await self.bridge.browser_open()

    async def prepare(self, account: str = "") -> str:
        return await self.bridge.prepare(account)

    async def launch(self) -> str:
        return await self.bridge.browser_click("launch")

    async def connect(self) -> str:
        return await self.bridge.browser_click("connect")

    async def discover_jupyter(self) -> str:
        return await self.bridge.discover_jupyter()
