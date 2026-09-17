# ARC Chat v0.2.4 preview

## What changed

- Adds self-contained Windows and macOS portable build pipelines.
- Bundles the compatible headful Playwright Chromium runtime used for visible VT login/MFA.
- Keeps the smaller macOS bootstrap and Linux bootstrap as fallback/advanced paths.
- Adds packaged-app smoke tests that launch the bundled Chromium through Playwright, start ARC Chat on loopback, serve the UI, and verify non-secret launch-state persistence.
- Adds real local Jupyter/UI integration to CI in addition to the Windows/macOS/Linux Python 3.10/3.12/3.14 unit and human-stress matrix.
- Includes the v0.2.3 human-stress hardening for duplicate UI actions, stale tabs, login/retry recovery, upload/path validation, endpoint validation, model retry behavior, and execution disconnect handling.

## Release gates exercised locally

- 57/57 unit, mock, reconnect, hardening, and human-stress tests pass.
- Real local Jupyter integration passes: proxy auth, kernel creation, persistent state, stdin, errors, notebook validation, upload, browser UI execution, and shutdown.
- Windows portable build succeeds under PyInstaller and the packaged-app/Chromium smoke test passes.
- Workflow YAML parses cleanly and `git diff --check` is clean.

## Rollback

If a portable-package regression is discovered, use the v0.2.3 preview artifacts while the issue is corrected. v0.2.4 does not migrate or rewrite remote notebooks, Jupyter files, or ARC allocations, so rollback does not require data conversion.
