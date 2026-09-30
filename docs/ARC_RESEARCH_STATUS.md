# ARC Research: rename and requirements status

ARC Research is the new name for ARC Chat. This file tracks what the technical product requirements
cover today and what is still open.

## Rename
- Done: product name in UI, docs, installers' display names, error/diagnostic text and the macOS app name.
- Compatibility kept on purpose: `ARC_CHAT_*` environment variables still work (`ARC_RESEARCH_*` takes
  precedence, see `envcompat.py`); local state directories keep the legacy `ARC Chat` / `arc-chat` names so
  existing recovery files are found; `arc-chat.html`, the `ARC-Chat-*.zip` asset names and the
  `release-assets-v0.4.1` download URLs are unchanged because they depend on the GitHub repository and
  existing release assets.
- Needs a maintainer: rename the GitHub repository (Settings, General), then update README links and asset names.

## Added in this change (all unit-tested)
| Requirement | Module |
|---|---|
| 9 Login-node safety classification | `safety.py` |
| 10 Structured workload spec, export, duplicate | `workload.py` |
| 11 Automatic job naming, lock, dedupe | `naming.py` |
| 7/8 Scheduler output parsing and explainable ranking | `discovery.py` |
| 24 Persistent run records, rerun, export | `runs.py` |
| 5.1/5.2 ARC username check, SSH key generation/checks | `sshkeys.py` |
| 22 Actionable ARC username/SSH error text | `errors.py`, `sshkeys.py` |
| 23 Reviewable, redacted bug reports | `bugreport.py` |
| 5.3 Local credential template | `.env.example` |

## Not yet done
These modules are not wired into `helper.py` or the UI yet. Also open: the connection-setup UI, the discovery
UI, the vLLM launch UI, catalog search/favorites, the terminal, Markdown/table/syntax rendering, and the
overflow/clipping fixes in `arc-chat.html`.
- The Slurm queries in `discovery.py` are standard Slurm commands and must be validated against current ARC
  documentation and on a real account before release. Accepted ARC SSH key algorithms must be confirmed too.
