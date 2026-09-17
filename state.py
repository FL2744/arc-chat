"""Explicit application state machine shared by Student and Advanced modes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from time import time


class AppState(str, Enum):
    APP_STARTING = "APP_STARTING"
    READY_LOCAL = "READY_LOCAL"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTHENTICATING = "AUTHENTICATING"
    ARC_READY = "ARC_READY"
    WORKSPACE_STARTING = "WORKSPACE_STARTING"
    JOB_QUEUED = "JOB_QUEUED"
    JUPYTER_STARTING = "JUPYTER_STARTING"
    WORKSPACE_READY = "WORKSPACE_READY"
    EXECUTING = "EXECUTING"
    INPUT_REQUIRED = "INPUT_REQUIRED"
    RECOVERING = "RECOVERING"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"
    SHUTTING_DOWN = "SHUTTING_DOWN"


DISPLAY_STATE = {
    AppState.APP_STARTING: "Starting",
    AppState.READY_LOCAL: "Ready locally",
    AppState.AUTH_REQUIRED: "Sign in required",
    AppState.AUTHENTICATING: "Signing in",
    AppState.ARC_READY: "ARC ready",
    AppState.WORKSPACE_STARTING: "Starting workspace",
    AppState.JOB_QUEUED: "Workspace queued",
    AppState.JUPYTER_STARTING: "Connecting workspace",
    AppState.WORKSPACE_READY: "Ready",
    AppState.EXECUTING: "Running",
    AppState.INPUT_REQUIRED: "Input required",
    AppState.RECOVERING: "Recovering",
    AppState.DEGRADED: "Needs attention",
    AppState.ERROR: "Error",
    AppState.SHUTTING_DOWN: "Stopping",
}


class InvalidTransition(ValueError):
    pass


_ANY = frozenset(AppState)
TRANSITIONS = {
    AppState.APP_STARTING: {AppState.READY_LOCAL, AppState.ERROR},
    AppState.READY_LOCAL: {AppState.AUTH_REQUIRED, AppState.AUTHENTICATING, AppState.WORKSPACE_STARTING, AppState.ERROR},
    AppState.AUTH_REQUIRED: {AppState.AUTHENTICATING, AppState.READY_LOCAL, AppState.ERROR},
    AppState.AUTHENTICATING: {AppState.ARC_READY, AppState.AUTH_REQUIRED, AppState.DEGRADED, AppState.ERROR},
    AppState.ARC_READY: {AppState.WORKSPACE_STARTING, AppState.AUTH_REQUIRED, AppState.DEGRADED, AppState.ERROR},
    AppState.WORKSPACE_STARTING: {AppState.JOB_QUEUED, AppState.JUPYTER_STARTING, AppState.WORKSPACE_READY, AppState.DEGRADED, AppState.ERROR},
    AppState.JOB_QUEUED: {AppState.JUPYTER_STARTING, AppState.WORKSPACE_STARTING, AppState.DEGRADED, AppState.ERROR},
    AppState.JUPYTER_STARTING: {AppState.WORKSPACE_READY, AppState.RECOVERING, AppState.DEGRADED, AppState.ERROR},
    AppState.WORKSPACE_READY: {AppState.EXECUTING, AppState.RECOVERING, AppState.AUTH_REQUIRED, AppState.SHUTTING_DOWN, AppState.DEGRADED, AppState.ERROR},
    AppState.EXECUTING: {AppState.WORKSPACE_READY, AppState.INPUT_REQUIRED, AppState.RECOVERING, AppState.ERROR},
    AppState.INPUT_REQUIRED: {AppState.EXECUTING, AppState.WORKSPACE_READY, AppState.RECOVERING, AppState.ERROR},
    AppState.RECOVERING: {AppState.WORKSPACE_READY, AppState.AUTH_REQUIRED, AppState.DEGRADED, AppState.ERROR},
    AppState.DEGRADED: {AppState.READY_LOCAL, AppState.AUTH_REQUIRED, AppState.ARC_READY, AppState.WORKSPACE_STARTING, AppState.WORKSPACE_READY, AppState.RECOVERING, AppState.ERROR},
    AppState.ERROR: {AppState.READY_LOCAL, AppState.AUTH_REQUIRED, AppState.RECOVERING, AppState.SHUTTING_DOWN},
    AppState.SHUTTING_DOWN: {AppState.READY_LOCAL, AppState.ERROR},
}


@dataclass(frozen=True)
class StateSnapshot:
    state: AppState
    display: str
    reason: str
    changed_at: float

    def as_dict(self) -> dict[str, str | float]:
        return {"state": self.state.value, "display": self.display, "reason": self.reason, "changed_at": self.changed_at}


class AppStateMachine:
    def __init__(self, initial: AppState = AppState.APP_STARTING):
        self.state = initial
        self.history: list[StateSnapshot] = []

    def transition(self, target: AppState, reason: str = "") -> StateSnapshot:
        target = AppState(target)
        if target != self.state and target not in TRANSITIONS.get(self.state, _ANY):
            raise InvalidTransition(f"Cannot transition from {self.state.value} to {target.value}.")
        snapshot = StateSnapshot(target, DISPLAY_STATE[target], reason, time())
        self.state = target
        self.history.append(snapshot)
        return snapshot

    def force(self, target: AppState, reason: str = "") -> StateSnapshot:
        """Record an externally detected failure/recovery without hiding it."""
        snapshot = StateSnapshot(AppState(target), DISPLAY_STATE[AppState(target)], reason, time())
        self.state = snapshot.state
        self.history.append(snapshot)
        return snapshot
