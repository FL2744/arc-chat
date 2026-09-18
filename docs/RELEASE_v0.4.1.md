# ARC Chat v0.4.1

Build: `2026.09.18.1`

This patch release stabilizes the classroom-facing onboarding and finalizes the v0.4 development iteration without expanding the product scope.

## Student Mode and UX

- Reworked the pre-workspace experience into a guided `ARC access -> workspace -> research` flow with one dominant action and contextual recovery controls.
- A missing course allocation is no longer treated as a fatal Student Mode error. ARC Chat opens the visible OOD form, surfaces allocations already authorized for the signed-in ARC account, and requires an explicit user selection/review before launch.
- Waiting for Falcon/Jupyter is now a normal state rather than an exception. Student Mode performs bounded readiness checks without relaunching or duplicating the job; `Check now` and `Open ARC` remain explicit controls.
- Separated `workspace ready` from `AI chat ready`: Python/files can be used as soon as Jupyter is attached, while chat clearly requests a model connection when one is still needed.
- Added progressive-disclosure Student/Advanced UX, Advanced Session/Compute/Models/Data navigation, focus mode, compact status/toast feedback, attachment chips, drag/drop and paste-file upload, modern chat keyboard behavior, and human-readable job/service/model/diagnostic/result surfaces.
- Removed mojibake-prone decorative Unicode glyphs from CSS and added regression coverage preventing UTF-8/Windows-1252 encoding artifacts from returning.

## Reliability and verification

- Added regression coverage for unconfigured course allocation fallback, safe allocation choice presentation, and non-error Jupyter waiting behavior.
- Full unit/stress suite: **92 tests pass** locally.
- Python compile gate: pass.
- JavaScript syntax gate: pass.
- `git diff --check`: pass.
- Real local Jupyter + browser integration: pass, including persistent kernel execution through the redesigned progressive-disclosure UI.
- Windows self-contained PyInstaller package: rebuilt and portable smoke test pass.
- Windows/Linux bootstrap archives: rebuilt and audited for current version/UI plus bundled contributor, security, and support documentation.
- Browser-level responsive/UI checks at desktop and 430 px mobile widths: no horizontal overflow.
- Live local helper test: the Student Mode primary action reaches the visible VT/ARC authentication flow without recreating the previous FL 2744 allocation exception.

## Attribution

ARC Chat was created by William Taggart. Alejandro Grenier is a major development contributor across the current architecture, reliability/security hardening, Windows/cross-platform packaging, Student Mode UX/onboarding, automated testing, and Advanced ARC workflows. See `CONTRIBUTORS.md` and Git history for per-commit attribution.

## Boundaries that remain external

- A complete credentialed ARC acceptance pass still requires an authorized user to finish VT login/MFA and submit a real allocation/job in the target environment.
- Apple production signing/notarization requires institutional credentials.
- Formal copyright/license ownership and institutional support/security ownership remain stakeholder decisions rather than code changes.
