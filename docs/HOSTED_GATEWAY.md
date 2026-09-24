# Hosted Gateway

Status: the gateway service, PostgreSQL schema, student client integration, and hardened Kubernetes renderer are implemented. No Virginia Tech tenant, authentication client, Vault path, public hostname, or provider delegation is configured by this repository.

The gateway is a separate service boundary. It does not relax the local helper’s loopback policy, proxy ARC cookies, collect VT passwords, or provision a research provider without an approved adapter.

## Components

- `hosted_gateway/server.py` serves the authenticated `/api/v1` API using the shared project, placement, application, and resource-resolution code.
- `hosted_gateway/store.py` owns PostgreSQL queries, membership synchronization, idempotency, rate limits, audit events, and SSE event reads.
- `hosted_gateway/migrations/0001_core.sql` creates canonical opaque IDs, projects, course-group membership, applications, workspaces, provider resources, jobs, artifacts, deployments, audit, and event tables.
- `hosted_gateway/admin.py` provisions a project, its application manifests, and operator-mapped course groups from an administrator-owned JSON file.
- `web/student/` supports sign-in, project selection, browser-workspace records, ARC placement review, and the existing static JupyterLite view when a gateway origin is configured.
- `deploy/kubernetes/render.py` renders manifests only after receiving immutable image digests, exact origins, and explicit approved network ranges.
- `deploy/kubernetes/environments/` contains fail-closed `dvlp`, `pprd`, and `prod` value/project-mapping templates. They contain no assigned tenant, OIDC, Vault, network, or group values.

The static site’s public `gateway_url` must be an HTTPS origin. The bundle builder adds that exact origin to the student page’s Content Security Policy. The gateway separately allows only the configured static origin through CORS. Use related same-site hostnames where possible so browsers accept the secure cross-origin session cookie; third-party-cookie blocking can prevent a browser client and API on unrelated sites from sharing a session.

## API surface

| Endpoint | Purpose |
| --- | --- |
| `GET /health/live`, `GET /health/ready`, `GET /metrics` | Liveness, PostgreSQL readiness, and scrape metrics |
| `GET /api/v1/session` | Authenticated identity summary, project IDs, capability flags, and the CSRF token |
| `GET /api/v1/projects`, `GET /api/v1/projects/{id}` | List and read the caller’s authorized projects |
| `GET /api/v1/providers` | List available and planned providers |
| `GET /api/v1/applications`, `GET /api/v1/applications/{id}` | List and read project application manifests |
| `POST /api/v1/applications/{id}/plan`, `POST /api/v1/placement` | Review a provider choice without provisioning |
| `GET /api/v1/workspaces`, `GET /api/v1/workspaces/{id}` | List and read authorized workspace records |
| `POST /api/v1/workspaces` | Create a browser-workspace record when JupyterLite is configured |
| `POST /api/v1/workspaces/resolve` | Resolve server-owned provider records; client-supplied candidates are rejected |
| `POST /api/v1/workspaces/{id}/stop` | Close an explicitly selected browser-workspace record |
| `GET /api/v1/workspaces/{id}/jobs`, `GET /api/v1/jobs/{id}` | Read authorized job summaries |
| `GET /api/v1/jobs/{id}/logs`, `POST /api/v1/jobs/{id}/cancel` | Return a safe provider-unavailable response until an approved adapter exists |
| `GET /api/v1/artifacts`, `GET /api/v1/deployments` | List authorized artifact and deployment records |
| `GET /api/v1/events` | Stream authorized project events with SSE and `Last-Event-ID` resume |

Mutation requests require an allowed `Origin`, the session’s `X-CSRF-Token`, and an `Idempotency-Key`. Project authorization comes from the server database. Provider resource identifiers returned by the API are opaque IDs. Error responses use `error_version`, `code`, `message`, `request_id`, and `recovery_action`.

`POST /api/v1/workspaces` creates a record that points to the static JupyterLite site; it does not create an isolated server kernel. Notebook files and kernels remain in browser storage. Closing this record does not stop a notebook tab that is already running. ARC start/stop, Slurm cancel/log access, persistent services, and cloud deployments are intentionally capability-gated until supported institutional adapters are approved and configured.

The current OpenAPI file is [`hosted-gateway-openapi.yaml`](hosted-gateway-openapi.yaml).

## Authentication and authorization

The deployment places OAuth2 Proxy beside the gateway in the same pod. Only the proxy is exposed by the ClusterIP Service and ingress. It strips incoming authentication headers, performs OIDC, and forwards `X-Forwarded-User`, `X-Forwarded-Email`, and `X-Forwarded-Groups` to the gateway over loopback. The gateway trusts those headers only from configured proxy CIDRs. It stores an HMAC of the OIDC subject and does not persist the raw subject.

Before enabling the deployment, confirm that the chosen OIDC provider puts an immutable subject in `X-Forwarded-User`; configure the gateway’s `AUTH_USER_HEADER` to match the validated proxy behavior. The proxy claim names are set for Virginia Tech’s requested `mailPreferredAddress` and `targetedMembership` claims. Verify those claims and the comma-delimited group header against the actual OIDC client before onboarding users.

Roles come only from rows in `course_group_mappings`, provisioned by an operator. Each authenticated request synchronizes its trusted group snapshot. A change to group membership or the operator’s group-to-project mapping refreshes project access on the next request. Direct memberships take precedence over course-group-derived memberships. A missing group claim revokes course-group-derived access rather than retaining stale access.

The session uses a secure, HTTP-only OAuth cookie and a second secure CSRF cookie. Browser mutations also send the CSRF value returned by `/api/v1/session`. Never set `DEV_TRUST_IDENTITY_HEADERS=true` outside a private development process.

## Local service setup

Use an approved PostgreSQL service and the gateway environment variables described in [`hosted_gateway/config.py`](../hosted_gateway/config.py). Run the schema migration as a single operator-controlled step before starting gateway replicas:

```powershell
python -m pip install -r requirements-gateway.txt
python -m hosted_gateway.migrate
python -m hosted_gateway.admin docs/hosted-project.example.json
python -m hosted_gateway.server
```

`DATABASE_URL`, `IDENTITY_HMAC_KEY`, and `CSRF_HMAC_KEY` are required. Keep the two HMAC keys stable across restarts; changing an identity key changes the derived account identity and requires a planned database migration. Store production values in the approved secret manager, not in the repository or static bundle.

The example project spec demonstrates only public labels and placeholder group names. Replace those names with groups returned by the approved OIDC claim before running the admin command. The admin CLI is a desired-state upsert for the project, its listed apps, and its course-group mappings. It does not create users or direct memberships, and it does not automatically delete old application manifests.

## Kubernetes rendering

The renderer produces JSON Kubernetes objects, which `kubectl` accepts directly:

```powershell
python deploy/kubernetes/render.py operator-values.json rendered
kubectl apply -f rendered/
```

`operator-values.json` must provide the environment (`dvlp`, `pprd`, or `prod`), tenant label, namespace, gateway host, ingress class, TLS issuer and certificate secret name, static origin, JupyterLite URL, registered OIDC redirect/client/issuer, email domain, immutable gateway image digest, External Secrets `ClusterSecretStore` name, Vault path, ingress/monitoring namespaces, explicit trusted-ingress/PostgreSQL/OIDC egress CIDRs, replica count, per-container CPU/memory requests and limits, and external PostgreSQL/no-persistent-volume storage policy. The renderer rejects mutable image tags, unrestricted `/0` networks, non-HTTPS origins, missing limits, requests larger than limits, a persistent gateway volume, and fewer than two replicas in `pprd`/`prod`.

The manifests apply a non-root gateway and OAuth sidecar, read-only filesystems, dropped Linux capabilities, a short-lived secure cookie, TLS ingress, a cert-manager `Certificate`, an External Secrets resource, a default-deny ingress/egress NetworkPolicy with explicit DNS and service CIDRs, the supplied replicas/resource limits, and a PodDisruptionBudget. Tenant and environment labels appear on generated resources. The gateway has no persistent pod volume; its only durable state is the external PostgreSQL database. Course ED group mappings are applied separately through the environment's operator project spec after the OIDC group claim is verified. The cluster must already provide the namespace, `ExternalSecret` CRD, named `ClusterSecretStore`, cert-manager issuer, ingress controller, DNS, image pull access, and approved egress routing. The Vault object must contain exactly the keys referenced by the renderer; its client secret must have no trailing newline and its cookie secret must be 16, 24, or 32 random bytes.

See [`COMMON_PLATFORM_ONBOARDING.md`](COMMON_PLATFORM_ONBOARDING.md) for the values/ownership request and [`ARC_INTEGRATION_SPEC.md`](ARC_INTEGRATION_SPEC.md) for the delegated provider questions.

The example renderer does not invent an IT tenant, IDP issuer, Vault policy, ingress range, database range, image digest, DNS name, or certificate owner. Have the platform operator supply and review those values. Do not apply the sample until they reflect the actual tenant.

## Operations and data handling

- Liveness is separate from readiness; readiness requires PostgreSQL.
- Logs are structured and include request ID, method, route, status, duration, and opaque actor ID. They exclude request bodies and OIDC group/email claims.
- `/metrics` exposes aggregate request counts; the rendered NetworkPolicy permits it only from the monitoring namespace on the gateway port.
- `audit_events` are append-only. Workspace cleanup only ages local records; it never stops provider resources.
- Idempotency records expire after 24 hours. Rate-limit windows are cleaned after two days.
- SSE connections poll for authorized events, heartbeat when idle, and close after 30 minutes so clients reconnect with `Last-Event-ID`.
- Back up PostgreSQL under institutional policy. Define production retention, deletion, recovery, alert thresholds, and on-call ownership with the platform operator before a pilot.

## Remaining institutional activation gates

These require a Virginia Tech or ARC administrator and cannot be completed from this repository:

1. Provision the Common Platform namespace, PostgreSQL, External Secrets/Vault store, ingress, TLS issuer, DNS, monitoring, and approved network ranges.
2. Register the OIDC client and callback URL; confirm subject, email, group claim names and formats; authorize the exact static-site origin.
3. Publish and scan the gateway image, record its registry digest, and pass the cluster’s image/signature policy.
4. Provision project and group mappings using [`hosted-project.example.json`](hosted-project.example.json) adapted with real course groups.
5. Obtain ARC’s supported delegated API path and an authorized acceptance account before enabling ARC mutations.
6. Supply Developer ID credentials/notarization for signed macOS production releases, and complete an authorized ARC/browser pilot with support, retention, quota, and incident runbooks.

Until those gates pass, the hosted API can serve browser-side project metadata and JupyterLite records; ARC remains on the visible local helper/Open OnDemand path.
