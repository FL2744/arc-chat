# ARC Chat v0.5.0

Build: `2026.09.24.1`

This architectural release establishes the project/workspace control plane and adds the first standalone hosted-gateway implementation. It is a source/review build; no Common Platform service or hosted ARC provider is deployed by this release.

## Control plane and local helper

- Added canonical opaque hosted IDs for users, projects, applications, workspaces, jobs, provider resources, endpoints, artifacts, deployments, audit events, and platform events.
- Completed provider-neutral workspace lifecycle states, provider-state mappings, staleness handling, and persisted associations.
- Strengthened project-resource resolution: exact stored links and explicit server-owned project metadata can be reused; unrelated active jobs and name-only hints cannot be auto-attached.
- Routed Slurm submission and explicit resource association through `ControlPlane`.
- Added registry serialization round trips and recovery schema v1-v4 migration coverage.
- Added public metadata validation and stopped using provider endpoint URLs as endpoint identifiers.

## Hosted gateway and student client

- Added an aiohttp API with PostgreSQL schema/migrations, group-derived project membership, per-object authorization, HMAC-derived user IDs, CSRF, exact-origin CORS, idempotency, rate limits, request IDs, structured logging, aggregate metrics, and SSE events.
- Added an operator CLI for provisioning project/application manifests and course-group mappings.
- Added a static Projects view with optional gateway sign-in, project selection, browser-workspace records, and non-mutating ARC placement review.
- Added a fail-closed Kubernetes renderer with non-root/read-only containers, an OIDC proxy sidecar, External Secrets, TLS ingress, explicit network policy CIDRs, replicas, and disruption budget.
- Added a CI-generated SPDX SBOM and Grype image scan; CI blocks gateway images with high or critical known vulnerabilities and retains the reports as workflow artifacts.
- Kept ARC and Common Platform mutations disabled until their institutionally supported provider adapters and credentials are available.

## Verification

- Full local unit suite after the environment-renderer changes: **149 tests passed; 2 PostgreSQL-only tests skipped without a local database**.
- Python compile gate: passed for gateway, control-plane, and distribution modules.
- The GitHub Actions matrix passed on the v0.5 foundation commit, including the fresh PostgreSQL integration service, Docker image build, OpenAPI validation, and portable package smoke checks. The follow-up SBOM/vulnerability gate is being added in this change and must pass before release.
- CI does not replace OIDC claim verification, a Common Platform deployment, or an authorized ARC acceptance pass.

## Activation work still required

- Provision the VT Common Platform tenant, database, Vault integration, namespace, DNS, ingress/TLS, monitoring, and real network ranges.
- Register the OIDC client and confirm that the chosen proxy forwards an immutable subject plus the expected email and group claims.
- Publish, scan, and pin the gateway image by registry digest; render and review deployment values for the actual tenant.
- Deploy the static client and configure matching gateway CORS/CSP origins.
- Obtain ARC’s approved user-delegated API for job/session discovery and mutations. Until then ARC remains on the secure local helper/Open OnDemand path.
- Pin and configure the separately maintained JupyterLite deployment; validate the requested 100 MB file policy and browser-storage quota there.
- Complete the environment-specific Common Platform values and ownership decisions in [`COMMON_PLATFORM_ONBOARDING.md`](COMMON_PLATFORM_ONBOARDING.md); no institutional values are checked in.
- Confirm ARC's supported delegated operations against [`ARC_INTEGRATION_SPEC.md`](ARC_INTEGRATION_SPEC.md).
- Define production retention, backup, SLO, quota, support, incident, and staged-pilot runbooks. Sign/notarize macOS releases with institutional credentials before production distribution.
