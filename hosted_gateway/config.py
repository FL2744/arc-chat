"""Environment-backed configuration for the hosted gateway."""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,80}$")


def _https_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("Allowed browser origins must be HTTPS origins without paths or credentials.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Allowed browser origin has an invalid port.") from exc
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    authority = hostname if port in (None, 443) else f"{hostname}:{port}"
    return f"https://{authority}"


@dataclass(frozen=True)
class GatewayConfig:
    database_url: str
    allowed_origins: tuple[str, ...]
    trusted_proxy_networks: tuple[ipaddress._BaseNetwork, ...]
    identity_hmac_key: bytes
    csrf_hmac_key: bytes
    jupyterlite_url: str = ""
    host: str = "0.0.0.0"
    port: int = 8080
    environment: str = "production"
    auth_user_header: str = "X-Auth-Request-User"
    auth_email_header: str = "X-Auth-Request-Email"
    auth_groups_header: str = "X-Auth-Request-Groups"
    max_auth_groups: int = 1000
    read_header_identity_in_development: bool = False
    max_request_bytes: int = 65536
    rate_limit_per_minute: int = 120

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "GatewayConfig":
        env = os.environ if env is None else env
        database_url = env.get("DATABASE_URL", "").strip()
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL must use PostgreSQL for the hosted gateway.")
        origins = tuple(dict.fromkeys(
            _https_origin(item) for item in env.get("STATIC_ALLOWED_ORIGINS", "").split(",") if item.strip()
        ))
        if not origins:
            raise ValueError("Configure at least one HTTPS STATIC_ALLOWED_ORIGINS entry.")
        trusted: list[ipaddress._BaseNetwork] = []
        for item in env.get("TRUSTED_AUTH_PROXY_CIDRS", "127.0.0.1/32,::1/128").split(","):
            if item.strip():
                trusted.append(ipaddress.ip_network(item.strip(), strict=False))
        if not trusted:
            raise ValueError("TRUSTED_AUTH_PROXY_CIDRS must identify the auth proxy network.")
        identity_key = env.get("IDENTITY_HMAC_KEY", "").encode("utf-8")
        csrf_key = env.get("CSRF_HMAC_KEY", "").encode("utf-8")
        if len(identity_key) < 32 or len(csrf_key) < 32:
            raise ValueError("IDENTITY_HMAC_KEY and CSRF_HMAC_KEY must each contain at least 32 bytes.")
        notebook = env.get("JUPYTERLITE_URL", "").strip()
        if notebook:
            parsed = urlsplit(notebook)
            try:
                parsed.port
            except ValueError as exc:
                raise ValueError("JUPYTERLITE_URL has an invalid port.") from exc
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None
                    or parsed.query or parsed.fragment):
                raise ValueError("JUPYTERLITE_URL must be an HTTPS URL without credentials, query, or fragment.")
        try:
            port = int(env.get("PORT", "8080"))
            body_limit = int(env.get("MAX_REQUEST_BYTES", "65536"))
            rate_limit = int(env.get("RATE_LIMIT_PER_MINUTE", "120"))
        except ValueError as exc:
            raise ValueError("PORT, MAX_REQUEST_BYTES, and RATE_LIMIT_PER_MINUTE must be integers.") from exc
        if not 1 <= port <= 65535 or not 1024 <= body_limit <= 1_048_576 or not 1 <= rate_limit <= 10000:
            raise ValueError("Gateway port, request size, or rate limit is outside supported bounds.")
        auth_headers = (
            env.get("AUTH_USER_HEADER", "X-Auth-Request-User").strip(),
            env.get("AUTH_EMAIL_HEADER", "X-Auth-Request-Email").strip(),
            env.get("AUTH_GROUPS_HEADER", "X-Auth-Request-Groups").strip(),
        )
        if any(not _HEADER_NAME.fullmatch(name) for name in auth_headers) or len({name.casefold() for name in auth_headers}) != 3:
            raise ValueError("Authentication claim header names must be distinct HTTP header names.")
        return cls(
            database_url=database_url,
            allowed_origins=origins,
            trusted_proxy_networks=tuple(trusted),
            identity_hmac_key=identity_key,
            csrf_hmac_key=csrf_key,
            jupyterlite_url=notebook,
            host=env.get("HOST", "0.0.0.0"),
            port=port,
            environment=env.get("APP_ENV", "production").strip().lower(),
            auth_user_header=auth_headers[0],
            auth_email_header=auth_headers[1],
            auth_groups_header=auth_headers[2],
            read_header_identity_in_development=env.get("DEV_TRUST_IDENTITY_HEADERS", "false").lower() == "true",
            max_request_bytes=body_limit,
            rate_limit_per_minute=rate_limit,
        )
