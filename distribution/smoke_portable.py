"""Smoke-test a packaged ARC Chat directory without requiring ARC credentials."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def executable(package: Path) -> Path:
    if package.suffix == ".app":
        mac = package / "Contents" / "MacOS" / "ARC-Chat"
        if mac.exists():
            return mac
    windows = package / "ARC-Chat.exe"
    if windows.exists():
        return windows
    unix = package / "ARC-Chat"
    if unix.exists():
        return unix
    raise RuntimeError(f"ARC Chat executable not found in {package}")


def assert_browser_is_bundled(package: Path) -> None:
    roots = list(package.rglob("playwright-browsers"))
    if not roots:
        raise RuntimeError("Packaged Playwright browser directory is missing")
    chromium = [p for root in roots for p in root.iterdir() if p.name.startswith("chromium-")]
    if not chromium:
        raise RuntimeError("Packaged headful Chromium runtime is missing")
    if any(p.name.startswith("chromium_headless_shell-") for root in roots for p in root.iterdir()):
        raise RuntimeError("Unneeded Chromium headless shell was bundled")


def smoke(package: Path) -> None:
    assert_browser_is_bundled(package)
    port = free_port()
    with tempfile.TemporaryDirectory() as temporary:
        state = Path(temporary) / "session.json"
        env = os.environ.copy()
        env.update({
            "ARC_CHAT_PORT": str(port),
            "ARC_CHAT_NO_OPEN": "1",
            "ARC_CHAT_BROWSER_SMOKE": "1",
            "ARC_CHAT_STATE": str(state),
        })
        process = subprocess.Popen(
            [str(executable(package))],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 30
            body = b""
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Packaged ARC Chat exited early with code {process.returncode}")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                        body = response.read(65536)
                    break
                except OSError:
                    time.sleep(0.2)
            else:
                raise RuntimeError("Packaged ARC Chat did not become ready within 30 seconds")
            if b'id="main-content"' not in body or b'id="status"' not in body:
                raise RuntimeError("Packaged ARC Chat served unexpected UI content")
            if not state.exists():
                raise RuntimeError("Packaged ARC Chat did not persist its non-secret launch state")
            saved = json.loads(state.read_text(encoding="utf-8"))
            if not saved.get("url", "").startswith(f"http://127.0.0.1:{port}/#"):
                raise RuntimeError("Packaged ARC Chat persisted an invalid launch URL")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    smoke(args.package.resolve())
    print("portable smoke test passed")
