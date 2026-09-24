# CLAHS Research Application Foundations

Status: the v0.5 control-plane foundation and hosted gateway implementation are present in this repository. Institutional provider integrations and deployment remain external activation work.

## Goal

ARC Chat should become a thin control plane over existing Virginia Tech infrastructure rather than a replacement for it. The user-facing object is a **project/workspace/application**. ARC jobs, Jupyter servers, browser kernels, persistent services, and future cloud resources are provider-specific implementation details.

## Core contracts

### Project

`projects.py` owns durable, non-secret project state. A project groups workspaces, jobs, endpoints, artifacts, and deployments. Recovery persists opaque resource IDs and policy metadata rather than credentials or raw provider URLs where an opaque identifier is sufficient.

### Provider

`providers.py` describes execution capabilities. The initial registry exposes:

- `browser` — JupyterLite / zero-install browser compute;
- `arc` — ARC/Open OnDemand/Slurm research compute;
- `common-platform` — planned persistent application hosting target;
- `cloud` — planned escape hatch for workloads that do not fit the other providers.

Only browser and ARC are enabled by default. Planned providers remain review-only until there is a validated institutional integration.

### Placement

The placement engine decides which *class* of infrastructure fits a workload. It does not provision anything. Small browser-compatible work prefers JupyterLite; GPU/server-package work goes to ARC; persistent applications remain a reviewed future target.

### Resource resolver

`resolver.py` handles the multi-job problem. A previously linked active project job wins with high confidence. Explicit project metadata can establish a strong match. Unrelated active jobs never become an automatic attachment merely because they are running. Similar/tied candidates require human choice.

The OOD browser adapter now follows the same principle. When several ready Jupyter sessions are visible, ARC Chat emits differentiated choices using bounded session-card context and waits for an explicit selection rather than connecting to the first button in DOM order.

### Applications and deployment plans

`apps.py` defines project-scoped application manifests, an application registry, and non-mutating deployment plans. `ARC_CHAT_APP_FILE` can point at a versioned JSON manifest file. Planning an application routes through the same provider policy as an interactive workspace.

A planned provider such as the VT IT Common Platform can therefore be returned as the correct deployment class while remaining `requires_review=true` until its institutional integration is actually enabled. This distinguishes architectural intent from operational capability.

## Previous-workspace recovery

ARC Chat already persisted a previous Jupyter base/notebook. The helper now treats that as a high-confidence project attachment: after authentication, a reachable prior workspace for the same course/profile can be resumed without launching another allocation. When several Jupyter tabs are open, the exact previously recorded server is preferred. Stale state falls back to normal visible OOD selection.

## Student web surface

`web/student/` is a static, VT-Domains-friendly shell. It contains no secrets, embeds the configured JupyterLite site, and can call the optional hosted API when its public `gateway_url` is set and the gateway’s CORS/CSP origins match. The Projects view supports OIDC sign-in, authorized project selection, browser-workspace records, and ARC placement review. ARC execution stays disabled until the supported provider path is approved and configured.

## Security/institutional boundaries

- no MFA bypass;
- no password/cookie persistence in project records;
- no automatic provisioning of planned Common Platform/cloud targets;
- no hosted ARC mutations or provider credential handling;
- no student research-file storage in the hosted gateway;
- no guessing between ambiguous ARC resources;
- no mutation through the external integration API;
- existing explicit review boundaries for code execution and resource creation remain intact.

## Next integration milestones

1. Obtain a Common Platform tenant, PostgreSQL, Vault/External Secrets, ingress, DNS, TLS, and approved network ranges.
2. Register and verify the OIDC client, immutable subject, `mailPreferredAddress`, and `targetedMembership` claim mapping.
3. Host the static student shell on the provided VT Domains account and configure the exact gateway origin.
4. Validate prior-workspace recovery and provider metadata against live ARC/OOD.
5. Obtain ARC’s supported user-delegated job/session API before enabling hosted ARC mutation.
6. Add Common Platform app deployment, user-data quotas/retention, and operational runbooks after tenant onboarding.
7. Treat direct AWS/cloud as another provider behind the same application manifest only if an approved workload needs it.
