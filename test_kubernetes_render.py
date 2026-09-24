from __future__ import annotations

import json
from pathlib import Path
import unittest

from deploy.kubernetes.render import render_resources


def deployment_values():
    return {
        "environment": "dvlp",
        "tenant_id": "clahs",
        "namespace": "arc-chat",
        "gateway_host": "gateway.compute.example.edu",
        "static_origin": "https://students.compute.example.edu",
        "notebook_url": "https://notebooks.example.edu/lab/index.html",
        "gateway_image": "ghcr.io/fl2744/arc-chat-gateway@sha256:" + "a" * 64,
        "oidc_issuer_url": "https://login.example.edu/oidc",
        "oidc_redirect_url": "https://gateway.compute.example.edu/oauth2/callback",
        "oidc_client_id": "arc-chat-client",
        "email_domain": "example.edu",
        "ingress_class": "nginx",
        "cluster_issuer": "institutional-public",
        "tls_secret_name": "arc-chat-gateway-tls",
        "external_secret_store": "clahs-vault",
        "vault_secret_path": "clahs/arc-chat/gateway",
        "ingress_namespace": "ingress-nginx",
        "monitoring_namespace": "monitoring",
        "trusted_ingress_cidrs": ["10.30.0.0/16"],
        "database_egress_cidrs": ["10.40.8.12/32"],
        "oidc_egress_cidrs": ["10.50.0.0/24"],
        "replicas": 2,
        "resources": {
            "gateway": {
                "requests": {"cpu": "100m", "memory": "192Mi"},
                "limits": {"cpu": "1", "memory": "512Mi"},
            },
            "oauth2_proxy": {
                "requests": {"cpu": "50m", "memory": "64Mi"},
                "limits": {"cpu": "500m", "memory": "256Mi"},
            },
        },
        "storage": {"database": "external-postgresql", "gateway_persistent_volume": False},
    }


class KubernetesRendererTests(unittest.TestCase):
    def test_renders_proxy_gateway_tls_vault_network_and_availability_resources(self):
        resources = render_resources(deployment_values())
        kinds = [resource["kind"] for resource in resources]
        self.assertIn("Deployment", kinds)
        self.assertIn("ExternalSecret", kinds)
        self.assertIn("Ingress", kinds)
        self.assertIn("Certificate", kinds)
        self.assertIn("NetworkPolicy", kinds)
        self.assertIn("PodDisruptionBudget", kinds)
        deployment = next(resource for resource in resources if resource["kind"] == "Deployment")
        self.assertEqual(deployment["spec"]["replicas"], 2)
        self.assertEqual(deployment["metadata"]["labels"]["arc-chat.vt.edu/environment"], "dvlp")
        self.assertEqual(deployment["metadata"]["labels"]["arc-chat.vt.edu/tenant"], "clahs")
        proxy = next(container for container in deployment["spec"]["template"]["spec"]["containers"] if container["name"] == "oauth2-proxy")
        self.assertIn("--oidc-groups-claim=targetedMembership", proxy["args"])
        self.assertIn("--oidc-email-claim=mailPreferredAddress", proxy["args"])
        self.assertIn("--pass-access-token=false", proxy["args"])
        gateway = next(container for container in deployment["spec"]["template"]["spec"]["containers"] if container["name"] == "gateway")
        self.assertTrue(gateway["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(gateway["resources"]["requests"]["cpu"], "100m")
        secret = next(resource for resource in resources if resource["kind"] == "ExternalSecret")
        self.assertEqual(secret["spec"]["secretStoreRef"]["kind"], "ClusterSecretStore")
        certificate = next(resource for resource in resources if resource["kind"] == "Certificate")
        ingress = next(resource for resource in resources if resource["kind"] == "Ingress")
        self.assertEqual(certificate["metadata"]["name"], ingress["spec"]["tls"][0]["secretName"])

    def test_renderer_rejects_mutable_images_unrestricted_networks_and_insecure_origins(self):
        invalid = deployment_values()
        invalid["gateway_image"] = "ghcr.io/fl2744/arc-chat-gateway:latest"
        with self.assertRaisesRegex(ValueError, "immutable image"):
            render_resources(invalid)
        invalid = deployment_values()
        invalid["database_egress_cidrs"] = ["0.0.0.0/0"]
        with self.assertRaisesRegex(ValueError, "unrestricted"):
            render_resources(invalid)
        invalid = deployment_values()
        invalid["static_origin"] = "http://students.example.edu"
        with self.assertRaisesRegex(ValueError, "HTTPS origin"):
            render_resources(invalid)

    def test_renderer_normalizes_default_origin_port_and_rejects_notebook_query(self):
        values = deployment_values()
        values["static_origin"] = "https://STUDENTS.example.edu:443/"
        resources = render_resources(values)
        config_map = next(resource for resource in resources if resource["kind"] == "ConfigMap")
        self.assertEqual(config_map["data"]["STATIC_ALLOWED_ORIGINS"], "https://students.example.edu")

        values = deployment_values()
        values["notebook_url"] = "https://notebooks.example.edu/lab/index.html?token=unexpected"
        with self.assertRaisesRegex(ValueError, "notebook_url"):
            render_resources(values)

        values = deployment_values()
        values["oidc_redirect_url"] = "https://gateway.compute.example.edu:444/oauth2/callback"
        with self.assertRaisesRegex(ValueError, "oidc_redirect_url"):
            render_resources(values)

        values = deployment_values()
        values["oidc_issuer_url"] = "https://login.example.edu:444/oidc"
        with self.assertRaisesRegex(ValueError, "oidc_issuer_url"):
            render_resources(values)

    def test_renderer_requires_explicit_environment_resources_and_external_storage(self):
        values = deployment_values()
        values["environment"] = "production"
        with self.assertRaisesRegex(ValueError, "dvlp, pprd, or prod"):
            render_resources(values)

        values = deployment_values()
        values["environment"] = "prod"
        values["replicas"] = 1
        with self.assertRaisesRegex(ValueError, "at least two replicas"):
            render_resources(values)

        values = deployment_values()
        values["resources"]["gateway"]["requests"]["memory"] = "1Gi"
        with self.assertRaisesRegex(ValueError, "cannot exceed its limit"):
            render_resources(values)

        values = deployment_values()
        values["storage"]["gateway_persistent_volume"] = True
        with self.assertRaisesRegex(ValueError, "disable gateway persistent volumes"):
            render_resources(values)

    def test_environment_templates_are_valid_json_and_cannot_render_with_placeholders(self):
        root = Path(__file__).parent / "deploy" / "kubernetes" / "environments"
        for environment in ("dvlp", "pprd", "prod"):
            with self.subTest(environment=environment):
                values = json.loads((root / environment / "values.example.json").read_text(encoding="utf-8"))
                self.assertEqual(values["environment"], environment)
                with self.assertRaises(ValueError):
                    render_resources(values)


if __name__ == "__main__":
    unittest.main()
