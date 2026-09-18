# ARC Chat governance

ARC Chat is currently maintained as a small project rather than a Virginia Tech centrally managed service.

## Decision model

- Repository maintainers accept/reject code and documentation changes.
- Security, authentication, privacy, execution-approval, and resource-allocation invariants take precedence over convenience features.
- Course-specific policy belongs in versioned course profiles rather than forks or hard-coded personal values.
- ARC-specific behavior should track official ARC documentation; material departures require a documented rationale.
- Major architecture decisions are recorded in `docs/TECHNICAL_PLAN.md` rather than existing only in chat/issues.

## Institutional boundary

The repository does not claim to represent an officially supported Virginia Tech product. Institutional deployment decisions - managed distribution, signing/notarization credentials, formal support ownership, data classification approval, and copyright/licensing ownership - require the appropriate Virginia Tech/project stakeholders.

## Release authority

Maintainers may publish preview releases after automated release gates pass. A preview becoming an institutionally supported release requires the deployment/support decisions above; a Git tag alone does not imply institutional support.

