import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import discovery
import envcompat
import naming
import safety
import sshkeys
from bugreport import build_report, finalize
from runs import RunHistory
from workload import WorkloadSpec

NOW = dt.datetime(2026, 9, 30, 12, 0)


class NamingTests(unittest.TestCase):
    def test_generates_from_model_and_dedupes(self):
        name = naming.generate_job_name(model="meta-llama/Llama 70B", activity="inference", now=NOW)
        self.assertEqual(name, "llama-70b-inference-0930")
        self.assertEqual(naming.generate_job_name(model="meta-llama/Llama 70B", activity="inference",
                                                  existing=[name], now=NOW), name + "-02")

    def test_sanitizes_and_validates(self):
        self.assertEqual(naming.sanitize("My Sim! #4"), "my-sim-4")
        with self.assertRaises(ValueError):
            naming.validate_job_name("bad name")

    def test_namer_follows_context_until_locked(self):
        namer = JobNamerFactory()
        first = namer.update(model="a/one", activity="run", now=NOW)
        self.assertEqual(namer.update(model="a/two", activity="run", now=NOW), first.replace("one", "two"))
        namer.set_manual("custom")
        self.assertEqual(namer.update(model="a/three", now=NOW), "custom")


def JobNamerFactory():
    return naming.JobNamer()


class SafetyTests(unittest.TestCase):
    def test_light_commands(self):
        for cmd in ["squeue -u me", "ls -la /scratch", "sbatch job.sh", "sbatch --wrap 'python x.py'", "FOO=1 sinfo"]:
            self.assertTrue(safety.classify_command(cmd).allowed_on_login_node, cmd)

    def test_heavy_commands_redirected(self):
        for cmd in ["python train.py", "vllm serve model", "ls && python3 x.py", "/usr/bin/torchrun a.py", "tar czf a.tgz big/"]:
            d = safety.classify_command(cmd)
            self.assertEqual(d.verdict, safety.HEAVY, cmd)
            self.assertIn("compute job", d.explanation())

    def test_unknown_is_not_allowed(self):
        d = safety.classify_command("mystery-tool --go")
        self.assertEqual(d.verdict, safety.UNKNOWN)
        self.assertFalse(d.allowed_on_login_node)


class WorkloadTests(unittest.TestCase):
    def spec(self, **kw):
        base = dict(project="p", job_name="llama-0930", allocation="myalloc", partition="l40s_normal_q",
                    gpus=1, gpu_type="l40s", command="python run.py", modules=["Python/3.11"],
                    working_directory="/scratch/me", env_vars={"OMP_NUM_THREADS": "4"})
        base.update(kw)
        return WorkloadSpec(**base)

    def test_to_job_spec_builds_script(self):
        script = self.spec().to_job_spec().script()
        self.assertIn("#SBATCH --gres=gpu:l40s:1", script)
        self.assertIn("module load Python/3.11", script)
        self.assertIn("export OMP_NUM_THREADS=4", script)

    def test_validation_reports_problems(self):
        problems = WorkloadSpec().validate()
        self.assertTrue(any("allocation" in p for p in problems))
        with self.assertRaises(ValueError):
            WorkloadSpec().to_job_spec()

    def test_rejects_secret_env_vars(self):
        self.assertTrue(any("credential" in p for p in self.spec(env_vars={"HF_TOKEN": "x"}).validate()))

    def test_roundtrip_and_duplicate(self):
        s = self.spec()
        self.assertEqual(WorkloadSpec.from_dict(s.to_dict()), s)
        self.assertEqual(s.duplicate(gpus=2).gpus, 2)
        with self.assertRaises(ValueError):
            WorkloadSpec.from_dict({"schema_version": 99})


class RunHistoryTests(unittest.TestCase):
    def test_persists_and_reruns(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "runs.json"
            spec = WorkloadSpecHelper()
            history = RunHistory(path)
            rec = history.record_submission(spec, "12345")
            history.update(rec.run_id, status="COMPLETED", node="fal001")
            reloaded = RunHistory(path)
            self.assertEqual(reloaded.get(rec.run_id).status, "COMPLETED")
            self.assertEqual(reloaded.duplicate_spec(rec.run_id, gpus=2).gpus, 2)
            self.assertIn('"job_id": "12345"', reloaded.export_manifest(rec.run_id))
            with self.assertRaises(ValueError):
                reloaded.update(rec.run_id, spec={})
            with self.assertRaises(ValueError):
                reloaded.get("run_missing")


def WorkloadSpecHelper():
    return WorkloadSpec(project="p", job_name="j", allocation="a", partition="q", command="ls")


SINFO = "l40s_normal_q*|up|4|idle|gpu:l40s:4|64|512000\nl40s_normal_q*|up|6|mixed|gpu:l40s:4|64|512000\n" \
        "a30_normal_q|up|2|idle|gpu:a30:2|32|256000\nv100_normal_q|down|3|idle|gpu:v100:2|32|192000\nbadline\n"


class DiscoveryTests(unittest.TestCase):
    def test_parse_and_summarize(self):
        rows = discovery.parse_sinfo(SINFO)
        self.assertEqual(len(rows), 4)
        summary = {p["partition"]: p for p in discovery.summarize_partitions(rows, {"l40s_normal_q": 7})}
        self.assertEqual(summary["l40s_normal_q"]["idle_nodes"], 4)
        self.assertEqual(summary["l40s_normal_q"]["nodes_by_state"], {"idle": 4, "mixed": 6})
        self.assertEqual(summary["l40s_normal_q"]["pending_jobs"], 7)

    def test_recommend_explains_and_skips_down(self):
        rows = discovery.parse_sinfo(SINFO)
        recs = discovery.recommend(rows, gpus=2, gpu_memory_gb=80, gpu_memory_table={"l40s": 48, "a30": 24})
        self.assertEqual([r["partition"] for r in recs], ["l40s_normal_q", "a30_normal_q"])
        self.assertTrue(recs[0]["fits_memory"])
        self.assertFalse(recs[1]["fits_memory"])
        self.assertTrue(any("below" in r for r in recs[1]["reasons"]))

    def test_pending_and_accounts(self):
        self.assertEqual(discovery.parse_pending_counts("a|1\na|2\nb|3\n"), {"a": 2, "b": 1})
        self.assertEqual(discovery.parse_accounts("acct1\nacct1\n\nacct2\n"), ["acct1", "acct2"])


class SshKeyTests(unittest.TestCase):
    def test_username_validation(self):
        self.assertEqual(sshkeys.validate_arc_username("jdoe"), "jdoe")
        with self.assertRaisesRegex(ValueError, "email address"):
            sshkeys.validate_arc_username("jdoe@vt.edu")
        with self.assertRaises(ValueError):
            sshkeys.validate_arc_username("")

    @unittest.skipUnless(__import__("shutil").which("ssh-keygen"), "ssh-keygen not installed")
    def test_generate_key_is_protected_and_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            pair = sshkeys.generate_key(directory=Path(d))
            self.assertTrue(pair.public_key.startswith("ssh-ed25519 "))
            self.assertEqual(sshkeys.check_key_permissions(pair.private_path), [])
            self.assertEqual(sshkeys.list_existing_keys(Path(d)), [pair.private_path])
            with self.assertRaises(FileExistsError):
                sshkeys.generate_key(directory=Path(d))

    def test_generate_requires_ssh_keygen_and_valid_algorithm(self):
        with self.assertRaises(ValueError):
            sshkeys.generate_key(algorithm="dsa")
        with patch("sshkeys.shutil.which", return_value=None), self.assertRaisesRegex(RuntimeError, "ssh-keygen"):
            sshkeys.generate_key()

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_permission_check(self):
        with tempfile.TemporaryDirectory() as d:
            key = Path(d) / "k"
            key.write_text("x")
            key.chmod(0o644)
            self.assertTrue(any("chmod 600" in p for p in sshkeys.check_key_permissions(key)))

    def test_failure_translation(self):
        self.assertIn("registered", sshkeys.translate_ssh_failure("Permission denied (publickey)", "jdoe"))
        self.assertIn("VPN", sshkeys.translate_ssh_failure("ssh: connect: Connection timed out"))


class BugReportAndEnvTests(unittest.TestCase):
    def test_report_redacts_and_drops_secret_config(self):
        r = build_report(stage="launch", log_excerpt="token=abcd1234 failed", config={"api_key": "abcd1234", "port": 1},
                         secrets=["abcd1234"])
        self.assertNotIn("abcd1234", finalize(r))
        self.assertNotIn("api_key", r["configuration"])
        self.assertNotIn("launch", finalize(r, removed={"workflow_stage"}))

    def test_env_prefers_new_then_legacy(self):
        with patch.dict(os.environ, {"ARC_CHAT_ZZ": "old"}, clear=False):
            self.assertEqual(envcompat.getenv("ZZ"), "old")
            with patch.dict(os.environ, {"ARC_RESEARCH_ZZ": "new"}):
                self.assertEqual(envcompat.getenv("ZZ"), "new")
        self.assertEqual(envcompat.getenv("NOPE_XYZ", "d"), "d")


if __name__ == "__main__":
    unittest.main()
