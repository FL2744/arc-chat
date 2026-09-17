import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from artifacts import ArtifactStore, PipelineGraph, PipelineStep, safe_workspace_path
from config import get_profile
from context_window import bounded_history, estimate_tokens, truncate_text
from helper import Bridge
from jobs import JobSpec, SlurmBackend, SshCommandGateway
from model_providers import ModelCatalog
from protocol import CommandEnvelope, ReplayCache
from security import redact_structure, redact_text
from services import SshTunnel, VllmServiceSpec


class ProtocolTests(unittest.TestCase):
    def test_typed_and_legacy_commands_parse(self):
        typed = CommandEnvelope.parse({
            "version": 1,
            "id": "request-12345678",
            "type": "command",
            "action": "workspace.start",
            "payload": {"profile": "fl2744"},
        })
        self.assertEqual(typed.action, "workspace.start")
        self.assertEqual(typed.payload["profile"], "fl2744")
        legacy = CommandEnvelope.parse({"action": "doctor", "full": True})
        self.assertTrue(legacy.payload["full"])

    def test_protocol_rejects_wrong_version_and_bad_payload(self):
        with self.assertRaises(ValueError):
            CommandEnvelope.parse({"version": 2, "id": "request-12345678", "action": "doctor", "payload": {}})
        with self.assertRaises(ValueError):
            CommandEnvelope.parse({"version": 1, "id": "request-12345678", "action": "doctor", "payload": []})

    def test_replay_cache_is_bounded(self):
        cache = ReplayCache(limit=16)
        for i in range(20):
            cache.put(f"request-{i:08d}", {"value": i})
        self.assertIsNone(cache.get("request-00000000"))
        result = cache.get("request-00000019")
        result["value"] = -1
        self.assertEqual(cache.get("request-00000019")["value"], 19)


class SecurityTests(unittest.TestCase):
    def test_multiple_secrets_are_redacted(self):
        text = redact_text("token=alpha-secret and alpha", ["alpha", "alpha-secret"])
        self.assertNotIn("alpha", text)
        nested = redact_structure({"x": ["alpha-secret", "safe"]}, ["alpha-secret"])
        self.assertEqual(nested["x"][0], "[redacted]")


class ContextWindowTests(unittest.TestCase):
    def test_large_output_is_clipped_and_recent_history_survives(self):
        clipped = truncate_text("x" * 100_000, 4000)
        self.assertLessEqual(len(clipped), 4100)
        self.assertIn("omitted", clipped)
        history = [
            {"role": "user", "content": "old" * 50_000},
            {"role": "user", "content": "recent"},
        ]
        bounded = bounded_history(history, context_tokens=8192, output_reserve=1024)
        self.assertEqual(bounded[-1]["content"], "recent")
        self.assertGreater(estimate_tokens("hello world"), 0)

    def test_tool_pair_is_kept_together(self):
        history = [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "x"}]},
            {"role": "tool", "tool_call_id": "x", "content": "result"},
        ]
        roles = [item["role"] for item in bounded_history(history, context_tokens=8192, output_reserve=1024)]
        self.assertEqual(roles[-2:], ["assistant", "tool"])


class ArtifactTests(unittest.TestCase):
    def test_artifact_store_and_pipeline_contract(self):
        store = ArtifactStore()
        record = store.register("results/report.csv", workspace="ws-1", created_by="job-42")
        self.assertEqual(store.get(record.id).media_type, "text/csv")
        self.assertIn(record.id, store.export_json())
        graph = PipelineGraph([
            PipelineStep("clean", "python"),
            PipelineStep("analyze", "slurm", inputs=("clean",)),
        ])
        self.assertEqual(len(graph.steps), 2)
        with self.assertRaises(ValueError):
            safe_workspace_path("../secret")
        with self.assertRaises(ValueError):
            PipelineGraph([PipelineStep("x", "python", inputs=("missing",))])


class JobSpecTests(unittest.TestCase):
    def test_falcon_job_script_matches_documented_slurm_shape(self):
        spec = JobSpec(
            account="course_alloc",
            command="python analysis.py",
            partition="l40s_normal_q",
            walltime="01:00:00",
            cpus_per_task=8,
            gpus=1,
            gpu_type="l40s",
        )
        script = spec.script()
        self.assertIn("#SBATCH --account=course_alloc", script)
        self.assertIn("#SBATCH --partition=l40s_normal_q", script)
        self.assertIn("#SBATCH --gres=gpu:l40s:1", script)
        self.assertIn("set -euo pipefail", script)
        with self.assertRaises(ValueError):
            JobSpec(account="bad account", command="true")

    def test_ssh_gateway_restricts_login_hosts(self):
        gateway = SshCommandGateway("student", "falcon2.arc.vt.edu")
        self.assertEqual(gateway.host, "falcon2.arc.vt.edu")
        with self.assertRaises(ValueError):
            SshCommandGateway("student", "evil.example")


class FakeGateway:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def run(self, command, *, stdin="", timeout=30.0):
        self.calls.append((command, stdin, timeout))
        return self.responses.pop(0)


class SlurmBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_status_list_cancel_and_logs(self):
        gateway = FakeGateway([
            "5123;falcon",
            "5123|RUNNING|fal036|None",
            "5123|RUNNING|fal036|None|arc-chat",
            "",
            "JobId=5123 StdOut=/home/user/arc-chat-5123.log",
            "hello\nworld",
        ])
        backend = SlurmBackend(gateway)
        job_id = await backend.submit(JobSpec(account="alloc", command="hostname"))
        self.assertEqual(job_id, "5123")
        self.assertEqual((await backend.status(job_id))["node"], "fal036")
        self.assertEqual((await backend.list_active())[0]["name"], "arc-chat")
        await backend.cancel(job_id)
        self.assertIn("world", await backend.logs(job_id))
        self.assertEqual(gateway.calls[0][0], "sbatch --parsable")
        self.assertIn("#SBATCH", gateway.calls[0][1])


class VllmTests(unittest.TestCase):
    def test_vllm_spec_follows_arc_common_model_pattern(self):
        spec = VllmServiceSpec(
            account="alloc",
            model_path="/common/data/models/openai--gpt-oss-120b",
            served_model_name="gpt-oss-120b",
            reasoning_parser="openai_gptoss",
        )
        key = "0123456789abcdef0123456789abcdef"
        script = spec.job_spec(api_key=key).script()
        self.assertIn("module load vLLM", script)
        self.assertIn("/common/data/models/openai--gpt-oss-120b", script)
        self.assertIn("--tensor-parallel-size", script)
        self.assertIn("--tool-call-parser", script)
        self.assertIn("--reasoning-parser", script)
        self.assertIn(key, script)

    def test_vllm_rejects_non_common_model_and_tunnel_matches_docs(self):
        with self.assertRaises(ValueError):
            VllmServiceSpec(account="alloc", model_path="hf://openai/gpt", served_model_name="gpt")
        tunnel = SshTunnel("student", "fal036", 8000)
        command = " ".join(tunnel.argv())
        self.assertIn("8000:fal036:8000", command)
        self.assertIn("student@falcon2.arc.vt.edu", command)


class ManagedProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_managed_chat_requires_live_tunnel(self):
        bridge = Bridge()
        bridge.profile = get_profile("default")
        bridge.kernel = "kernel"
        bridge.vllm_service = SimpleNamespace(
            api_key="managed-secret-0000000000000000",
            port=8000,
            model="gpt-oss-120b",
            local_endpoint=lambda port: f"http://127.0.0.1:{port}/v1",
        )
        payload = {
            "provider": "managed",
            "endpoint": "http://127.0.0.1:8000/v1",
            "key": "",
            "model": "gpt-oss-120b",
            "advanced": True,
            "text": "hello",
        }
        with self.assertRaisesRegex(ValueError, "SSH tunnel"):
            await bridge.chat(payload)

        bridge.vllm_tunnel = SimpleNamespace(
            local_port=18000,
            process=SimpleNamespace(returncode=None),
        )
        bridge.emit = AsyncMock()

        class Provider:
            async def complete(self, body):
                return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        with patch("helper.build_provider", return_value=Provider()) as factory:
            self.assertEqual(await bridge.chat(payload), "Ready.")
        self.assertEqual(factory.call_args.args[2], "http://127.0.0.1:18000/v1")


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_discovery_keeps_documented_metadata(self):
        class Response:
            status = 200
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
            async def json(self): return {"data": [{"id": "gpt-oss-120b"}, {"id": "future-model"}]}
        class Http:
            def get(self, *args, **kwargs): return Response()
        catalog = await ModelCatalog.discover(Http(), "https://example.org/v1", "secret-key")
        self.assertEqual(catalog.get("gpt-oss-120b").context_tokens, 131072)
        self.assertEqual(catalog.get("future-model").capabilities, ("chat",))


if __name__ == "__main__":
    unittest.main()
