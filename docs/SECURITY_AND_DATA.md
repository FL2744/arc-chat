# Security and data boundary

This document records the intended data flows and local retention policy for ARC Chat.

## Authentication and credentials

VT login and MFA occur only in the visible Playwright browser opened by ARC Chat. The helper uses the resulting browser session for the user's authorized Open OnDemand/Jupyter requests; it does not ask the user to type a VT password or Duo code into ARC Chat.

Model API keys are entered into ARC Chat only when needed and are retained in process memory. Managed vLLM creates a per-service API key in memory. Dedicated OOD LLM sessions use the unique API key supplied by that session. These keys are not written to recovery metadata, diagnostic reports, notebooks, or transcripts by ARC Chat.

## Provider/data boundary

- **ARC shared hosted model**: prompts/tool results are sent to the selected Virginia Tech ARC model endpoint.
- **ARC dedicated OOD LLM**: prompts/tool results are sent to the selected session's ARC-hosted HTTPS API endpoint.
- **Managed ARC vLLM**: prompts/tool results travel through a tracked localhost SSH tunnel to the user's ARC compute-node service.
- **External/custom provider**: prompts/tool results leave Virginia Tech for the user-selected public HTTPS endpoint. Advanced Mode makes that choice explicit.

ARC Chat does not automatically send arbitrary user files to a model. Tool outputs included in chat context can contain research data, so users must review what Python prints/returns before continuing to a provider.

## Remote data

Python cells and supported outputs are persisted to the user's Jupyter notebook on the active remote workspace. Uploaded/downloaded/generated files remain subject to the user's ARC/Jupyter storage permissions and retention rules. ARC Chat does not create a separate cloud copy.

## Local retained metadata

ARC Chat may retain a small recovery file containing:

- app/build/profile identifiers;
- workspace/Jupyter base and notebook/session identifiers;
- Slurm job IDs and non-secret resource/state metadata;
- SHA-256 fingerprints of submitted raw job commands, not the raw commands;
- artifact provenance metadata (path, media type, creator/job identifier), not artifact contents;
- save timestamp.

The recovery file intentionally excludes API keys, browser cookies, chat text, notebook contents, Slurm stdout, raw Slurm commands, and VT credentials.

## Local logs

The lightweight macOS launcher keeps `launcher.log` under `~/Library/Application Support/ARC Chat`. It rotates the file when it exceeds 2 MiB and retains at most the current log plus one prior log. The helper itself does not intentionally log credentials, chat, notebook contents, or model keys. Terminal/bootstrap runs on other platforms primarily use the invoking console rather than a persistent ARC Chat application log.

## Diagnostics

Doctor reports contain app/platform/runtime state, sanitized transitions, configuration health, error/status classes, and explicitly requested remote health checks. They exclude credentials, cookies, chat history, notebook contents, and arbitrary file contents. A full check may create/delete one temporary remote file and execute a visible smoke-test cell after the user explicitly requests it.

## Local integration API

`/api/v1` is loopback-only and requires the same random local token as the WebSocket API. It exposes non-secret status/job/artifact metadata and a review-proposal inbox. It deliberately has no Python execution, file mutation, Slurm submit/cancel, or service start/stop route.

