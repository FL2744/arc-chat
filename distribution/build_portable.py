"""Build a self-contained ARC Chat package for the current operating system.

The release workflow installs Playwright Chromium into a known external browser
directory before calling this script.  We copy only the headful Chromium and
FFmpeg runtime into Playwright's bundled ``.local-browsers`` location; the
separate Chromium headless shell is intentionally omitted because ARC Chat's
VT/OOD authentication flow must remain visible to the user.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess

import PyInstaller.__main__
import playwright


ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "portable"


def browser_root() -> Path:
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured and configured != "0":
        root = Path(configured).expanduser().resolve()
    else:
        root = Path(playwright.__file__).resolve().parent / "driver" / "package" / ".local-browsers"
    if not root.is_dir():
        raise RuntimeError(
            "Playwright browser runtime is missing. Install it before packaging, for example: "
            "python -m playwright install chromium"
        )
    return root


def browser_data_args(root: Path) -> list[str]:
    selected = [
        child for child in root.iterdir()
        if child.name.startswith("chromium-") or child.name.startswith("ffmpeg-")
    ]
    if not any(child.name.startswith("chromium-") for child in selected):
        raise RuntimeError(f"No headful Chromium runtime found under {root}")
    args: list[str] = []
    destination = "playwright/driver/package/.local-browsers"
    for child in sorted(selected):
        args.append(f"--add-data={child}:{destination}/{child.name}")
    return args


def archive_name() -> str:
    system = platform.system()
    if system == "Windows":
        return "ARC-Chat-Windows-Portable"
    if system == "Darwin":
        return "ARC-Chat-macOS-Portable"
    return "ARC-Chat-Linux-Portable"


def checksum(path: Path) -> Path:
    output = path.with_suffix(path.suffix + ".sha256")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    output.write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return output


def clean_previous_package() -> None:
    for candidate in (DIST / "ARC-Chat", DIST / "ARC-Chat.app"):
        if not candidate.exists():
            continue
        try:
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink()
        except PermissionError as exc:
            raise RuntimeError(
                f"Cannot replace {candidate} because a packaged ARC Chat/Chromium process is still using it. "
                "Quit the packaged app and close its visible ARC browser before rebuilding."
            ) from exc


def build() -> tuple[Path, Path, Path]:
    root = browser_root()
    DIST.mkdir(parents=True, exist_ok=True)
    clean_previous_package()
    args = [
        str(ROOT / "helper.py"),
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name=ARC-Chat",
        f"--add-data={ROOT / 'arc-chat.html'}:.",
        "--collect-all=playwright",
        f"--runtime-hook={ROOT / 'distribution' / 'pyinstaller_runtime.py'}",
        f"--distpath={DIST}",
        f"--workpath={ROOT / 'build' / 'portable'}",
        f"--specpath={ROOT / 'build' / 'portable-spec'}",
        *browser_data_args(root),
    ]
    if platform.system() in {"Windows", "Darwin"}:
        args.append("--windowed")
    PyInstaller.__main__.run(args)

    package = DIST / "ARC-Chat"
    if platform.system() == "Darwin" and (DIST / "ARC-Chat.app").exists():
        package = DIST / "ARC-Chat.app"
    if not package.exists():
        raise RuntimeError(f"PyInstaller did not create the expected package: {package}")

    archive_base = DIST / archive_name()
    archive = archive_base.with_suffix(".zip")
    if platform.system() == "Darwin":
        # Preserve app-bundle symlinks, executable bits, and resource metadata.
        archive.unlink(missing_ok=True)
        subprocess.run(
            ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(package), str(archive)],
            check=True,
        )
    else:
        archive = Path(shutil.make_archive(str(archive_base), "zip", package.parent, package.name))
    return package, archive, checksum(archive)


if __name__ == "__main__":
    package, archive, sha = build()
    print(package)
    print(archive)
    print(sha)
