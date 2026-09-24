# ARC delegated integration specification

**Status:** interface request for ARC/Research Computing. This document asks
which supported user-delegated interfaces ARC provides. It does not assume that
Open OnDemand scraping, a shared service credential, or a particular API is
approved.

## Purpose and security boundary

The hosted gateway needs to map its authenticated user to the same VT person ARC
authorizes, discover that user's allocations and resources, and request
user-scoped Slurm/Jupyter actions. ARC must identify an approved delegation
mechanism before a hosted ARC provider can be enabled.

Until then, the local ARC Chat helper remains the compatibility path. It uses
the user's visible Open OnDemand browser session and/or the user's configured
SSH authentication. The hosted gateway does not receive VT passwords, Duo
codes, OOD cookies, SSH keys, or Jupyter auth tokens. The static `l1001` site
does not call localhost and cannot trigger local-helper operations.

## Requested capability matrix

For each operation, ARC should identify the supported interface, authorization
model, request/response contract, rate limits, error semantics, and whether it
is approved for a user-facing application. “Local fallback” describes what the
current desktop helper can do; it is not a claim that ARC has endorsed that
mechanism for a hosted server.

| Capability | Hosted gateway requirement | Current local-helper fallback |
| --- | --- | --- |
| Authenticated-user mapping | Map the gateway's verified immutable VT subject to the ARC principal. Define mismatch and account-linking behavior without using email as the durable key. | User signs in through the visible OOD browser. SSH mode uses the locally configured ARC username/host and user's own SSH authentication. No VT password or MFA code is collected by ARC Chat. |
| Authorized allocation discovery | Return only allocations/accounts, clusters, and partitions the delegated user may use; include limits, expiry, and policy metadata needed for review. | Course profile can supply an allocation. The visible OOD launch form exposes available account choices; the user selects/reviews the allocation. The helper does not provide a hosted authoritative allocation API. |
| Resource policy/preview | Validate account, cluster, partition, CPU, GPU type/count, memory, walltime, and any SU/cost estimate before submission. Clearly distinguish estimates from enforced limits. | The helper renders a Slurm job preview and exposes resource profiles; OOD launch remains a visible user-reviewed form. Enforcement is ultimately ARC/Slurm policy. |
| Submit batch job | Idempotent, per-user job submission with a returned scheduler job ID and a way to reconcile a timed-out response without duplicate submission. | `SlurmBackend.submit` sends a reviewed script through the user's SSH connection with `sbatch --parsable`; the helper records the returned job ID locally. |
| List active/recent jobs | List only delegated user's jobs; include job ID, account, cluster, job name, submitted/start/end timestamps, state/reason, and permitted metadata. Support pagination/history retention. | `SlurmBackend.list_active` runs `squeue` for the SSH user. It returns active jobs only; completed history is limited to locally recorded jobs. Ambiguous project association requires user choice. |
| Job status | Read state, pending reason, allocated node, timestamps, and terminal state by exact job/cluster ID. | `SlurmBackend.status` queries `squeue` for one validated job ID. Empty queue result maps to `NOT_IN_QUEUE`; the local path does not itself distinguish every historical terminal state. |
| Cancel job | Cancel only a job owned by or explicitly delegated to the caller; require the exact ID and return a clear accepted/final status. | `SlurmBackend.cancel` runs `scancel` over the user's SSH session after explicit user action. |
| Job logs | Provide bounded, authorized stdout/stderr or a short-lived safe reference; redact secrets and avoid arbitrary filesystem paths. | The helper obtains the Slurm-reported stdout path via `scontrol show job`, tails a bounded line count over SSH, and displays recent output. This is an SSH fallback, not a hosted log service. |
| Interactive OOD launch | Create or identify a supported interactive Jupyter app/session for the delegated user, with explicit resource review and idempotent create behavior. Clarify whether the supported operation is OOD API, scheduler API, or another approved interface. | The user signs in visibly to `ood.arc.vt.edu`; the helper prepares the OOD form, exposes the account/resource selection, and clicks only a uniquely identified `Launch` control after review. |
| Interactive session discovery | List the user's OOD/Jupyter sessions with opaque session IDs, job IDs, state, cluster, and creation time. Do not rely on dashboard DOM order. | The helper inspects the visible OOD session cards/links. Multiple ready Jupyter sessions produce explicit user choices; a previous exact association may be resumed. No hosted OOD HTML scraper exists. |
| Safe Jupyter endpoint | Issue a user-scoped endpoint or short-lived brokered link without exposing reusable credentials in the URL, logs, API responses, or persistent metadata. Explain TLS, lifetime, audience, revocation, and browser access constraints. | The helper attaches to the Jupyter server opened in the user's authenticated browser, uses Jupyter's session/kernel APIs through that browser context, and stores local recovery metadata. It does not expose that browser session to the hosted gateway. |
| Project/workspace correlation | Accept an opaque ARC Chat workspace/project correlation value in scheduler metadata where supported, or return a durable supported association mechanism. | The helper persists explicit user-selected resource/job-to-workspace associations locally. `ControlPlane` uses exact links and approved project metadata for recovery, never “only active job” or DOM ordering. |
| Delegation lifecycle | Define consent, token issuance/storage, scopes, refresh/revocation, expiry, audit attribution, and user offboarding. Prefer per-user delegation with narrow operations. | No server-side delegation is implemented. SSH uses the user's local auth; OOD uses the user's browser session. |
| Resource usage | Return post-run usage metrics through supported APIs, including the stable job/account/cluster identity and units. | No unified provider adapter currently collects usage. ARC guidance may point to approved equivalents for `showjobusage`, `seff`, or job-utilization links. |

## Required semantic details

ARC responses should include provider-owned identifiers separately from
ARC Chat's opaque identifiers. At minimum, a normalized resource should map to
these fields when ARC exposes them:

```json
{
  "provider": "arc",
  "resource_id": "<ARC-owned opaque or scheduler identifier>",
  "kind": "job|jupyter|interactive-session",
  "user_id": "<delegated ARC principal reference>",
  "project_correlation": "<optional opaque workspace/project value>",
  "account": "<authorized allocation>",
  "job_id": "<scheduler job ID>",
  "cluster": "<cluster identifier>",
  "state": "<provider state>",
  "created_at": "<UTC timestamp>",
  "started_at": "<UTC timestamp or null>",
  "node": "<authorized metadata or null>"
}
```

Provider URLs must not be primary IDs. If a Jupyter endpoint must contain a
bearer credential, do not return or persist that URL as ordinary metadata;
provide a short-lived brokered endpoint or a documented browser-mediated
handoff instead. The adapter must never attach an unrelated running resource
because it is the only one visible.

Mutating calls need a client-provided idempotency/correlation key or an
equivalent reconciliation method. A lost HTTP response after Slurm accepted a
submission must not make a retry create another job. The API should report
ambiguity as a chooser response, not silently pick a resource.

## Questions for the ARC integration meeting

1. Which supported interface can map a VT OIDC subject to the user's ARC
   identity? Is an approved account-link step required?
2. Can an application request a narrowly scoped, user-delegated token? If so,
   how are consent, storage, refresh, revocation, session expiry, and audit
   attribution handled?
3. Which approved API or protocol returns the user's authorized allocations,
   clusters, partitions, and resource limits?
4. Is there an approved server-side interface for `sbatch`, job status/history,
   cancellation, and bounded stdout/stderr retrieval? What idempotency and
   ownership checks does it provide?
5. Which supported interface launches and discovers interactive OOD/Jupyter
   sessions? Does it expose scheduler job IDs and durable session IDs?
6. How should a web client receive a safe Jupyter endpoint without exposing
   OOD cookies, reusable tokens, SSH credentials, or credential-bearing URLs?
7. Can ARC preserve an opaque project/workspace correlation value in job
   metadata/comments and return it during discovery? What length/character
   constraints apply?
8. Which clusters and allocations may be used by the initial FL 2744/research
   pilot, and which resource ceilings/quotas must the application show before
   launch?
9. What test allocation, non-production account, and representative identity
   are available for contract and failure testing?
10. What rate limits, maintenance windows, user support path, incident process,
    data-retention policy, and API-versioning commitments apply?
11. Which usage/cost/efficiency metrics may be read after job completion?
12. If server delegation is unavailable, can ARC confirm the supported local
    per-user helper path and any browser/SSH requirements that must be preserved?

## Adapter acceptance tests

Do not enable the hosted ARC capability until an approved adapter passes these
checks against an authorized non-production allocation:

- A user sees only their own permitted accounts and jobs; another user's job
  cannot be read, cancelled, or used to obtain an endpoint.
- A project A resource is never auto-attached to project B. Exact stored
  associations and explicit project metadata are honored; multiple plausible
  matches require a user choice.
- Duplicate submission keys create one scheduler job. A simulated timeout
  followed by retry reconciles the first job rather than submitting again.
- Cancel, status, and log access validate user, cluster, and exact provider ID.
- Queued, running, completed, failed, cancelled, expired, and unknown states
  map into the generic workspace/job state model without losing provider state.
- Interactive launch returns a correlation ID immediately; refresh/restart
  recovers the same session and never submits a replacement because polling
  failed.
- Expired delegation fails closed, can be revoked, and does not fall back to a
  shared service identity.
- Logs and audit/events contain no VT tokens, OOD cookies, SSH keys, Jupyter
  tokens, database credentials, provider secrets, or credential-bearing URLs.
- ARC API outage does not affect browser/JupyterLite or Common Platform
  capabilities.

## Decision gate

Until ARC identifies and approves the delegated operations above, the gateway
must report ARC as unavailable for hosted mutation (or as a planned/local-agent
capability with the exact limitation shown). Browser compute and hosted
metadata remain independent. If no supported server delegation is available,
retain the local per-user ARC helper and design a separately reviewed pairing
protocol before any browser-to-local-agent connection is introduced.

