# ARC Chat v0.3.0 preview

v0.3.0 is the first ARC Chat preview that treats the prototype as a small research-computing platform rather than only a browser/Jupyter bridge. The default Student Mode remains intentionally narrow; the new scheduler and model-service controls live in Advanced Mode.

## Reliability and recovery

- Adds a versioned local command protocol with request IDs, bounded replay caching, and duplicate/in-flight request protection. Reconnecting the UI can resend the same outstanding request without submitting the underlying mutation twice.
- Adds structured error codes and recovery guidance while preserving recoverable ARC/Jupyter state instead of collapsing routine client mistakes into a generic fatal state.
- Persists non-secret recovery metadata and can opportunistically reattach a still-running prior Jupyter session/notebook after helper restart. Recovery never executes notebook cells or automatically replays Python.
- Bounds model context conservatively and clips oversized tool text so long sessions cannot grow prompt payloads without limit.
- Expands diagnostics with OpenSSH availability, model capability information, and a redacted recent state-transition trail.

## Advanced ARC platform preview

- Adds validated Slurm job specifications and explicit preview/submit/status/list/log/cancel controls over the documented Falcon SSH path. ARC Chat uses `BatchMode=yes`; it does not collect SSH passwords or Duo prompts and does not run the reviewed compute command directly on a login node.
- Adds reviewed vLLM service jobs for models under ARC's `/common/data/models/` tree, generated per-service API keys, service-state tracking, and explicit localhost SSH tunneling. The managed provider cannot be used unless the tracked tunnel process is alive.
- Adds model catalog metadata/discovery and first-class artifact/provenance records for future pipeline work.
- Keeps all of these controls behind Advanced Mode; the built-in FL 2744 Student profile remains provider/resource constrained.

## Packaging and reproducibility

- Runtime, build, and integration-test dependencies are pinned for reproducible preview builds.
- Windows and macOS self-contained packages bundle the compatible headful Chromium runtime used for visible VT authentication. Linux remains a bootstrap/advanced path in this preview.
- Lightweight Windows/Linux bootstrap bundles and the macOS bootstrap explicitly include every runtime module introduced in v0.3.0.
- Classroom-sized Windows/macOS/Linux bootstrap downloads and the source archive are mirrored on the `release-assets-v0.3.0` Git branch with SHA-256 sidecars. This path uses ordinary Git transport and remains independent of GitHub Release asset-upload availability; the larger self-contained packages remain CI artifacts because they exceed GitHub's normal Git object limit.
- Release/CI checks cover Python 3.10, 3.12, and 3.14 across Windows, macOS, and Ubuntu, plus local Jupyter integration and package-content/smoke gates where applicable.

## Validation completed before release

- 74 local unit/stress tests pass, including reconnect/idempotency, stale/multiple UI tabs, endpoint and upload hardening, recovery metadata, context bounding, Slurm/vLLM contracts, and secret redaction.
- The local real-Jupyter integration test passes end to end: browser-cookie proxy authentication, kernel creation, persistent variables, stdin, errors, HTML output, notebook validation, upload, browser UI execution, and shutdown.
- A freshly rebuilt Windows self-contained package passes the portable smoke test with bundled headful Chromium.
- Windows and Linux bootstrap archives were rebuilt and checked for the new runtime modules.

## Known boundary

The authenticated ARC/OOD selectors and the new SSH/Slurm/vLLM path still require a live acceptance test with an authorized Virginia Tech ARC account. Synthetic tests verify command construction, validation, state handling, and failure behavior, but v0.3.0 should not describe those Advanced controls as classroom-supported until that live acceptance pass is completed.

No remote notebook, Jupyter filesystem, or ARC allocation migration is performed by this release. Rolling back to v0.2.4 does not require data conversion.
