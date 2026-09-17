# ARC Chat v0.4.0

Build: `2026.09.17.11`

This release moves ARC Chat from the v0.3 classroom-hardening baseline into a broader advanced/research preview while preserving the human-approval and visible-authentication boundaries.

## Major additions

- First-class Virginia Tech ARC dedicated OOD LLM provider support. ARC Chat accepts the API base URL and generated key from a user-launched dedicated OOD session and restricts that provider to Virginia Tech ARC HTTPS hosts.
- Documented Falcon GPU resource profiles for L40S, A30, V100, and T4 queues, with reviewed Slurm preview/submit/status/log/cancel controls.
- Durable, non-secret job history and artifact provenance. Recovery state stores resource metadata, command hashes, scheduler state, and artifact identifiers, not raw job commands, stdout contents, API keys, passwords, or cookies.
- Managed vLLM improvements including explicit Falcon QoS, generated API credentials, reviewed Slurm scripts, compute-node lifecycle tracking, and loopback-only SSH tunnels.
- Workspace inspector and non-secret endpoint registry for Advanced Mode.
- Versioned course-profile configuration.
- Token-protected loopback `/api/v1` integration contract for status, jobs, artifacts, and external review proposals. External applications cannot execute Python or allocate/cancel ARC resources through this API.
- Reference integration client under `examples/integration_client.py`.
- Accessibility/keyboard-focus regression checks and removal of legacy UI encoding artifacts.
- Security, data-boundary, support, contribution, governance, release/deprecation, and licensing-status documentation.
- Bounded macOS launcher-log retention.

## Verification performed locally

- Python compile gate: pass.
- Unit/stress suite: **90 tests pass**.
- Real local Jupyter + browser integration: pass, including proxy authentication, persistent kernel state, stdin, errors, HTML output, notebook validation/persistence, upload, browser UI execution, and shutdown.
- Windows/Linux bootstrap generation: pass.
- Windows self-contained PyInstaller build: pass.
- Bundled Chromium/UI portable smoke: pass.
- `git diff --check`: pass.

Cross-platform GitHub Actions remains the release gate for macOS and the full supported CI matrix. The version tag must not be cut until the candidate commit's specific CI run is green.

## Boundaries that remain external

- Live ARC login, allocation selection, scheduler submission, compute-node access, dedicated OOD LLM startup, and managed vLLM startup require acceptance testing by an authorized ARC user.
- Apple production signing/notarization requires institutional credentials; preview macOS packages remain ad-hoc signed unless those credentials are supplied.
- Copyright ownership and the final repository software license remain an explicit owner/institutional decision; see `docs/LICENSING.md`.
- Formal institutional support ownership and stakeholder security/data review remain organizational decisions rather than code changes.

