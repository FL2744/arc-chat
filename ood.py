"""Open OnDemand/browser adapter boundary."""

from __future__ import annotations

from state import AppState


class OODBrowserAdapter:
    """Visible VT/OOD workflow exposed as a small orchestration interface."""

    backend = "ood_browser"

    def __init__(self, bridge):
        self.bridge = bridge

    async def open(self) -> str:
        return await self.bridge.browser_open()

    async def prepare(self, account: str = "") -> str:
        try:
            return await self.bridge.prepare(account)
        except ValueError as exc:
            # Starting a course workspace normally opens the visible browser
            # before the user can finish VT login/MFA.  That is an expected
            # intermediate state, not an application error.  Keep the same
            # browser session and make the single Start Workspace action safely
            # retryable after authentication completes.
            if str(exc) != "Finish VT login first.":
                raise
            await self.bridge.set_state(
                AppState.AUTH_REQUIRED,
                "Complete VT login/MFA in the visible browser, then retry the course workspace action.",
                force=True,
            )
            return "Complete VT login/MFA in the visible ARC browser, then click Start course workspace again. Your browser session will be reused."

    async def launch(self) -> str:
        return await self.bridge.browser_click("launch")

    async def connect(self) -> str:
        return await self.bridge.browser_click("connect")

    async def discover_jupyter(self) -> str:
        return await self.bridge.discover_jupyter()
