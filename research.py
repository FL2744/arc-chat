"""ARC Research action service: connection, discovery, workloads, runs, catalog, terminal, planning.

Each handler returns ``(status_text, [(event_type, payload), ...])`` so the helper can
emit events without this module knowing about websockets. All ARC access goes through
the bridge's SSH gateway; nothing here runs compute on a login node.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import catalog as catalog_mod
import discovery
import planner
import sshkeys
from applog import AppLog
from bugreport import build_report, finalize
from connection import validate_connection
from jobs import SlurmBackend, SshCommandGateway
from naming import generate_job_name, validate_job_name
from project_extras import ProjectExtras
from runs import RunHistory
from safety import classify_command
from terminal import CommandSurface
from workload import WorkloadSpec

Result = tuple[str, list[tuple[str, dict[str, Any]]]]
DEFAULT_HOST = "falcon2.arc.vt.edu"


class ResearchService:
    ACTIONS = {
        "arc_username_check", "ssh_keys", "ssh_key_generate", "ssh_key_inspect", "connection_test",
        "arc_discover", "resource_recommend", "catalog_refresh", "catalog_search", "catalog_favorite",
        "name_suggest", "workload_preview", "workload_submit", "runs_list", "run_duplicate", "run_export",
        "run_rerun", "config_save", "config_list", "config_load", "note_add", "notes_list",
        "command_propose", "command_run", "command_cancel", "command_reject", "commands_history",
        "plan_assist", "report_build", "log_level", "log_recent", "context_status",
    }

    def __init__(self, bridge, state_dir: Path | None = None):
        self.bridge = bridge
        state_dir = Path(state_dir) if state_dir else None
        self.log = AppLog()
        self.runs = RunHistory(state_dir / "runs.json" if state_dir else None)
        self.inventory = catalog_mod.ModelInventory(path=state_dir / "catalog.json" if state_dir else None)
        self.extras = ProjectExtras(state_dir / "project_extras.json" if state_dir else None)
        self.terminal = CommandSurface(self._gateway_from_payload)
        self.connection: dict[str, Any] = {}
        self.discovered: dict[str, Any] = {}

    # ---- helpers -------------------------------------------------------------------------
    def _gateway_from_payload(self, d):
        return SshCommandGateway(str(d.get("arc_user", "")).strip(), str(d.get("login_host") or DEFAULT_HOST))

    def _project_name(self) -> str:
        project = self.bridge.project_registry.current() if getattr(self.bridge, "project_registry", None) else None
        return project.manifest.name if project else ""

    def _allocation(self, d) -> str:
        return str(d.get("job_account") or self.bridge.profile.resolved_allocation() or "").strip()

    def _spec_from_payload(self, d) -> WorkloadSpec:
        raw = d.get("workload") if isinstance(d.get("workload"), dict) else {}
        spec = WorkloadSpec.from_dict({k: v for k, v in raw.items() if k != "schema_version"})
        spec.project = spec.project or self._project_name()
        spec.allocation = spec.allocation or self._allocation(d)
        return spec

    def _taken_names(self) -> list[str]:
        return [r.spec.get("job_name", "") for r in self.runs.list()] + [j.name for j in self.bridge.job_history.list()]

    def context(self) -> dict[str, Any]:
        b = self.bridge
        kernel = bool(getattr(b, "kernel", None))
        active = [j for j in b.job_history.list() if j.state in {"RUNNING", "PENDING", "SUBMITTED", "CONFIGURING"}]
        return {
            "authenticated": bool(self.connection.get("ok")),
            "arc_session": getattr(b.state_machine, "state", None) and b.state_machine.state.name,
            "allocation": b.profile.resolved_allocation() or "",
            "environment": "compute" if kernel else "login",
            "environment_note": ("Connected to a Jupyter session running on an ARC compute node." if kernel else
                                 "Commands run on the ARC login node; only lightweight commands are allowed there."),
            "computation_safe": True,
            "active_jobs": [{"job_id": j.job_id, "name": j.name, "state": j.state, "node": j.node} for j in active],
            "project": self._project_name(),
            "login_host": self.connection.get("login_host", ""),
        }

    # ---- dispatch -----------------------------------------------------------------------
    async def handle(self, action: str, d: dict[str, Any]) -> Result:
        handler = getattr(self, "do_" + action)
        self.log.debug("action", f"start {action}")
        try:
            result = await handler(d)
        except Exception as exc:
            self.log.info("error", f"{action} failed: {exc}", action=action)
            raise
        self.log.verbose("action", f"finished {action}")
        return result

    # ---- connection setup ---------------------------------------------------------------
    async def do_arc_username_check(self, d) -> Result:
        try:
            name = sshkeys.validate_arc_username(d.get("arc_user", ""))
            return f"Username '{name}' has a valid format.", [("username_check", {"ok": True, "username": name})]
        except ValueError as exc:
            return str(exc), [("username_check", {"ok": False, "message": str(exc)})]

    async def do_ssh_keys(self, d) -> Result:
        keys = []
        for path in sshkeys.list_existing_keys():
            keys.append({"path": str(path), "problems": sshkeys.check_key_permissions(path)})
        return f"Found {len(keys)} local SSH key(s).", [("ssh_keys", {"keys": keys, "directory": str(sshkeys.default_key_dir())})]

    async def do_ssh_key_generate(self, d) -> Result:
        pair = sshkeys.generate_key(str(d.get("key_name") or "arc_research_ed25519"),
                                    algorithm=str(d.get("algorithm") or "ed25519"))
        self.log.info("connection", "generated SSH key", path=str(pair.private_path))
        return ("SSH key created locally. Register the public key with ARC; the private key stays on this computer.",
                [("ssh_key", {"private_path": str(pair.private_path), "public_key": pair.public_key})])

    async def do_ssh_key_inspect(self, d) -> Result:
        path = Path(str(d.get("key_path", ""))).expanduser()
        problems = sshkeys.check_key_permissions(path)
        pub = Path(str(path) + ".pub")
        public_key = pub.read_text(encoding="utf-8").strip() if pub.is_file() else ""
        return ("Key looks fine." if not problems else "; ".join(problems),
                [("ssh_key", {"private_path": str(path), "public_key": public_key, "problems": problems})])

    async def do_connection_test(self, d) -> Result:
        host = str(d.get("login_host") or DEFAULT_HOST)
        self.log.debug("connection", "testing connection", host=host, user=d.get("arc_user", ""))
        result = await validate_connection(str(d.get("arc_user", "")), host,
                                           lambda u, h: SshCommandGateway(u, h),
                                           expected_allocation=str(d.get("job_account", "")).strip())
        result["login_host"] = host
        self.connection = result
        self.log.info("connection", "connection test " + ("passed" if result["ok"] else "failed at " + result["failed_stage"]))
        text = "ARC connection verified." if result["ok"] else next(
            (s["message"] for s in result["stages"] if not s["ok"]), "Connection check failed.")
        return text, [("connection", result), ("context", self.context())]

    # ---- discovery / resource selection --------------------------------------------------
    async def do_arc_discover(self, d) -> Result:
        gateway = self._gateway_from_payload(d)
        sinfo = await gateway.run(discovery.SINFO_COMMAND, timeout=30)
        pending = await gateway.run(discovery.SQUEUE_PENDING_COMMAND, timeout=30)
        mine = await SlurmBackend(gateway).list_active()
        accounts = discovery.parse_accounts(await gateway.run(discovery.ACCOUNTS_COMMAND, timeout=30))
        rows = discovery.parse_sinfo(sinfo)
        summary = discovery.summarize_partitions(rows, discovery.parse_pending_counts(pending))
        self.discovered = {"rows": rows, "summary": summary, "accounts": accounts}
        self.log.debug("discovery", "scheduler responses parsed", partitions=len(summary), sinfo_lines=len(sinfo.splitlines()))
        return (f"Loaded live scheduler data for {len(summary)} partition(s).",
                [("arc_resources", {"partitions": summary, "active_jobs": mine, "accounts": accounts})])

    async def do_resource_recommend(self, d) -> Result:
        if not self.discovered:
            raise ValueError("Inspect ARC resources first so recommendations use live scheduler data.")
        gpus = int(d.get("gpus") or 1)
        need = d.get("gpu_memory_gb")
        model = self.inventory.get(str(d.get("model_id", ""))) if d.get("model_id") else None
        if model and not need:
            need = model.memory_estimate_gb()
        recs = discovery.recommend(self.discovered["rows"], gpus=gpus, gpu_memory_gb=int(need) if need else None,
                                   gpu_memory_table=catalog_mod.NOMINAL_GPU_MEMORY_GB)
        note = ("Ranked by idle nodes right now. ARC Research does not predict queue start times; "
                "GPU memory figures are nominal per-card estimates.")
        return f"{len(recs)} compatible partition(s).", [("recommendations", {"items": recs, "note": note, "needs_gb": need})]

    # ---- model catalog ------------------------------------------------------------------
    async def do_catalog_refresh(self, d) -> Result:
        text = await self._gateway_from_payload(d).run(catalog_mod.INVENTORY_COMMAND, timeout=90)
        entries = catalog_mod.parse_inventory(text)
        self.inventory.entries = entries
        self.log.info("catalog", f"discovered {len(entries)} model(s) from ARC")
        return await self._catalog_event(d, f"Discovered {len(entries)} ARC-hosted model(s).")

    async def do_catalog_search(self, d) -> Result:
        return await self._catalog_event(d, "")

    async def _catalog_event(self, d, text) -> Result:
        gpu = (int(d["fit_gpus"]), str(d.get("fit_gpu_type", ""))) if d.get("fit_gpus") and d.get("fit_gpu_type") else None
        def num(key):
            return float(d[key]) if str(d.get(key, "")).strip() else None
        items = self.inventory.search(str(d.get("query", "")), family=str(d.get("family", "")),
                                      max_params_b=num("max_params_b"), min_params_b=num("min_params_b"),
                                      vllm_only=bool(d.get("vllm_only")), favorites_only=bool(d.get("favorites_only")),
                                      fits_gpu=gpu)
        payload = {"items": [e.public_dict() | {"favorite": e.id in self.inventory.favorites} for e in items[:300]],
                   "total": len(self.inventory.entries), "families": self.inventory.families(),
                   "recent": self.inventory.recent}
        return text or f"{len(items)} of {len(self.inventory.entries)} model(s) match.", [("catalog", payload)]

    async def do_catalog_favorite(self, d) -> Result:
        state = self.inventory.toggle_favorite(str(d.get("model_id", "")))
        return ("Added to favorites." if state else "Removed from favorites."), []

    # ---- workloads ----------------------------------------------------------------------
    async def do_name_suggest(self, d) -> Result:
        if d.get("locked") and d.get("current"):
            return "Job name is locked.", [("job_name", {"name": validate_job_name(str(d["current"])), "locked": True})]
        name = generate_job_name(model=str(d.get("model", "")), activity=str(d.get("activity", "")),
                                 project=self._project_name(), existing=self._taken_names())
        return "", [("job_name", {"name": name, "locked": False})]

    def _prepare(self, d) -> tuple[WorkloadSpec, dict[str, Any]]:
        spec = self._spec_from_payload(d)
        if not spec.job_name:
            spec.job_name = generate_job_name(model=spec.model, activity=spec.application or "run",
                                              project=spec.project, existing=self._taken_names())
        decision = classify_command(spec.command)
        return spec, {"verdict": decision.verdict, "explanation": decision.explanation()}

    async def do_workload_preview(self, d) -> Result:
        spec, safety = self._prepare(d)
        problems = spec.validate()
        script = spec.to_job_spec().script() if not problems else ""
        return ("Review the script before submitting." if not problems else "Fix the listed problems before submitting."), [
            ("workload_preview", {"spec": spec.to_dict(), "problems": problems, "script": script,
                                  "safety": safety, "note": "This runs as a compute job, never on the login node."})]

    async def _submit(self, spec: WorkloadSpec, d, parent: str = "") -> Result:
        job_spec = spec.to_job_spec()
        job_id = await SlurmBackend(self._gateway_from_payload(d)).submit(job_spec)
        record = self.runs.record_submission(spec, job_id, parent_run_id=parent)
        self.bridge.job_history.record_submission(job_id, job_spec)
        self.bridge.last_job_id = job_id
        project = self.bridge.project_registry.current()
        if project:
            project.link("job", job_id, active=True)
        self.bridge.persist_recovery_state()
        if spec.model:
            self.inventory.mark_used(spec.model)
        self.log.info("job", "submitted workload", job_id=job_id, name=spec.job_name)
        return (f"Submitted job {job_id} ({spec.job_name}) to the scheduler. Nothing ran on the login node.",
                [("run", record.__dict__), ("context", self.context())])

    async def do_workload_submit(self, d) -> Result:
        spec, _ = self._prepare(d)
        return await self._submit(spec, d)

    async def do_runs_list(self, d) -> Result:
        items = [r.__dict__ for r in self.runs.list(str(d["project"]) if d.get("project") else None)]
        return f"{len(items)} recorded run(s).", [("runs", {"items": items})]

    async def do_run_duplicate(self, d) -> Result:
        spec = self.runs.duplicate_spec(str(d.get("run_id", "")), job_name="")
        return "Run loaded into the workload editor. Modify it and submit to rerun.", [("workload_loaded", {"spec": spec.to_dict()})]

    async def do_run_rerun(self, d) -> Result:
        spec = self.runs.duplicate_spec(str(d.get("run_id", "")), job_name="")
        spec.job_name = generate_job_name(model=spec.model, activity=spec.application or "run", project=spec.project,
                                          existing=self._taken_names())
        return await self._submit(spec, d, parent=str(d["run_id"]))

    async def do_run_export(self, d) -> Result:
        return "Run manifest ready.", [("run_manifest", {"run_id": d.get("run_id"), "manifest": self.runs.export_manifest(str(d.get("run_id", "")))})]

    async def do_config_save(self, d) -> Result:
        self.extras.save_config(self._project_name(), str(d.get("name", "")), self._spec_from_payload(d))
        return "Configuration saved to the project.", []

    async def do_config_list(self, d) -> Result:
        return "", [("configs", {"items": self.extras.configs(self._project_name())})]

    async def do_config_load(self, d) -> Result:
        spec = self.extras.load_config(self._project_name(), str(d.get("name", "")))
        return "Configuration loaded.", [("workload_loaded", {"spec": spec.to_dict()})]

    async def do_note_add(self, d) -> Result:
        self.extras.add_note(self._project_name(), str(d.get("text", "")))
        return "Note saved.", [("notes", {"items": self.extras.notes(self._project_name())})]

    async def do_notes_list(self, d) -> Result:
        return "", [("notes", {"items": self.extras.notes(self._project_name())})]

    # ---- terminal -----------------------------------------------------------------------
    def _command_payload(self, proposal) -> tuple[str, dict[str, Any]]:
        return "command", proposal.public_dict()

    async def do_command_propose(self, d) -> Result:
        proposal = self.terminal.propose(str(d.get("command", "")), origin=str(d.get("origin") or "user"),
                                         context=self.context()["environment"])
        events = [self._command_payload(proposal)]
        if proposal.verdict == "heavy":
            spec = WorkloadSpec(project=self._project_name(), allocation=self._allocation(d), command=proposal.command,
                                application="batch", partition=str(d.get("job_partition", "")), walltime="01:00:00",
                                cpus=4, job_name=generate_job_name(activity="command", project=self._project_name(),
                                                                   existing=self._taken_names()))
            self.terminal.redirect_to_job(proposal.id, spec.to_dict())
            events = [self._command_payload(proposal)]
        return proposal.explanation, events

    async def do_command_run(self, d) -> Result:
        proposal = await self.terminal.run(str(d.get("command_id", "")), d, confirmed=bool(d.get("confirmed")))
        self.log.debug("command", "executed", command_id=proposal.id, status=proposal.status, code=proposal.returncode)
        return f"Command {proposal.status}.", [self._command_payload(proposal)]

    async def do_command_cancel(self, d) -> Result:
        return "Cancelled.", [self._command_payload(self.terminal.cancel(str(d.get("command_id", ""))))]

    async def do_command_reject(self, d) -> Result:
        return "Command rejected.", [self._command_payload(self.terminal.reject(str(d.get("command_id", ""))))]

    async def do_commands_history(self, d) -> Result:
        return "", [("commands", {"items": self.terminal.history()})]

    # ---- hybrid planning ----------------------------------------------------------------
    async def do_plan_assist(self, d) -> Result:
        from model_providers import build_provider
        provider_name = str(d.get("provider") or "custom")
        endpoint, key, model = str(d.get("endpoint", "")).rstrip("/"), str(d.get("key", "")), str(d.get("model", "")).strip()
        if not key or not model or not endpoint:
            raise ValueError("Choose a provider, model and API key to plan with.")
        self.bridge.remember_secret(key)
        self.log.secrets.add(key)
        ctx = planner.build_context(
            allocations=self.discovered.get("accounts", []), partitions=self.discovered.get("summary", []),
            active_jobs=self.context()["active_jobs"])
        result = await build_provider(self.bridge.http, provider_name, endpoint, key).complete(
            planner.build_request(model, str(d.get("goal", "")), ctx))
        plan = planner.parse_plan(result["choices"][0]["message"].get("content") or "")
        origin = "assistant" if provider_name.startswith("arc") or provider_name == "managed" else "external-model"
        proposals = []
        for command in plan["commands"]:
            proposal = self.terminal.propose(command, origin=origin, context=self.context()["environment"])
            proposals.append(proposal.public_dict())
        where = "ARC-hosted model" if origin == "assistant" else f"external provider ({provider_name})"
        return (f"Plan drafted by {where}. Review each proposed command; none has run.",
                [("plan", {"explanation": plan["explanation"], "proposals": proposals, "inference_location": where})])

    # ---- support / diagnostics ----------------------------------------------------------
    async def do_report_build(self, d) -> Result:
        b = self.bridge
        fields = build_report(
            stage=str(d.get("stage") or self.context().get("arc_session") or ""), job_id=str(d.get("job_id") or b.last_job_id or ""),
            model=str(d.get("model", "")), resource=str(d.get("resource", "")),
            log_excerpt="\n".join(json.dumps(r) for r in self.log.recent(40)),
            traceback=str(d.get("traceback", "")), config={"profile": b.profile.id},
            secrets=sorted(b.secret_values))
        return "Review the report and remove anything you do not want to share.", [("report", {"fields": fields, "preview": finalize(fields)})]

    async def do_log_level(self, d) -> Result:
        self.log.set_level(str(d.get("level", "normal")))
        return f"Log level: {self.log.level}.", []

    async def do_log_recent(self, d) -> Result:
        return "", [("app_log", {"items": self.log.recent(int(d.get("limit") or 200), str(d.get("category", ""))),
                                 "level": self.log.level})]

    async def do_context_status(self, d) -> Result:
        return "", [("context", self.context())]
