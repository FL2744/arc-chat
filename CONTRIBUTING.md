# Contributing to ARC Chat

ARC Chat is intentionally conservative around authentication, remote execution, resource allocation, and research data. Changes should improve capability without weakening explicit human control.

## Development rules

- Never commit personal ARC allocations, API keys, credentials, browser cookies, private notebook/user data, or real recovery state.
- Keep Student Mode simpler than Advanced Mode. Infrastructure details belong in Advanced Mode unless a course profile can supply them safely.
- Treat official ARC documentation as the contract for ARC-specific behavior. Do not invent hidden OOD endpoints, allocation APIs, partition names, or scheduler semantics.
- Put infrastructure-specific behavior behind interfaces (`Workspace`, model providers, `JobBackend`, service/integration contracts) instead of embedding it directly in UI code.
- Every code execution, file mutation, Slurm submit/cancel, and model-service start/stop must remain reviewable and explicitly human-approved.
- Connection recovery may reconnect but must never silently replay Python or a resource mutation.
- New local/remote persistence must have a documented retention purpose and a test proving secrets/content are excluded where required.
- Breaking changes to `/api/v1` require a new API version rather than silently changing the v1 contract.

## Required checks

Run:

```text
python -m py_compile helper.py config.py state.py model_providers.py diagnostics.py ood.py workspace.py protocol.py security.py context_window.py artifacts.py jobs.py services.py integration.py errors.py version.py
python -m unittest discover -s . -p "test_*.py" -v
python integration_test.py
```

Packaging/release changes should also build the bootstrap bundles and pass the self-contained Windows/macOS smoke jobs in CI.

## Documentation changes

If implementation changes a major assumption, update `docs/TECHNICAL_PLAN.md` and the current release notes in the same change. When ARC behavior is based on documentation rather than live confirmation, say so explicitly.

