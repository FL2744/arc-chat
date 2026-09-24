"""Build the static Student Web bundle for VT Domains-style hosting."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = ROOT / "web" / "student"
DEFAULT_DIST = ROOT / "dist"
FILES = ("index.html", "config.json", "README.md")
SECRET_KEY_RE = re.compile(r"(api[_-]?key|password|secret|token|cookie|credential)", re.I)
FORBIDDEN_PUBLIC_RUNTIME_RE = re.compile(r"(?:127\.0\.0\.1|localhost|ws://|wss://127\.0\.0\.1|/ws\?token=)", re.I)


def _https_url(value: str, label: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} has an invalid port.") from exc
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or (label == "notebook_url" and (parsed.query or parsed.fragment))):
        raise ValueError(f"{label} must be a public HTTPS URL without embedded credentials.")
    return value


def _https_origin(value: str, label: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError(f"{label} must be an HTTPS origin without a path or credentials.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} has an invalid port.") from exc
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    authority = hostname if port in (None, 443) else f"{hostname}:{port}"
    return f"https://{authority}"


def validate_config(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Student web configuration must be an object.")
    rendered = json.dumps(value, sort_keys=True)
    if SECRET_KEY_RE.search(rendered):
        # Public labels such as "No API key required" still risk normalizing
        # credential concepts into a static config. Keep the contract stricter.
        raise ValueError("Student web configuration must not contain credential-like keys or text.")
    version = value.get("version")
    if version != 1:
        raise ValueError("Student web configuration version must be 1.")
    title = str(value.get("title") or "").strip()
    if not title or len(title) > 120:
        raise ValueError("Student web title must be 1-120 characters.")
    notebook_url = _https_url(str(value.get("notebook_url") or ""), "notebook_url")
    gateway_url = _https_origin(str(value.get("gateway_url") or ""), "gateway_url")
    apps = value.get("applications")
    if not isinstance(apps, list):
        raise ValueError("applications must be a list.")
    ids: set[str] = set()
    for app in apps:
        if not isinstance(app, dict):
            raise ValueError("Each application must be an object.")
        app_id = str(app.get("id") or "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", app_id):
            raise ValueError("Invalid application id.")
        if app_id in ids:
            raise ValueError(f"Duplicate application id: {app_id}")
        ids.add(app_id)
        if app.get("enabled", True) is not False and app.get("view") != "notebook":
            _https_url(str(app.get("url") or ""), f"application {app_id} URL")
    if any(app.get("view") == "notebook" and app.get("enabled", True) is not False for app in apps) and not notebook_url:
        raise ValueError("Enabled notebook application requires notebook_url.")
    value["gateway_url"] = gateway_url
    return value


def _index_with_gateway_csp(index_html: str, gateway_url: str) -> str:
    marker = "__GATEWAY_ORIGIN__"
    if index_html.count(marker) != 1:
        raise ValueError("Student index must include exactly one gateway CSP marker.")
    origin = _https_origin(gateway_url, "gateway_url") or "'none'"
    return index_html.replace(marker, origin)


def build_archive(output_dir: Path = DEFAULT_DIST) -> tuple[Path, Path]:
    config_path = WEB_ROOT / "config.json"
    config = validate_config(json.loads(config_path.read_text(encoding="utf-8")))
    output_dir.mkdir(parents=True, exist_ok=True)
    stage = output_dir / "student-web"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    for name in FILES:
        source = WEB_ROOT / name
        if not source.is_file():
            raise FileNotFoundError(source)
        if name in {"index.html", "config.json"}:
            public_text = source.read_text(encoding="utf-8")
            if FORBIDDEN_PUBLIC_RUNTIME_RE.search(public_text):
                raise ValueError(f"{name} references the private loopback/runtime control surface.")
            if name == "index.html":
                public_text = _index_with_gateway_csp(public_text, config.get("gateway_url", ""))
                (stage / name).write_text(public_text, encoding="utf-8")
                continue
        shutil.copy2(source, stage / name)

    # Re-serialize validated JSON for deterministic public deployment output.
    (stage / "config.json").write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    archive = output_dir / "ARC-Chat-Student-Web.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name in FILES:
            bundle.write(stage / name, arcname=name)

    checksum = output_dir / (archive.name + ".sha256")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return archive, checksum


def main() -> int:
    output = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_DIST
    archive, checksum = build_archive(output)
    print(archive)
    print(checksum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
