"""Runtime settings for frozen ARC Chat builds."""

import os


# Playwright's supported hermetic layout lives beside its bundled driver.
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
