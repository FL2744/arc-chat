# Static Student Web Client

This directory is the browser-first Student Mode foundation for VT Domains-style hosting.

## What works now

- a static launcher requiring no local installation;
- an embedded JupyterLite tab using the course deployment at `fl2744.github.io`;
- a full-page JupyterLite escape hatch;
- no API keys, ARC credentials, cookies, or user research data stored in the static bundle.

JupyterLite is the zero-infrastructure execution tier. It is appropriate for small browser-compatible Python/notebook work. ARC remains the research-compute tier for server-side packages, large data, GPUs, and longer jobs.

## Why ARC is not directly wired from this page yet

The existing ARC Chat helper is intentionally loopback-only. A hosted page must not receive permission to call that local control surface. The ARC card in `config.json` therefore remains disabled until an authenticated hosted gateway is validated.

See `docs/HOSTED_GATEWAY.md`.

## Build the deployment archive

From the repository root:

```sh
python distribution/build_student_web.py
```

This creates:

- `dist/ARC-Chat-Student-Web.zip`
- `dist/ARC-Chat-Student-Web.zip.sha256`

The build validates the public configuration, rejects credential-like content and non-HTTPS application targets, and emits a flat static archive.

## Deploy on VT Domains

VT Domains supports custom HTML/CSS/JavaScript sites and currently documents a 1 GB account storage limit. Upload the **contents** of the generated ZIP into the document root for the desired site/path using the provided cPanel account. No PHP or database is required for this client.

After deployment, verify:

1. the Start view loads;
2. Browser notebook opens the embedded JupyterLite view;
3. Open full page goes to the expected JupyterLite deployment;
4. browser developer tools show no mixed-content or CSP errors;
5. no secret/config file beyond the intended public `config.json` is present.

## Configuration

`config.json` is public by design. Only put labels, public HTTPS URLs, and feature flags in it.

Do not put:

- passwords;
- API keys;
- tokens;
- cookies;
- SSH material;
- private service endpoints;
- student data.

The disabled ARC application can be enabled only after a hosted ARC gateway exists and has a public HTTPS entry point appropriate for students.
