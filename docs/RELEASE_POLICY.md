# Release and deprecation policy

ARC Chat is currently a preview project.

## Versioning

- Application releases use `vMAJOR.MINOR.PATCH` Git tags.
- `version.py` is the single source of application/build identifiers.
- Course-profile files have an explicit schema version; unsupported future schema versions fail closed.
- The local integration API is path-versioned (`/api/v1`). Backward-incompatible API contract changes must use a new path version rather than silently redefining v1.

## Release gates

A tagged preview should not be treated as complete until:

1. the Python/unit/human-stress matrix passes on the supported OS/Python combinations;
2. the real local Jupyter integration test passes;
3. Windows and macOS self-contained package builds/smokes pass in CI;
4. source/bootstrap package checks pass;
5. checksums are produced for published classroom downloads;
6. the specific CI/release workflow runs are verified green.

## Published artifacts

Lightweight classroom downloads may be mirrored through an ordinary Git release-assets branch with SHA-256 sidecars when release-service upload reliability is poor. Large self-contained browser-bundled packages may remain CI artifacts when they exceed normal Git object limits.

## Compatibility and deprecation

- Recovery metadata readers should remain backward-compatible with at least the immediately preceding schema when practical.
- `/api/v1` remains stable while it exists; deprecated fields/actions should be documented before removal.
- Course-profile schema changes should either preserve the previous schema or fail with an actionable version error.
- Preview releases may remove experimental Advanced-Mode behavior, but release notes must identify the migration/rollback effect.

## Institutional release

Preview artifacts are not equivalent to a Virginia Tech managed distribution. Production/institutional distribution additionally requires the appropriate signing/notarization credentials, support ownership, data/security review, and licensing/copyright decision.

