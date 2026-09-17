"""Versioned local command protocol and replay protection for ARC Chat."""

from __future__ import annotations

import json
import re
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Mapping


PROTOCOL_VERSION = 1
ACTION_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")


@dataclass(frozen=True)
class CommandEnvelope:
    version: int
    id: str
    action: str
    payload: dict[str, Any]

    @classmethod
    def parse(cls, value: str | bytes | Mapping[str, Any]) -> "CommandEnvelope":
        if isinstance(value, (str, bytes)):
            try:
                value = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError("Command must be valid JSON.") from exc
        if not isinstance(value, Mapping):
            raise ValueError("Command must be a JSON object.")

        # Backward-compatible parser for the original UI packet shape. New UI
        # clients send the explicit envelope below.
        if "payload" not in value and "action" in value:
            action = value.get("action")
            payload = {k: v for k, v in value.items() if k not in {"version", "id", "type", "action"}}
        else:
            action = value.get("action")
            payload = value.get("payload", {})

        version = value.get("version", PROTOCOL_VERSION)
        request_id = value.get("id") or f"legacy-{uuid.uuid4().hex}"
        if version != PROTOCOL_VERSION:
            raise ValueError(f"Unsupported protocol version: {version!r}.")
        if value.get("type", "command") != "command":
            raise ValueError("Incoming WebSocket messages must be commands.")
        if not isinstance(action, str) or not ACTION_RE.fullmatch(action):
            raise ValueError("Invalid command action.")
        if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
            raise ValueError("Invalid request id.")
        if not isinstance(payload, Mapping):
            raise ValueError("Command payload must be an object.")
        return cls(PROTOCOL_VERSION, request_id, action, dict(payload))


class ReplayCache:
    """Small bounded cache used to make UI mutations idempotent on reconnect."""

    def __init__(self, limit: int = 256):
        self.limit = max(16, int(limit))
        self._items: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def get(self, request_id: str) -> dict[str, Any] | None:
        result = self._items.get(request_id)
        if result is not None:
            self._items.move_to_end(request_id)
            return dict(result)
        return None

    def put(self, request_id: str, result: Mapping[str, Any]) -> None:
        self._items[request_id] = dict(result)
        self._items.move_to_end(request_id)
        while len(self._items) > self.limit:
            self._items.popitem(last=False)

