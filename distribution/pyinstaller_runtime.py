"""Runtime settings for frozen ARC Chat builds."""

import os
from pathlib import Path
import sys


def bundled_browsers() -> Path:
    executable = Path(sys.executable).resolve()
    if sys.platform == "darwin" and executable.parent.name == "MacOS":
        return executable.parent.parent / "Resources" / "playwright-browsers"
    return executable.parent / "playwright-browsers"


browser_root = bundled_browsers()
if browser_root.is_dir():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_root)
