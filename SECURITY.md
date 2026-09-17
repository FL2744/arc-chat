# ARC Chat security policy

ARC Chat is a local helper that bridges a user-controlled browser session, Virginia Tech ARC resources, Jupyter, and explicitly selected model providers. Security changes should preserve the invariants in `docs/TECHNICAL_PLAN.md`.

## Reporting a vulnerability

Do not place API keys, VT credentials, browser cookies, private notebook contents, allocation identifiers that should remain private, or other sensitive data in a public issue.

For an ARC Chat application vulnerability, use the repository's private vulnerability-reporting mechanism when it is available. If private reporting is not available, contact the repository maintainers through an existing private Virginia Tech/project channel and include only the minimum information needed to reproduce the problem.

For Virginia Tech account, Duo, VPN, Open OnDemand, cluster, scheduler, allocation, or ARC service incidents, use the official Virginia Tech/ARC support channels described in `SUPPORT.md`. ARC Chat maintainers should not request or receive a user's VT password or Duo code.

## Supported security boundary

- The local HTTP/WebSocket service binds to `127.0.0.1` only.
- Non-root local routes require a high-entropy per-process token and enforce same-origin checks when an Origin header is present.
- VT login/MFA happens in a visible Playwright browser; ARC Chat does not collect those credentials.
- Model API keys and managed-vLLM keys are held in process memory and are redacted from diagnostics/errors.
- Recovery metadata intentionally excludes keys, cookies, chat text, notebook contents, raw Slurm commands, and Slurm stdout.
- External `/api/v1` clients may inspect non-secret status/jobs/artifacts and submit review proposals. They cannot execute Python, submit/cancel jobs, or start/stop model services.
- Custom model providers require public HTTPS endpoints. Dedicated ARC endpoints must remain under `arc.vt.edu`; managed vLLM is reachable only through a tracked loopback SSH tunnel.
- HTML notebook output is rendered in a sandboxed iframe with scripts/network disabled.
- Every code/resource mutation remains explicitly human-approved.

See `docs/SECURITY_AND_DATA.md` for data-flow and retention details.

