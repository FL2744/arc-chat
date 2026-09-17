"""Build small cross-platform ARC Chat bootstrap bundles."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
COMMON = (
    "helper.py", "arc-chat.html", "requirements.txt", "config.py", "state.py",
    "model_providers.py", "diagnostics.py", "ood.py", "workspace.py",
    "protocol.py", "security.py", "context_window.py", "artifacts.py",
    "jobs.py", "services.py", "integration.py", "errors.py", "version.py",
    "profiles.example.json", "README.md", "SUPPORT.md", "SECURITY.md",
)


def write_windows_launcher(path: Path) -> None:
    path.write_text(
        '@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\n'
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"\r\n'
        'if errorlevel 1 (echo. & echo ARC Chat stopped with an error. & pause)\r\n',
        encoding="utf-8",
    )


def write_linux_launcher(path: Path) -> None:
    path.write_text('#!/bin/sh\nset -eu\ncd "$(dirname "$0")"\nexec ./start.command\n', encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def zip_tree(source: Path, target: Path) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file in sorted(source.rglob("*")):
            if not file.is_file():
                continue
            relative = file.relative_to(source.parent)
            info = zipfile.ZipInfo.from_file(file, relative.as_posix())
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, file.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def checksum(path: Path) -> Path:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    output = path.with_suffix(path.suffix + ".sha256")
    output.write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return output


def build(platform: str) -> tuple[Path, Path]:
    DIST.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / f"ARC-Chat-{platform}"
        root.mkdir()
        for name in COMMON:
            shutil.copy2(ROOT / name, root / name)
        if platform == "Windows":
            shutil.copy2(ROOT / "start.ps1", root / "start.ps1")
            write_windows_launcher(root / "ARC Chat.cmd")
        elif platform == "Linux":
            shutil.copy2(ROOT / "start.command", root / "start.command")
            os.chmod(root / "start.command", 0o755)
            write_linux_launcher(root / "arc-chat")
        else:
            raise ValueError(platform)
        archive = DIST / f"ARC-Chat-{platform}.zip"
        zip_tree(root, archive)
    return archive, checksum(archive)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("platform", nargs="*", choices=("Windows", "Linux"))
    args = parser.parse_args()
    targets = args.platform or ["Windows", "Linux"]
    for target in targets:
        archive, sha = build(target)
        print(archive)
        print(sha)
