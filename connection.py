"""Staged ARC connection validation (username, network, authentication, account access)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from discovery import ACCOUNTS_COMMAND, parse_accounts
from sshkeys import translate_ssh_failure, validate_arc_username

STAGES = ("username", "network_and_auth", "account_access")
_HOST_LINE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


@dataclass
class StageResult:
    stage: str
    ok: bool
    message: str
    detail: str = ""

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


async def validate_connection(username: str, host: str, gateway_factory, *, expected_allocation: str = "") -> dict[str, Any]:
    """Run the staged checks; stops at the first failed stage and says which stage failed.

    ``gateway_factory(username, host)`` must return an object with ``run_detailed``.
    """
    stages: list[StageResult] = []
    try:
        username = validate_arc_username(username)
    except ValueError as exc:
        return _finish([StageResult("username", False, str(exc))])
    stages.append(StageResult("username", True, f"Username '{username}' has a valid format."))

    gateway = gateway_factory(username, host)
    result = await gateway.run_detailed("hostname && whoami", timeout=25)
    if result.returncode != 0:
        stages.append(StageResult("network_and_auth", False, translate_ssh_failure(result.stderr, username),
                                  result.stderr.strip()[-1500:]))
        return _finish(stages)
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    login_host = lines[0] if lines and _HOST_LINE.fullmatch(lines[0]) else host
    remote_user = lines[1] if len(lines) > 1 else ""
    if remote_user and remote_user != username:
        stages.append(StageResult("network_and_auth", False,
                                  f"Connected, but ARC reports the account '{remote_user}', not '{username}'. Check the username.",
                                  result.stdout.strip()))
        return _finish(stages)
    stages.append(StageResult("network_and_auth", True, f"SSH key authentication to {login_host} succeeded."))

    accounts = await gateway.run_detailed(ACCOUNTS_COMMAND, timeout=25)
    if accounts.returncode != 0:
        stages.append(StageResult("account_access", False,
                                  "Could not list your ARC allocations. Scheduler accounting may be unavailable; try again shortly.",
                                  accounts.stderr.strip()[-1500:]))
        return _finish(stages, login_host=login_host)
    found = parse_accounts(accounts.stdout)
    if not found:
        stages.append(StageResult("account_access", False,
                                  "Authenticated, but no ARC allocations are associated with this account. "
                                  "Ask your PI or ARC support to add you to an allocation."))
        return _finish(stages, login_host=login_host)
    if expected_allocation and expected_allocation not in found:
        stages.append(StageResult("account_access", False,
                                  f"Allocation '{expected_allocation}' is not available to this account. Available: {', '.join(found)}."))
        return _finish(stages, login_host=login_host, allocations=found)
    stages.append(StageResult("account_access", True, f"{len(found)} allocation(s) available: {', '.join(found)}."))
    return _finish(stages, login_host=login_host, allocations=found)


def _finish(stages: list[StageResult], **extra: Any) -> dict[str, Any]:
    return {"ok": all(s.ok for s in stages) and len(stages) == len(STAGES),
            "failed_stage": next((s.stage for s in stages if not s.ok), ""),
            "stages": [s.public_dict() for s in stages], **extra}
