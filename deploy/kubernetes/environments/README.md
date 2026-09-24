# Environment values and project access templates

Each environment has a separate `values.example.json` for
`deploy/kubernetes/render.py` and a separate
`project-mapping.example.json` for `hosted_gateway.admin`. These are templates,
not deployment values. Empty fields are intentional; the renderer and project
admin validation must reject them until Common Platform and VT identity owners
provide approved values.

```text
deploy/kubernetes/environments/
  dvlp/
    values.example.json
    project-mapping.example.json
  pprd/
    values.example.json
    project-mapping.example.json
  prod/
    values.example.json
    project-mapping.example.json
```

The render command consumes one completed values file at a time:

```powershell
python deploy/kubernetes/render.py operator-values.json rendered
```

The environment name must match its directory. Supply only platform-approved
tenant/namespace/DNS, immutable image digest, OIDC values, Vault reference,
TLS/ingress settings, explicit CIDRs, replica count, and container resource
requests/limits. `pprd` and `prod` require at least two replicas. The gateway
uses an external PostgreSQL service and must not receive a persistent pod
volume; database endpoint and credentials belong in the Vault-backed
`DATABASE_URL` property.

The student/project group mapping is separate from Kubernetes. Replace the
empty classification and add exact group values as verified in the OIDC claim;
then review the project/app spec before provisioning it against that
environment's database. An empty `course_groups` list grants no group-derived
project access. Do not copy a development group mapping or secret reference
into another environment without its owner's approval.

No template contains a default hostname, tenant, institution-owned CIDR,
OIDC client, secret path, or ED group. Never commit the completed secret values
or a `.env` file.

