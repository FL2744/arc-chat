# Hosted Gateway Boundary

Status: architecture contract only. No hosted ARC gateway is claimed to exist yet.

## Why this boundary exists

The current ARC Chat helper is intentionally bound to loopback and rejects external Host/Origin values. That protects local browser/Jupyter authority from unrelated websites. A page hosted on VT Domains must **not** be granted direct access to the local helper merely to make Student Mode look web-native.

The browser-first architecture therefore has two independent execution paths:

1. **Browser/JupyterLite** — fully static and safe to host on VT Domains today.
2. **ARC workspace** — continues through the local helper/OOD path until an institutionally supported hosted gateway is available.

A future hosted gateway is a server-side service, not a relaxed localhost policy.

## Shared policy layer

`control_plane.py` is deliberately I/O-free and can be imported by both the current helper and a future hosted gateway. It owns:

- current project policy;
- allowed execution providers;
- placement decisions;
- safe resource resolution;
- project/resource associations.

Authentication, provider credentials, OOD/Slurm calls, deployment, and storage are adapters outside the control plane.

## Minimum hosted gateway contract

A hosted implementation should expose a small authenticated API with project/workspace vocabulary rather than Slurm vocabulary.

Read operations:

- `GET /api/v1/session` — authenticated user/session state and non-secret capability flags;
- `GET /api/v1/projects` — projects visible to the authenticated user;
- `GET /api/v1/projects/{id}` — project and resource summaries;
- `GET /api/v1/workspaces` — provider-neutral workspace state;
- `GET /api/v1/providers` — enabled execution targets and capability metadata.

Planning operations:

- `POST /api/v1/placement` — return a provider decision without provisioning;
- `POST /api/v1/workspaces/resolve` — resolve known provider resources to a project without mutation.

Mutation operations must remain explicit and auditable:

- `POST /api/v1/workspaces` — create/start a reviewed workspace;
- `POST /api/v1/workspaces/{id}/stop` — stop a selected workspace;
- provider-specific deployment actions only after policy and human review.

The public API should return opaque workspace/resource IDs. Raw cookies, passwords, API keys, SSH material, provider session tokens, and private endpoint credentials must never be returned to the static client.

## Authentication

The hosted gateway should use Virginia Tech-supported web authentication/OIDC when an institutional deployment path is validated. It must not:

- collect VT passwords itself;
- automate MFA;
- copy OOD browser cookies into the static site;
- rely on a long-lived shared instructor credential;
- expose ARC SSH keys to browser JavaScript.

The authenticated VT identity should map server-side to project/course authorization.

## ARC adapter

ARC remains the compute provider, not the identity provider for the web client. Before implementing a hosted ARC adapter, validate the supported integration path with ARC. Do not scrape undocumented OOD internals from a server service merely to avoid that conversation.

Until a supported route exists, the existing visible OOD/local-helper path is the compatibility adapter.

## Deployment targets

Preferred order for a hosted gateway:

1. Virginia Tech IT Common Platform, if CLAHS/central IT confirm an appropriate tenant/onboarding path.
2. Institutionally managed cloud account (for example AWS) when the Common Platform does not fit the workload.
3. Do not run the control gateway as an ad-hoc long-lived process on VT Domains; VT Domains is the static/LAMP presentation tier, not the research control plane.

## Browser client behavior before the gateway exists

The VT Domains bundle may:

- embed/open JupyterLite;
- show public course/application metadata;
- link to documented public services.

It must keep ARC web-control features disabled until a real authenticated gateway URL is configured. This preserves a clean migration path without weakening the local helper.
