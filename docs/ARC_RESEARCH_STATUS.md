# ARC Research: requirements status

ARC Research is the new name for ARC Chat. This file maps each section of the technical product
requirements to the code that implements it, and lists what still needs a real ARC account to validate.

## Rename (section 3)
- Done: product name in the UI, docs, bundle/portable/installer names (`ARC-Research-*`), macOS app name,
  job-name prefixes (`arc-research-*`), error text and About/bug-report metadata.
- Compatibility kept on purpose: `ARC_CHAT_*` environment variables still work (`ARC_RESEARCH_*` wins, see
  `envcompat.py`); local state directories keep their legacy `ARC Chat` / `arc-chat` names so existing recovery
  files are found; `arc-chat.html` and the `CFBundleIdentifier` are unchanged; the README download links still point
  at the existing `release-assets-v0.4.1` files, which keep their old `ARC-Chat-*` names until the next release.
- Needs a maintainer: rename the GitHub repository (Settings, General), then update README links.

## Requirement map
| Section | Implementation |
|---|---|
| 5.1, 5.2 Connection setup, SSH keys, username validation | `sshkeys.py`, `connection.py`; UI: Connect tab |
| 5.3 Local dev credentials | `.env.example`, `.gitignore`, `security.py` redaction |
| 6 Session lifecycle / context | `ResearchService.context()`, context bar (authenticated, allocation, login vs compute, workload) |
| 7 Resource discovery | `discovery.py` (sinfo, squeue, sacctmgr parsers), UI: Connect, Resources & availability |
| 8 Resource selection | `discovery.recommend()` (idle nodes plus memory fit, reasons shown, no start-time prediction) |
| 9 Login-node safety | `safety.py`, `terminal.py` (heavy commands never run on the login node; redirected to compute jobs) |
| 10 Workload specification | `workload.py`; UI: Workload tab (all fields, JSON direct edit with validation) |
| 11 Job naming | `naming.py` (sanitize, dedupe, lock, auto-rename with context) |
| 12 vLLM | `services.py` (TP size, quantization, GPU utilization, extra args, env vars; health check; logs), UI: Models tab |
| 13 Model catalog | `catalog.py` (discovery from ARC's model directory, search/filters/favorites/recents, memory guidance, warnings) |
| 14 External providers | existing `model_providers.py`; the UI now states where inference ran |
| 15 Hybrid workflows | `planner.py`: a model proposes commands, which go through visible review; none runs invisibly |
| 16 ARC applications | existing `apps.py` manifests (discoverable, not hardcoded in the UI) |
| 17 Unified workspace | sidebar tabs plus the chat in one window |
| 18 Terminal / command surface | `terminal.py` and Terminal tab (stdout/stderr separated, ANSI, history, cancel, login/compute context) |
| 19 Output rendering | Markdown, tables, lists, headings, code with highlighting, copy, collapse, raw toggle (`arc-chat.html`) |
| 20 Advanced mode | Workload tab sections map to the ARC workload structure |
| 21 UI robustness | overflow, clipped-field and focus-ring fixes; verified in headless Chromium at phone, tablet, laptop and desktop widths |
| 22 Errors | `errors.py`, `sshkeys.translate_ssh_failure`, staged connection errors with expandable details |
| 23 Bug reporting | `bugreport.py` and Report a problem panel (user removes fields, nothing sent automatically) |
| 24 Run history | `runs.py` (persisted; duplicate, modify, rerun, export) |
| 25 Projects | notes and saved configurations (`project_extras.py`); runs, jobs, endpoints via the existing project registry |
| 26 Security | secrets are never attached to reports or logs; env vars that look like credentials are rejected; LLM commands require confirmation |
| 27 Logging | `applog.py` (normal/verbose/debug, redacted, viewable in the UI) |

## Not validated (needs a real ARC account)
- The Slurm queries in `discovery.py`, the model inventory command in `catalog.py`, and the connection checks have
  unit tests against fake gateways only. They are standard read-only commands, but they have not been run against ARC.
  Confirm the exact commands and output formats with current ARC documentation.
- Accepted SSH key algorithms should be confirmed against ARC documentation (ed25519 is the default, rsa-4096 optional).
- Per-GPU memory figures in `catalog.NOMINAL_GPU_MEMORY_GB` are nominal card sizes, not ARC-verified variants.
- Key generation was not exercised where `ssh-keygen` is unavailable; its test skips there.
- Not built or smoke-tested here: the packaged Windows/macOS apps. CI builds them.

## Known gaps
- No embedded interactive PTY: the terminal is a structured review-and-run surface. Long-running or interactive
  programs belong in compute jobs.
- ARC applications other than Jupyter and vLLM have launch-plan support only through the existing manifests.
