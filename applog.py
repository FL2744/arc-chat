"""Structured application logging with levels and secret redaction."""

from __future__ import annotations

import collections
import datetime as dt
import json
import re
import sys
from typing import Any

from security import REDACTED, _SECRET_FIELD

LEVELS = {"normal": 20, "verbose": 15, "debug": 10}
_KV_SECRET = re.compile(
    r"(?i)((?:password|passphrase|secret|token|api[_-]?key|authorization)\s*[=:]\s*)(?:Bearer\s+)?[^\s,;\"']+"
)
_BEARER = re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]{8,}")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)


def scrub(text: str) -> str:
    text = _PRIVATE_KEY.sub(REDACTED, str(text))
    text = _BEARER.sub("Bearer " + REDACTED, text)
    return _KV_SECRET.sub(lambda m: m.group(1) + REDACTED, text)


class AppLog:
    """Ring-buffered structured log. ``level`` is one of normal/verbose/debug.

    Event levels: ``info`` always logs; ``verbose`` and ``debug`` events are kept only
    when the configured level is at least that detailed. Extra fields whose names look
    like credentials are dropped and all strings are scrubbed.
    """

    def __init__(self, level: str = "normal", capacity: int = 2000, sink=None):
        self.buffer: collections.deque[dict[str, Any]] = collections.deque(maxlen=capacity)
        self.sink = sink
        self.set_level(level)
        self.secrets: set[str] = set()

    def set_level(self, level: str) -> None:
        if level not in LEVELS:
            raise ValueError(f"Unknown log level '{level}'. Choose normal, verbose or debug.")
        self.level = level

    def _enabled(self, event_level: str) -> bool:
        wanted = {"info": 20, "verbose": 15, "debug": 10}[event_level]
        return wanted >= LEVELS[self.level]

    def _clean(self, value: Any) -> Any:
        if isinstance(value, str):
            text = scrub(value)
            for secret in self.secrets:
                if len(secret) >= 4:
                    text = text.replace(secret, REDACTED)
            return text
        if isinstance(value, dict):
            return {k: (REDACTED if _SECRET_FIELD.search(str(k)) else self._clean(v)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._clean(v) for v in value]
        return value

    def log(self, event_level: str, category: str, message: str, **fields: Any) -> dict[str, Any] | None:
        if not self._enabled(event_level):
            return None
        record = {"time": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
                  "level": event_level, "category": category, "message": self._clean(message),
                  **{k: self._clean(v) for k, v in fields.items()}}
        if any(_SECRET_FIELD.search(k) for k in fields):
            for k in list(fields):
                if _SECRET_FIELD.search(k):
                    record[k] = REDACTED
        self.buffer.append(record)
        if self.sink:
            self.sink(json.dumps(record))
        return record

    def info(self, category: str, message: str, **fields: Any): return self.log("info", category, message, **fields)
    def verbose(self, category: str, message: str, **fields: Any): return self.log("verbose", category, message, **fields)
    def debug(self, category: str, message: str, **fields: Any): return self.log("debug", category, message, **fields)

    def recent(self, limit: int = 200, category: str = "") -> list[dict[str, Any]]:
        items = [r for r in self.buffer if not category or r["category"] == category]
        return items[-limit:]


def stderr_sink(line: str) -> None:
    print(line, file=sys.stderr)
