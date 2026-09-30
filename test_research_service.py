import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import catalog
import planner
from applog import AppLog
from connection import validate_connection
from jobs import CommandResult, JobHistory
from research import ResearchService
from services import VllmServiceSpec
from terminal import CommandSurface

INVENTORY = """@@MODEL /common/data/models/meta/Llama-3.1-70B-Instruct
{"architectures":["LlamaForCausalLM"],"torch_dtype":"bfloat16","max_position_embeddings":131072}
@@INDEX
"total_size": 141107412992
@@LICENSE present
@@END
@@MODEL /common/data/models/custom/Mystery-7B
not json at all
@@INDEX
@@END
@@MODEL /common/data/models/Qwen3-8B-AWQ
{"architectures":["Qwen3ForCausalLM"],"quantization_config":{"quant_method":"awq"},"max_position_embeddings":40960}
@@INDEX
@@END
"""


def run(coro):
    return asyncio.run(coro)


def flat(script):
    """Collapse shell line continuations so assertions see 'flag value' pairs."""
    return " ".join(script.replace("\\\n", " ").split())


class FakeGateway:
    def __init__(self, results=None, default=None):
        self.results, self.default, self.calls = results or {}, default or CommandResult("", "", 0), []

    async def run_detailed(self, command, *, stdin="", timeout=30.0):
        self.calls.append(command)
        return self.results.get(command, self.default)

    async def run(self, command, *, stdin="", timeout=30.0):
        self.calls.append(command)
        r = self.results.get(command, self.default)
        if r.returncode:
            raise RuntimeError(r.stderr)
        return r.stdout.strip()


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.entries = catalog.parse_inventory(INVENTORY)

    def test_parse_uses_real_metadata_only(self):
        by_id = {e.id: e for e in self.entries}
        llama = by_id["meta/Llama-3.1-70B-Instruct"]
        self.assertEqual((llama.params_b, llama.weights_gb, llama.context_length, llama.vllm), (70.0, 141.1, 131072, "likely"))
        self.assertIn("LICENSE file", llama.license_note)
        mystery = by_id["custom/Mystery-7B"]
        self.assertEqual((mystery.vllm, mystery.weights_gb, mystery.context_length), ("unknown", None, None))
        self.assertEqual(by_id["Qwen3-8B-AWQ"].quantization, "awq")

    def test_memory_guidance(self):
        llama = next(e for e in self.entries if "Llama" in e.id)
        self.assertEqual(llama.min_gpus(48), 4)
        self.assertTrue(any("Needs about" in w for w in llama.compatibility_warnings(gpus=1, gpu_type="l40s")))
        self.assertFalse(any("Needs about" in w for w in llama.compatibility_warnings(gpus=4, gpu_type="l40s")))
        awq = next(e for e in self.entries if "AWQ" in e.id)
        self.assertEqual(awq.memory_estimate_gb(), round(8 * 0.5 * 1.2 + 2, 1))

    def test_search_filters_favorites_recents(self):
        with tempfile.TemporaryDirectory() as d:
            inv = catalog.ModelInventory(self.entries, Path(d) / "c.json")
            self.assertEqual([e.id for e in inv.search("llama")], ["meta/Llama-3.1-70B-Instruct"])
            self.assertEqual(len(inv.search(vllm_only=True)), 2)
            self.assertEqual([e.id for e in inv.search(fits_gpu=(1, "l40s"))], ["custom/Mystery-7B", "Qwen3-8B-AWQ"])
            self.assertNotIn("meta/Llama-3.1-70B-Instruct", [e.id for e in inv.search(fits_gpu=(1, "l40s"))])
            self.assertTrue(inv.toggle_favorite("Qwen3-8B-AWQ"))
            inv.mark_used("Qwen3-8B-AWQ")
            again = catalog.ModelInventory(self.entries, Path(d) / "c.json")
            self.assertEqual(again.favorites, ["Qwen3-8B-AWQ"])
            self.assertEqual([e.id for e in again.search(favorites_only=True)], ["Qwen3-8B-AWQ"])
            self.assertEqual([e.id for e in again.recent_entries()], ["Qwen3-8B-AWQ"])
            with self.assertRaises(ValueError):
                inv.toggle_favorite("nope")


class TerminalTests(unittest.TestCase):
    def surface(self, **results):
        self.gw = FakeGateway(results)
        return CommandSurface(lambda d: self.gw)

    def test_requires_confirmation_and_runs_light(self):
        s = self.surface(**{"squeue -u me": CommandResult("\x1b[31mJOBID\x1b[0m\n", "warn", 0)})
        p = s.propose("squeue -u me", origin="external-model")
        with self.assertRaisesRegex(ValueError, "confirm"):
            run(s.run(p.id, {}, confirmed=False))
        self.assertEqual(self.gw.calls, [])
        done = run(s.run(p.id, {}, confirmed=True))
        self.assertEqual((done.status, done.stdout, done.stderr), ("done", "JOBID\n", "warn"))
        with self.assertRaisesRegex(ValueError, "already"):
            run(s.run(p.id, {}, confirmed=True))

    def test_heavy_never_runs_on_login_node(self):
        s = self.surface()
        p = s.propose("python train.py")
        self.assertEqual(p.verdict, "heavy")
        with self.assertRaisesRegex(ValueError, "compute job"):
            run(s.run(p.id, {}, confirmed=True))
        self.assertEqual(self.gw.calls, [])
        self.assertEqual(s.redirect_to_job(p.id, {"command": p.command}).status, "redirected")

    def test_compute_context_allows_heavy_and_failure_recorded(self):
        s = self.surface(**{"python x.py": CommandResult("", "boom", 3)})
        p = s.propose("python x.py", context="compute")
        self.assertEqual(run(s.run(p.id, {}, confirmed=True)).status, "failed")

    def test_cancel_and_history(self):
        s = self.surface()
        p = s.propose("ls")
        self.assertEqual(s.cancel(p.id).status, "cancelled")
        self.assertEqual(s.reject(p.id).status, "cancelled")
        self.assertEqual(len(s.history()), 1)
        with self.assertRaises(ValueError):
            s.propose("  ")


class ConnectionTests(unittest.TestCase):
    def check(self, results, user="jdoe", allocation=""):
        gw = FakeGateway(results)
        return run(validate_connection(user, "falcon2.arc.vt.edu", lambda u, h: gw, expected_allocation=allocation))

    def test_email_username_fails_first_stage(self):
        r = self.check({}, user="jdoe@vt.edu")
        self.assertEqual((r["ok"], r["failed_stage"]), (False, "username"))

    def test_auth_failure_is_translated(self):
        r = self.check({"hostname && whoami": CommandResult("", "Permission denied (publickey)", 255)})
        self.assertEqual(r["failed_stage"], "network_and_auth")
        self.assertIn("registered", r["stages"][-1]["message"])

    def test_full_success_and_missing_allocation(self):
        ok = {"hostname && whoami": CommandResult("falcon2\njdoe\n", "", 0),
              'sacctmgr -nP show assoc user="$USER" format=account': CommandResult("acct1\nacct2\n", "", 0)}
        r = self.check(ok)
        self.assertTrue(r["ok"])
        self.assertEqual(r["allocations"], ["acct1", "acct2"])
        bad = self.check(ok, allocation="other")
        self.assertEqual(bad["failed_stage"], "account_access")
        wrong_user = self.check({"hostname && whoami": CommandResult("falcon2\nsomeone\n", "", 0)})
        self.assertEqual(wrong_user["failed_stage"], "network_and_auth")


class LogAndPlannerTests(unittest.TestCase):
    def test_levels_and_redaction(self):
        log = AppLog("normal")
        log.secrets.add("hunter2-secret")
        log.info("x", "hello hunter2-secret Authorization: Bearer abcdefgh12345678 api_key=zzzz1234", password="p")
        log.debug("x", "hidden")
        (rec,) = log.recent()
        self.assertNotIn("hunter2-secret", rec["message"])
        self.assertNotIn("abcdefgh12345678", rec["message"])
        self.assertNotIn("zzzz1234", rec["message"])
        self.assertEqual(rec["password"], "[redacted]")
        log.set_level("debug")
        self.assertIsNotNone(log.debug("x", "shown"))
        with self.assertRaises(ValueError):
            log.set_level("loud")

    def test_planner_parses_and_bounds_commands(self):
        text = "Check the queue first.\n```bash\nsinfo\n```\nThen submit.\n```bash\nsbatch job.sh\n```"
        plan = planner.parse_plan(text)
        self.assertEqual(plan["commands"], ["sinfo", "sbatch job.sh"])
        self.assertNotIn("```", plan["explanation"])
        with self.assertRaises(ValueError):
            planner.build_request("m", " ", "ctx")
        ctx = planner.build_context(allocations=["a"], partitions=[{"partition": "p", "idle_nodes": 2}], active_jobs=[])
        self.assertIn("Partition p", ctx)


class VllmAdvancedTests(unittest.TestCase):
    base = dict(account="a", model_path="/common/data/models/m", served_model_name="m", gpus=4)

    def test_advanced_arguments_render(self):
        spec = VllmServiceSpec(**self.base, tensor_parallel_size=2, quantization="awq", gpu_memory_utilization=0.85,
                               extra_args=("--enforce-eager", "--dtype=bfloat16"), env_vars=(("VLLM_LOGGING_LEVEL", "DEBUG"),))
        script = flat(spec.job_spec(api_key="k" * 20).script())
        for expected in ("--tensor-parallel-size 2", "--quantization awq", "--gpu-memory-utilization 0.85",
                         "--enforce-eager", "--dtype=bfloat16", "export VLLM_LOGGING_LEVEL=DEBUG"):
            self.assertIn(expected, script)

    def test_rejects_unsafe_or_reserved(self):
        for bad in (dict(extra_args=("--api-key=x",)), dict(extra_args=("$(rm -rf ~)",)), dict(extra_args=("a;b",)),
                    dict(tensor_parallel_size=8), dict(gpu_memory_utilization=1.5), dict(quantization="a b"),
                    dict(env_vars=(("HF_TOKEN", "x"),)), dict(env_vars=(("A-B", "x"),)), dict(env_vars=(("A", "x y"),))):
            with self.assertRaises(ValueError, msg=str(bad)):
                VllmServiceSpec(**self.base, **bad)

    def test_defaults_unchanged(self):
        self.assertIn("--tensor-parallel-size 4", flat(VllmServiceSpec(**self.base).job_spec(api_key="k" * 20).script()))


class ResearchServiceTests(unittest.TestCase):
    def bridge(self):
        project = SimpleNamespace(manifest=SimpleNamespace(name="mali"), linked=[],
                                  link=lambda kind, rid, active=False: project.linked.append((kind, rid)))
        return SimpleNamespace(
            profile=SimpleNamespace(resolved_allocation=lambda: "acct1", id="p"), project_registry=SimpleNamespace(current=lambda: project),
            job_history=JobHistory(), last_job_id="", kernel=None, secret_values={"sekret-value"},
            state_machine=SimpleNamespace(state=SimpleNamespace(name="ARC_READY")), persist_recovery_state=lambda: None,
            remember_secret=lambda v: None, _project=project)

    def service(self, d, gateway):
        b = self.bridge()
        svc = ResearchService(b, Path(d))
        svc._gateway_from_payload = lambda payload: gateway
        return svc, b

    def test_submit_records_run_and_rerun_links_parent(self):
        with tempfile.TemporaryDirectory() as d:
            gw = FakeGateway({"sbatch --parsable": CommandResult("4242\n", "", 0)})
            svc, b = self.service(d, gw)
            payload = {"workload": {"partition": "l40s_normal_q", "gpus": 1, "gpu_type": "l40s", "command": "python run.py",
                                    "model": "meta/Llama-3", "application": "inference"}}
            text, events = run(svc.handle("workload_submit", payload))
            self.assertIn("4242", text)
            self.assertIn("Nothing ran on the login node", text)
            (run_rec,) = svc.runs.list()
            self.assertEqual((run_rec.job_id, run_rec.spec["allocation"], run_rec.project), ("4242", "acct1", "mali"))
            self.assertRegex(run_rec.spec["job_name"], r"^llama-3-inference-\d{4}$")
            self.assertIn(("job", "4242"), b._project.linked)
            gw.results["sbatch --parsable"] = CommandResult("4243\n", "", 0)
            run(svc.handle("run_rerun", {"run_id": run_rec.run_id}))
            newest = svc.runs.list()[0]
            self.assertEqual((newest.job_id, newest.parent_run_id), ("4243", run_rec.run_id))
            self.assertNotEqual(newest.spec["job_name"], run_rec.spec["job_name"])

    def test_invalid_workload_does_not_submit(self):
        with tempfile.TemporaryDirectory() as d:
            gw = FakeGateway()
            svc, _ = self.service(d, gw)
            text, events = run(svc.handle("workload_preview", {"workload": {"command": "ls"}}))
            self.assertTrue(events[0][1]["problems"])
            with self.assertRaises(ValueError):
                run(svc.handle("workload_submit", {"workload": {"command": "ls"}}))
            self.assertEqual(gw.calls, [])

    def test_heavy_command_proposal_redirects_with_job_plan(self):
        with tempfile.TemporaryDirectory() as d:
            svc, _ = self.service(d, FakeGateway())
            text, events = run(svc.handle("command_propose", {"command": "vllm serve x"}))
            self.assertIn("compute job", text)
            payload = events[0][1]
            self.assertEqual((payload["verdict"], payload["status"]), ("heavy", "redirected"))
            self.assertEqual(payload["redirect_spec"]["command"], "vllm serve x")
            with self.assertRaises(ValueError):
                run(svc.handle("command_run", {"command_id": payload["id"], "confirmed": True}))

    def test_discovery_and_recommendation_use_live_output(self):
        with tempfile.TemporaryDirectory() as d:
            sinfo = "l40s_normal_q*|up|4|idle|gpu:l40s:4|64|512000\n"
            gw = FakeGateway({
                "sinfo -h -o '%P|%a|%D|%T|%G|%c|%m'": CommandResult(sinfo, "", 0),
                "squeue -h -t PENDING -o '%P|%i'": CommandResult("l40s_normal_q|1\n", "", 0),
                "squeue -h -u \"$USER\" -o '%i|%T|%N|%R|%j'": CommandResult("", "", 0),
                'sacctmgr -nP show assoc user="$USER" format=account': CommandResult("acct1\n", "", 0)})
            svc, _ = self.service(d, gw)
            with self.assertRaisesRegex(ValueError, "Inspect ARC resources first"):
                run(svc.handle("resource_recommend", {"gpus": 1}))
            _, events = run(svc.handle("arc_discover", {}))
            self.assertEqual(events[0][1]["partitions"][0]["pending_jobs"], 1)
            _, rec = run(svc.handle("resource_recommend", {"gpus": 2}))
            self.assertEqual(rec[0][1]["items"][0]["partition"], "l40s_normal_q")

    def test_report_never_contains_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            svc, _ = self.service(d, FakeGateway())
            svc.log.info("x", "token is sekret-value here")
            _, events = run(svc.handle("report_build", {"traceback": "oops sekret-value"}))
            self.assertNotIn("sekret-value", events[0][1]["preview"])

    def test_context_reports_login_environment(self):
        with tempfile.TemporaryDirectory() as d:
            svc, b = self.service(d, FakeGateway())
            ctx = svc.context()
            self.assertEqual((ctx["environment"], ctx["allocation"], ctx["authenticated"]), ("login", "acct1", False))
            b.kernel = object()
            self.assertEqual(svc.context()["environment"], "compute")

    def test_name_suggest_respects_lock(self):
        with tempfile.TemporaryDirectory() as d:
            svc, _ = self.service(d, FakeGateway())
            _, ev = run(svc.handle("name_suggest", {"locked": True, "current": "my-name"}))
            self.assertEqual(ev[0][1], {"name": "my-name", "locked": True})
            _, ev = run(svc.handle("name_suggest", {"model": "a/B-7B", "activity": "infer"}))
            self.assertRegex(ev[0][1]["name"], r"^b-7b-infer-\d{4}$")


if __name__ == "__main__":
    unittest.main()
