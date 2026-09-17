"""OpenAI-compatible model providers and endpoint/retry policy."""

from __future__ import annotations

import asyncio
import ipaddress
import random
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Mapping
from urllib.parse import urlsplit

from aiohttp import ClientTimeout


ARC_ENDPOINT = "https://llm-api.arc.vt.edu/api/v1"
OPENAI_ENDPOINT = "https://api.openai.com/v1"


class EndpointPolicy:
    @staticmethod
    def validate(endpoint: str, *, allow_custom: bool = True) -> str:
        value = endpoint.rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme != "https":
            raise ValueError("Model API URL must use HTTPS.")
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Model API URL must contain a public HTTPS host without embedded credentials.")
        host = parsed.hostname.lower().rstrip(".")
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            raise ValueError("Model API URL cannot target a local host.")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast):
            raise ValueError("Model API URL cannot target a private or local IP address.")
        if not allow_custom and value not in {ARC_ENDPOINT, OPENAI_ENDPOINT}:
            raise ValueError("This course profile only permits the configured model provider.")
        return value


@dataclass(frozen=True)
class ModelCatalogEntry:
    id: str
    provider: str
    capabilities: tuple[str, ...] = ("tool_calling",)


FALLBACK_CATALOG = (
    ModelCatalogEntry("gpt-oss-120b", "arc_shared"),
    ModelCatalogEntry("DeepSeek-V4.1-Flash", "arc_shared"),
    ModelCatalogEntry("GLM-5.3", "arc_shared"),
    ModelCatalogEntry("Kimi-K3", "arc_shared"),
)


class ModelCatalog:
    def __init__(self, entries: tuple[ModelCatalogEntry, ...] = FALLBACK_CATALOG):
        self.entries = entries

    def ids(self, provider: str | None = None) -> list[str]:
        return [entry.id for entry in self.entries if provider is None or entry.provider == provider]


class OpenAICompatibleProvider:
    """Transport for an OpenAI-compatible chat-completions endpoint.

    Retries are limited to connection failures, HTTP 429, and transient 5xx
    responses. A request is never retried after a successful response, so a
    model tool call cannot be duplicated by this layer.
    """

    def __init__(self, http, endpoint: str, key: str, *, max_attempts: int = 3, sleep=asyncio.sleep):
        self.http = http
        self.endpoint = EndpointPolicy.validate(endpoint)
        self.key = key
        self.max_attempts = max(1, max_attempts)
        self.sleep = sleep

    async def complete(self, body: Mapping[str, Any]) -> dict[str, Any]:
        delay = 0.75
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                async with self.http.post(
                    self.endpoint + "/chat/completions",
                    json=dict(body),
                    headers={"Authorization": "Bearer " + self.key},
                    timeout=ClientTimeout(total=180),
                    allow_redirects=False,
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    text = (await response.text())[:500]
                    if response.status not in {429, 500, 502, 503, 504} or attempt + 1 >= self.max_attempts:
                        raise RuntimeError(f"Model API HTTP {response.status}: {text}")
                    retry_after = self._retry_after(getattr(response, "headers", {}))
                    await self.sleep(min(30.0, retry_after if retry_after is not None else delay + random.uniform(0, 0.25)))
                    delay = min(30.0, delay * 2)
            except RuntimeError:
                raise
            except (OSError, asyncio.TimeoutError) as exc:
                last_error = exc
                if attempt + 1 >= self.max_attempts:
                    raise RuntimeError("Model API is temporarily unreachable after bounded retries.") from exc
                await self.sleep(min(30.0, delay + random.uniform(0, 0.25)))
                delay = min(30.0, delay * 2)
        raise RuntimeError("Model API request failed.") from last_error

    @staticmethod
    def _retry_after(headers: Mapping[str, str]) -> float | None:
        value = headers.get("Retry-After") or headers.get("retry-after")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                return max(0.0, (parsedate_to_datetime(value).timestamp() - __import__("time").time()))
            except (TypeError, ValueError, OverflowError):
                return None


class ArcSharedModelProvider(OpenAICompatibleProvider):
    def __init__(self, http, key: str, endpoint: str = ARC_ENDPOINT, **kwargs):
        super().__init__(http, endpoint, key, **kwargs)


class OpenAIModelProvider(OpenAICompatibleProvider):
    def __init__(self, http, key: str, endpoint: str = OPENAI_ENDPOINT, **kwargs):
        super().__init__(http, endpoint, key, **kwargs)


class CustomCompatibleProvider(OpenAICompatibleProvider):
    pass


def build_provider(http, provider: str, endpoint: str, key: str) -> OpenAICompatibleProvider:
    if provider == "arc":
        return ArcSharedModelProvider(http, key, endpoint)
    if provider == "openai":
        return OpenAIModelProvider(http, key, endpoint)
    if provider == "custom":
        return CustomCompatibleProvider(http, endpoint, key)
    raise ValueError(f"Unsupported model provider: {provider}")
