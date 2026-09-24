"""Syntax-check the inline static student client using the installed Node.js."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required for the Student Web JavaScript syntax check.")
    source = (ROOT / "web" / "student" / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?:\s[^>]*)?>([\s\S]*?)</script\s*>", source, flags=re.IGNORECASE)
    if len(scripts) != 1 or not scripts[0].strip():
        raise SystemExit("Student Web page must contain exactly one inline application script.")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".js", delete=False) as temporary:
        temporary.write(scripts[0])
        path = Path(temporary.name)
    try:
        subprocess.run([node, "--check", str(path)], check=True)
    finally:
        path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
