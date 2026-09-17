"""Structured, user-actionable ARC Chat error classification."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str
    recovery: str


def classify_error(value: object, *, redactor=str) -> ErrorInfo:
    message = redactor(value)
    lower = message.lower()
    if "wait for the current action" in lower or "already in progress" in lower:
        return ErrorInfo("ACTION_BUSY", message, "Wait for the active action to finish, or interrupt Python if it is running.")
    if "ssh" in lower and ("timed out" in lower or "failed" in lower or "not installed" in lower or "not on path" in lower):
        return ErrorInfo("ARC_SSH_UNAVAILABLE", message, "Connect to the VT network/VPN, verify OpenSSH and SSH-key authentication to Falcon, then retry.")
    if "model api http 429" in lower or ("rate" in lower and "limit" in lower):
        return ErrorInfo("MODEL_RATE_LIMITED", message, "ARC is busy. ARC Chat uses bounded retries; retry later if the limit persists.")
    if "model api is temporarily unreachable" in lower:
        return ErrorInfo("MODEL_UNREACHABLE", message, "Check network/VPN access and the configured model endpoint, then retry the model request.")
    if "no jupyter tab" in lower or "jupyter is still opening" in lower:
        return ErrorInfo("JUPYTER_NOT_READY", message, "Open the ready Jupyter session in the visible ARC browser, then Attach automatically.")
    if "ood cannot reach this jupyter server" in lower or "jupyter http" in lower:
        return ErrorInfo("JUPYTER_PROXY_UNAVAILABLE", message, "Check My Interactive Sessions and re-open the running Jupyter job. Do not replay uncertain code.")
    if "arc returned http" in lower or ("vpn" in lower and "arc" in lower):
        return ErrorInfo("ARC_UNREACHABLE", message, "Connect to the Virginia Tech network/VPN and verify ARC opens in the visible browser.")
    if "invalid" in lower or "must" in lower or "unsupported" in lower:
        return ErrorInfo("INVALID_REQUEST", message, "Review the setting/action and submit a valid value.")
    return ErrorInfo("APP_ERROR", message, "Review the visible ARC/Jupyter state and retry only the failed action.")

