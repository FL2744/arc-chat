"""Structured command-execution surface with login-node safety and visible approval.

Every command, including model-generated ones, is first *proposed*. Nothing runs
until the user confirms a specific proposal. Commands classified as heavy are
never run on the login node; they are converted into a compute-job plan instead.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from identifiers import ID_PREFIXES, new_id
from safety import HEAVY, LIGHT, classify_command

ID_PREFIXES.setdefault("command", "cmd")

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
MAX_OUTPUT_CHARS = 200_000


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


@dataclass
class CommandProposal:
    id: str
    command: str
    origin: str                 # "user" | "assistant" | "external-model"
    verdict: str
    explanation: str
    context: str = "login"      # where it would run: login | compute
    status: str = "proposed"    # proposed | running | done | failed | cancelled | rejected | redirected
    created_at: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat())
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    redirect_spec: dict[str, Any] | None = None

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class CommandSurface:
    """Holds proposals and history; executes approved light commands through a gateway."""

    def __init__(self, gateway_factory, *, history_limit: int = 200):
        self._gateway_factory = gateway_factory   # callable(d) -> gateway with run_detailed()
        self.items: dict[str, CommandProposal] = {}
        self.order: list[str] = []
        self.limit = history_limit
        self._tasks: dict[str, asyncio.Task] = {}

    def propose(self, command: str, *, origin: str = "user", context: str = "login") -> CommandProposal:
        command = str(command or "").strip()
        if not command:
            raise ValueError("Enter a command to run.")
        if "\x00" in command or len(command) > 8000:
            raise ValueError("Command is empty, too long, or contains invalid characters.")
        decision = classify_command(command)
        if context == "compute":
            # Already inside a compute allocation; heavy work is acceptable there.
            verdict, explanation = LIGHT, "Runs inside your compute allocation."
        else:
            verdict, explanation = decision.verdict, decision.explanation()
        proposal = CommandProposal(new_id("command"), command, origin, verdict, explanation, context)
        self.items[proposal.id] = proposal
        self.order.append(proposal.id)
        while len(self.order) > self.limit:
            self.items.pop(self.order.pop(0), None)
        return proposal

    def get(self, command_id: str) -> CommandProposal:
        try:
            return self.items[command_id]
        except KeyError:
            raise ValueError("Unknown command proposal.") from None

    def history(self) -> list[dict[str, Any]]:
        return [self.items[i].public_dict() for i in self.order if i in self.items]

    def reject(self, command_id: str) -> CommandProposal:
        proposal = self.get(command_id)
        if proposal.status == "proposed":
            proposal.status = "rejected"
        return proposal

    def redirect_to_job(self, command_id: str, spec_fields: dict[str, Any]) -> CommandProposal:
        """Mark a heavy proposal as redirected and attach the job plan that replaces it."""
        proposal = self.get(command_id)
        proposal.status = "redirected"
        proposal.redirect_spec = spec_fields
        return proposal

    async def run(self, command_id: str, d: dict[str, Any], *, confirmed: bool, timeout: float = 120.0) -> CommandProposal:
        proposal = self.get(command_id)
        if not confirmed:
            raise ValueError("Review the command and confirm it before it runs.")
        if proposal.status != "proposed":
            raise ValueError(f"This command is already {proposal.status}.")
        if proposal.verdict != LIGHT:
            raise ValueError(proposal.explanation)
        gateway = self._gateway_factory(d)
        proposal.status = "running"
        task = asyncio.ensure_future(gateway.run_detailed(proposal.command, timeout=timeout))
        self._tasks[command_id] = task
        try:
            result = await task
        except asyncio.CancelledError:
            proposal.status = "cancelled"
            return proposal
        except Exception as exc:
            proposal.status, proposal.stderr = "failed", str(exc)
            return proposal
        finally:
            self._tasks.pop(command_id, None)
        proposal.stdout = strip_ansi(result.stdout)[-MAX_OUTPUT_CHARS:]
        proposal.stderr = strip_ansi(result.stderr)[-MAX_OUTPUT_CHARS:]
        proposal.returncode = result.returncode
        proposal.status = "done" if result.returncode == 0 else "failed"
        return proposal

    def cancel(self, command_id: str) -> CommandProposal:
        proposal = self.get(command_id)
        task = self._tasks.get(command_id)
        if task:
            task.cancel()
        elif proposal.status == "proposed":
            proposal.status = "cancelled"
        return proposal
