import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from artifacts import ArtifactStore, PipelineGraph, PipelineStep, safe_workspace_path
from config import get_profile
from context_window import bounded_history, estimate_tokens, truncate_text
from helper import Bridge
from jobs import JobHistory, JobSpec, RESOURCE_PROFILES, SlurmBackend, SshCommandGateway, get_resource_profile
from model_providers import ArcDedicatedModelProvider, EndpointPolicy, ModelCatalog, build_provider
from protocol import CommandEnvelope, ReplayCache
from security import redact_structure, redact_text
from services import EndpointRegistry, SshTunnel, VllmServiceSpec


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


class DedicatedArcProviderTests(unittest.TestCase):
    def test_dedicated_provider_accepts_only_arc_https_hosts(self):
        endpoint = EndpointPolicy.validate_arc_dedicated("https://ood.arc.vt.edu/node/fal001/session/api/v1")
        self.assertEqual(endpoint, "https://ood.arc.vt.edu/node/fal001/session/api/v1")
        endpoint = EndpointPolicy.validate_arc_dedicated("https://fal001.arc.vt.edu:8443/v1")
        self.assertEqual(endpoint, "https://fal001.arc.vt.edu:8443/v1")
        for invalid in (
            "http://ood.arc.vt.edu/v1",
            "https://arc.vt.edu.evil.example/v1",
            "https://example.org/v1",
            "https://127.0.0.1/v1",
        ):
            with self.assertRaises(ValueError):
                EndpointPolicy.validate_arc_dedicated(invalid)

    def test_build_provider_has_first_class_arc_dedicated_type(self):
        provider = build_provider(object(), "arc_dedicated", "https://ood.arc.vt.edu/session/v1", "session-key")
        self.assertIsInstance(provider, ArcDedicatedModelProvider)
        self.assertEqual(provider.endpoint, "https://ood.arc.vt.edu/session/v1")


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
        with self.assertRaises(ValueError):
            PipelineGraph([
                PipelineStep("a", "python", inputs=("b",)),
                PipelineStep("b", "python", inputs=("a",)),
            ])

    def test_artifact_records_round_trip_without_contents(self):
        store = ArtifactStore()
        record = store.register("jobs/5123/stdout.txt", workspace="slurm", created_by="slurm-job:5123", metadata={"job_id":"5123"})
        restored = ArtifactStore.from_records(store.export_records())
        self.assertEqual(restored.get(record.id).metadata["job_id"], "5123")


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

    def test_resource_profiles_match_documented_falcon_partitions(self):
        expected = {
            "falcon-l40s-small": ("l40s_normal_q", "fal_l40s_normal_base", "l40s"),
            "falcon-a30-small": ("a30_normal_q", "fal_a30_normal_base", "a30"),
            "falcon-v100-small": ("v100_normal_q", "fal_v100_normal_base", "v100"),
            "falcon-t4-small": ("t4_normal_q", "fal_t4_normal_base", "t4"),
        }
        for profile_id, values in expected.items():
            profile = get_resource_profile(profile_id)
            self.assertEqual((profile.partition, profile.qos, profile.gpu_type), values)
            self.assertGreater(profile.gpus, 0)
        self.assertNotIn("falcon-cpu-small", RESOURCE_PROFILES)

    def test_job_history_persists_only_hashed_command_and_resource_metadata(self):
        history = JobHistory()
        spec = JobSpec(account="alloc", command="python secret_analysis.py --token dont-persist-me", gpus=1)
        record = history.record_submission("5123", spec)
        rendered = str(history.export_records())
        self.assertNotIn("dont-persist-me", rendered)
        self.assertNotIn("secret_analysis.py", rendered)
        self.assertEqual(len(record.command_sha256), 64)
        history.update("5123", state="RUNNING", node="fal036")
        history.link_artifact("5123", "artifact-0123456789abcdef")
        restored = JobHistory.from_records(history.export_records())
        self.assertEqual(restored.list()[0].node, "fal036")
        self.assertEqual(restored.list()[0].artifact_ids, ["artifact-0123456789abcdef"])


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
        self.assertIn("#SBATCH --qos=fal_l40s_normal_base", script)
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

    def test_endpoint_registry_is_non_secret_and_tracks_reachability(self):
        registry = EndpointRegistry()
        record = registry.upsert(
            provider="managed",
            model="gpt-oss-120b",
            endpoint="http://127.0.0.1:8000/v1",
            reachability="loopback_tunnel",
            job_id="5123",
            metadata={"remote_node": "fal036", "remote_port": 8000},
        )
        self.assertEqual(record.reachability, "loopback_tunnel")
        self.assertNotIn("key", str(record.public_dict()).lower())
        registry.remove_job("5123")
        self.assertEqual(registry.list(), [])
        with self.assertRaises(ValueError):
            registry.upsert(provider="managed", model="gpt", endpoint="http://127.0.0.1/v1", reachability="public")


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
