"""Render a fail-closed hosted gateway deployment from approved site values.

No credentials or institution-specific network assumptions are checked in.
The renderer requires immutable images, TLS origins, a preconfigured Vault-backed
ExternalSecret store, and explicit ingress/egress network ranges.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


OAUTH2_PROXY_IMAGE = (
    "quay.io/oauth2-proxy/oauth2-proxy:v7.15.4@"
    "sha256:b1b2021fe8f4004573e8d690dec6c7bb29cc44364572cf8510a05bf3a0ae2ded"
)
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
_IMAGE = re.compile(r"^[a-z0-9./_-]+@sha256:[0-9a-f]{64}$")
_SECRET_PATH = re.compile(r"^[A-Za-z0-9._/-]{1,240}$")
_CPU_QUANTITY = re.compile(r"^(?:[0-9]+(?:\.[0-9]+)?|[0-9]+m)$")
_MEMORY_QUANTITY = re.compile(r"^([1-9][0-9]*)(Ki|Mi|Gi|Ti)?$")


def _origin(value: Any, label: str) -> tuple[str, str]:
    parsed = urlsplit(str(value or "").strip())
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError(f"{label} must be an HTTPS origin without a path or credentials.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} has an invalid port.") from exc
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    authority = hostname if port in (None, 443) else f"{hostname}:{port}"
    return f"https://{authority}", parsed.hostname.lower()


def _https_url(value: Any, label: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} has an invalid port.") from exc
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or (label == "notebook_url" and (parsed.query or parsed.fragment))):
        raise ValueError(f"{label} must be an HTTPS URL without embedded credentials.")
    if label in {"oidc_issuer_url", "oidc_redirect_url"} and port not in (None, 443):
        raise ValueError(f"{label} must use HTTPS port 443.")
    return str(value).strip()


def _dns(value: Any, label: str) -> str:
    result = str(value or "").strip().lower().rstrip(".")
    if len(result) > 253 or not result or any(not _DNS_LABEL.fullmatch(part) for part in result.split(".")):
        raise ValueError(f"{label} must be a DNS name.")
    return result


def _cidrs(values: Any, label: str) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{label} must contain at least one explicit CIDR.")
    parsed = []
    for value in values:
        network = ipaddress.ip_network(str(value), strict=False)
        if network.prefixlen == 0:
            raise ValueError(f"{label} cannot contain an unrestricted /0 network.")
        parsed.append(str(network))
    return sorted(set(parsed))


def _resource_quantity(value: Any, label: str, *, cpu: bool) -> tuple[str, Decimal]:
    quantity = str(value or "").strip()
    if cpu:
        if not _CPU_QUANTITY.fullmatch(quantity):
            raise ValueError(f"{label} must be an explicit CPU quantity such as 100m or 1.")
        try:
            amount = Decimal(quantity[:-1]) / Decimal(1000) if quantity.endswith("m") else Decimal(quantity)
        except InvalidOperation as exc:
            raise ValueError(f"{label} is invalid.") from exc
    else:
        match = _MEMORY_QUANTITY.fullmatch(quantity)
        if not match:
            raise ValueError(f"{label} must be an explicit memory quantity using bytes, Ki, Mi, Gi, or Ti.")
        scale = {None: 1, "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}[match.group(2)]
        amount = Decimal(match.group(1)) * scale
    if amount <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return quantity, amount


def _container_resources(config: Any, name: str) -> dict[str, dict[str, str]]:
    if not isinstance(config, dict) or set(config) != {"requests", "limits"}:
        raise ValueError(f"resources.{name} must define requests and limits.")
    normalized: dict[str, dict[str, str]] = {}
    numeric: dict[str, dict[str, Decimal]] = {}
    for section in ("requests", "limits"):
        raw = config[section]
        if not isinstance(raw, dict) or set(raw) != {"cpu", "memory"}:
            raise ValueError(f"resources.{name}.{section} must explicitly define cpu and memory.")
        normalized[section] = {}
        numeric[section] = {}
        for resource, is_cpu in (("cpu", True), ("memory", False)):
            text, amount = _resource_quantity(raw[resource], f"resources.{name}.{section}.{resource}", cpu=is_cpu)
            normalized[section][resource] = text
            numeric[section][resource] = amount
    for resource in ("cpu", "memory"):
        if numeric["requests"][resource] > numeric["limits"][resource]:
            raise ValueError(f"resources.{name} request for {resource} cannot exceed its limit.")
    return normalized


def validate_config(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("Deployment configuration must be a JSON object.")
    required = {
        "environment", "tenant_id", "namespace", "gateway_host", "static_origin", "notebook_url", "gateway_image",
        "oidc_issuer_url", "oidc_redirect_url", "oidc_client_id", "email_domain",
        "ingress_class", "cluster_issuer", "tls_secret_name", "external_secret_store", "vault_secret_path",
        "ingress_namespace", "monitoring_namespace", "trusted_ingress_cidrs",
        "database_egress_cidrs", "oidc_egress_cidrs", "replicas", "resources", "storage",
    }
    missing = sorted(required - set(config))
    if missing:
        raise ValueError("Missing deployment settings: " + ", ".join(missing))
    value = dict(config)
    value["environment"] = str(value["environment"]).strip().lower()
    if value["environment"] not in {"dvlp", "pprd", "prod"}:
        raise ValueError("environment must be one of dvlp, pprd, or prod.")
    tenant_id = str(value["tenant_id"]).strip().lower()
    if len(tenant_id) > 63 or not _DNS_LABEL.fullmatch(tenant_id):
        raise ValueError("tenant_id must be a Kubernetes label-compatible DNS label.")
    value["tenant_id"] = tenant_id
    replicas = value["replicas"]
    if not isinstance(replicas, int) or isinstance(replicas, bool) or not 1 <= replicas <= 10:
        raise ValueError("replicas must be an explicitly configured integer from 1 through 10.")
    if value["environment"] in {"pprd", "prod"} and replicas < 2:
        raise ValueError("pprd and prod require at least two replicas.")
    value["resources"] = {
        "gateway": _container_resources(value["resources"].get("gateway") if isinstance(value["resources"], dict) else None, "gateway"),
        "oauth2_proxy": _container_resources(value["resources"].get("oauth2_proxy") if isinstance(value["resources"], dict) else None, "oauth2_proxy"),
    }
    if set(config["resources"]) != {"gateway", "oauth2_proxy"}:
        raise ValueError("resources must define exactly gateway and oauth2_proxy containers.")
    storage = value["storage"]
    if (not isinstance(storage, dict) or set(storage) != {"database", "gateway_persistent_volume"}
            or storage.get("database") != "external-postgresql"
            or storage.get("gateway_persistent_volume") is not False):
        raise ValueError("storage must use external-postgresql and disable gateway persistent volumes.")
    for field in ("namespace", "ingress_namespace", "monitoring_namespace"):
        value[field] = _dns(value[field], field)
    value["gateway_host"] = _dns(value["gateway_host"], "gateway_host")
    value["static_origin"], value["static_host"] = _origin(value["static_origin"], "static_origin")
    value["notebook_url"] = _https_url(value["notebook_url"], "notebook_url")
    value["oidc_issuer_url"] = _https_url(value["oidc_issuer_url"], "oidc_issuer_url")
    value["oidc_redirect_url"] = _https_url(value["oidc_redirect_url"], "oidc_redirect_url")
    callback = urlsplit(value["oidc_redirect_url"])
    if (callback.hostname.lower() != value["gateway_host"] or callback.port not in (None, 443)
            or callback.path != "/oauth2/callback" or callback.query or callback.fragment):
        raise ValueError("oidc_redirect_url must be https://gateway_host/oauth2/callback.")
    if not _IMAGE.fullmatch(str(value["gateway_image"])):
        raise ValueError("gateway_image must be an immutable image reference ending in @sha256:<64 lowercase hex characters>.")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", str(value["oidc_client_id"])):
        raise ValueError("oidc_client_id is invalid.")
    email_domain = str(value["email_domain"]).strip().lower().lstrip("@").rstrip(".")
    value["email_domain"] = _dns(email_domain, "email_domain")
    for field in ("ingress_class", "cluster_issuer", "tls_secret_name", "external_secret_store"):
        value[field] = _dns(value[field], field)
    if not _SECRET_PATH.fullmatch(str(value["vault_secret_path"])):
        raise ValueError("vault_secret_path must be an explicit non-secret path.")
    value["trusted_ingress_cidrs"] = _cidrs(value["trusted_ingress_cidrs"], "trusted_ingress_cidrs")
    value["database_egress_cidrs"] = _cidrs(value["database_egress_cidrs"], "database_egress_cidrs")
    value["oidc_egress_cidrs"] = _cidrs(value["oidc_egress_cidrs"], "oidc_egress_cidrs")
    return value


def _metadata(name: str, namespace: str, labels: dict[str, str] | None = None) -> dict[str, Any]:
    return {"name": name, "namespace": namespace, "labels": labels or {"app.kubernetes.io/name": "arc-chat-gateway"}}


def render_resources(config: Any) -> list[dict[str, Any]]:
    value = validate_config(config)
    namespace = value["namespace"]
    labels = {
        "app.kubernetes.io/name": "arc-chat-gateway",
        "app.kubernetes.io/part-of": "arc-chat",
        "arc-chat.vt.edu/environment": value["environment"],
        "arc-chat.vt.edu/tenant": value["tenant_id"],
    }
    selectors = {"app.kubernetes.io/name": "arc-chat-gateway"}
    trusted_proxy_cidrs = ",".join(value["trusted_ingress_cidrs"])
    resources: list[dict[str, Any]] = []

    resources.append({
        "apiVersion": "v1", "kind": "ServiceAccount", "metadata": _metadata("arc-chat-gateway", namespace, labels),
        "automountServiceAccountToken": False,
    })
    resources.append({
        "apiVersion": "v1", "kind": "ConfigMap", "metadata": _metadata("arc-chat-gateway", namespace, labels),
        "data": {
            "APP_ENV": "production", "HOST": "0.0.0.0", "PORT": "8080",
            "DEPLOYMENT_ENVIRONMENT": value["environment"], "TENANT_ID": value["tenant_id"],
            "STATIC_ALLOWED_ORIGINS": value["static_origin"], "JUPYTERLITE_URL": value["notebook_url"],
            "TRUSTED_AUTH_PROXY_CIDRS": "127.0.0.1/32,::1/128",
            "AUTH_USER_HEADER": "X-Forwarded-User", "AUTH_EMAIL_HEADER": "X-Forwarded-Email",
            "AUTH_GROUPS_HEADER": "X-Forwarded-Groups",
            "OIDC_ISSUER_URL": value["oidc_issuer_url"], "OIDC_REDIRECT_URL": value["oidc_redirect_url"],
            "OIDC_CLIENT_ID": value["oidc_client_id"], "TRUSTED_INGRESS_CIDRS": trusted_proxy_cidrs,
            "STATIC_SITE_HOST": value["static_host"], "ALLOWED_EMAIL_DOMAIN": value["email_domain"],
        },
    })
    resources.append({
        "apiVersion": "external-secrets.io/v1", "kind": "ExternalSecret",
        "metadata": _metadata("arc-chat-gateway", namespace, labels),
        "spec": {
            "refreshInterval": "1h",
            "secretStoreRef": {"kind": "ClusterSecretStore", "name": value["external_secret_store"]},
            "target": {"name": "arc-chat-gateway-secrets", "creationPolicy": "Owner", "deletionPolicy": "Retain"},
            "data": [
                {"secretKey": key, "remoteRef": {"key": value["vault_secret_path"], "property": key}}
                for key in ("DATABASE_URL", "IDENTITY_HMAC_KEY", "CSRF_HMAC_KEY", "OAUTH2_CLIENT_SECRET", "OAUTH2_COOKIE_SECRET")
            ],
        },
    })

    secret_env = lambda key: {"name": key, "valueFrom": {"secretKeyRef": {"name": "arc-chat-gateway-secrets", "key": key}}}
    gateway_env = [
        {"name": key, "valueFrom": {"configMapKeyRef": {"name": "arc-chat-gateway", "key": key}}}
        for key in ("APP_ENV", "HOST", "PORT", "STATIC_ALLOWED_ORIGINS", "JUPYTERLITE_URL", "TRUSTED_AUTH_PROXY_CIDRS", "AUTH_USER_HEADER", "AUTH_EMAIL_HEADER", "AUTH_GROUPS_HEADER")
    ] + [secret_env(key) for key in ("DATABASE_URL", "IDENTITY_HMAC_KEY", "CSRF_HMAC_KEY")]
    oauth_args = [
        "--provider=oidc", "--oidc-issuer-url=$(OIDC_ISSUER_URL)",
        "--oidc-email-claim=mailPreferredAddress", "--oidc-groups-claim=targetedMembership",
        "--email-domain=$(ALLOWED_EMAIL_DOMAIN)", "--client-id=$(OIDC_CLIENT_ID)",
        "--client-secret-file=/var/run/arc-chat-secrets/OAUTH2_CLIENT_SECRET",
        "--redirect-url=$(OIDC_REDIRECT_URL)", "--scope=openid profile email",
        "--code-challenge-method=S256", "--http-address=0.0.0.0:4180",
        "--upstream=http://127.0.0.1:8080/", "--reverse-proxy=true",
        "--trusted-proxy-ip=$(TRUSTED_INGRESS_CIDRS)", "--pass-user-headers=true",
        "--set-xauthrequest=true", "--prefer-email-to-user=false",
        "--pass-access-token=false", "--pass-authorization-header=false",
        "--skip-auth-strip-headers=true", "--api-route=^/api/",
        "--skip-auth-route=GET=^/health/(live|ready)$", "--skip-auth-preflight=true",
        "--force-json-errors=true", "--skip-provider-button=true",
        "--cookie-name=__Host-arcchat-oauth", "--cookie-secure=true",
        "--cookie-samesite=none", "--cookie-path=/", "--cookie-expire=8h",
        "--cookie-refresh=5m", "--cookie-secret-file=/var/run/arc-chat-secrets/OAUTH2_COOKIE_SECRET",
        "--whitelist-domain=$(STATIC_SITE_HOST)",
    ]
    resources.append({
        "apiVersion": "apps/v1", "kind": "Deployment", "metadata": _metadata("arc-chat-gateway", namespace, labels),
        "spec": {
            "replicas": value["replicas"], "revisionHistoryLimit": 3,
            "selector": {"matchLabels": selectors},
            "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1}},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "serviceAccountName": "arc-chat-gateway", "automountServiceAccountToken": False,
                    "terminationGracePeriodSeconds": 30,
                    "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
                    "topologySpreadConstraints": [{
                        "maxSkew": 1, "topologyKey": "kubernetes.io/hostname", "whenUnsatisfiable": "ScheduleAnyway",
                        "labelSelector": {"matchLabels": selectors},
                    }],
                    "volumes": [
                        {"name": "oauth-secrets", "secret": {"secretName": "arc-chat-gateway-secrets", "defaultMode": 288}},
                        {"name": "oauth-tmp", "emptyDir": {"sizeLimit": "16Mi"}},
                    ],
                    "containers": [
                        {
                            "name": "gateway", "image": value["gateway_image"], "imagePullPolicy": "IfNotPresent",
                            "ports": [{"name": "http", "containerPort": 8080, "protocol": "TCP"}],
                            "env": gateway_env,
                            "resources": value["resources"]["gateway"],
                            "securityContext": {"runAsUser": 10001, "runAsGroup": 10001, "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
                            "startupProbe": {"exec": {"command": ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=2)"]}, "periodSeconds": 5, "failureThreshold": 24},
                            "livenessProbe": {"exec": {"command": ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=2)"]}, "periodSeconds": 20, "timeoutSeconds": 3, "failureThreshold": 3},
                            "readinessProbe": {"exec": {"command": ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=3)"]}, "periodSeconds": 10, "timeoutSeconds": 4, "failureThreshold": 3},
                        },
                        {
                            "name": "oauth2-proxy", "image": OAUTH2_PROXY_IMAGE, "imagePullPolicy": "IfNotPresent",
                            "args": oauth_args,
                            "env": [
                                {"name": key, "valueFrom": {"configMapKeyRef": {"name": "arc-chat-gateway", "key": key}}}
                                for key in ("OIDC_ISSUER_URL", "OIDC_REDIRECT_URL", "OIDC_CLIENT_ID", "TRUSTED_INGRESS_CIDRS", "STATIC_SITE_HOST", "ALLOWED_EMAIL_DOMAIN")
                            ],
                            "ports": [{"name": "auth-http", "containerPort": 4180, "protocol": "TCP"}],
                            "volumeMounts": [
                                {"name": "oauth-secrets", "mountPath": "/var/run/arc-chat-secrets", "readOnly": True},
                                {"name": "oauth-tmp", "mountPath": "/tmp"},
                            ],
                            "resources": value["resources"]["oauth2_proxy"],
                            "securityContext": {"runAsUser": 2000, "runAsGroup": 2000, "runAsNonRoot": True, "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
                            "startupProbe": {"httpGet": {"path": "/ping", "port": 4180}, "periodSeconds": 5, "failureThreshold": 24},
                            "livenessProbe": {"httpGet": {"path": "/ping", "port": 4180}, "periodSeconds": 20, "timeoutSeconds": 3, "failureThreshold": 3},
                            "readinessProbe": {"httpGet": {"path": "/ready", "port": 4180}, "periodSeconds": 10, "timeoutSeconds": 4, "failureThreshold": 3},
                        },
                    ],
                },
            },
        },
    })
    resources.append({
        "apiVersion": "v1", "kind": "Service", "metadata": _metadata("arc-chat-gateway", namespace, labels),
        "spec": {"type": "ClusterIP", "selector": selectors, "ports": [{"name": "http", "port": 80, "targetPort": 4180, "protocol": "TCP"}]},
    })
    resources.append({
        "apiVersion": "networking.k8s.io/v1", "kind": "Ingress", "metadata": _metadata("arc-chat-gateway", namespace, labels) | {
            "annotations": {"nginx.ingress.kubernetes.io/ssl-redirect": "true", "nginx.ingress.kubernetes.io/proxy-body-size": "1m", "nginx.ingress.kubernetes.io/proxy-buffering": "off", "nginx.ingress.kubernetes.io/proxy-read-timeout": "1900", "nginx.ingress.kubernetes.io/proxy-send-timeout": "1900"},
        },
        "spec": {
            "ingressClassName": value["ingress_class"],
            "tls": [{"hosts": [value["gateway_host"]], "secretName": value["tls_secret_name"]}],
            "rules": [{"host": value["gateway_host"], "http": {"paths": [{"path": "/", "pathType": "Prefix", "backend": {"service": {"name": "arc-chat-gateway", "port": {"number": 80}}}}]}}],
        },
    })
    resources.append({
        "apiVersion": "cert-manager.io/v1", "kind": "Certificate", "metadata": _metadata(value["tls_secret_name"], namespace, labels),
        "spec": {"secretName": value["tls_secret_name"], "issuerRef": {"name": value["cluster_issuer"], "kind": "ClusterIssuer"}, "dnsNames": [value["gateway_host"]]},
    })
    resources.append({
        "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": _metadata("arc-chat-gateway", namespace, labels),
        "spec": {
            "podSelector": {"matchLabels": selectors}, "policyTypes": ["Ingress", "Egress"],
            "ingress": [
                {"from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": value["ingress_namespace"]}}}], "ports": [{"protocol": "TCP", "port": 4180}]},
                {"from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": value["monitoring_namespace"]}}}], "ports": [{"protocol": "TCP", "port": 8080}]},
            ],
            "egress": [
                {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}, "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}], "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]},
                *[
                    {"to": [{"ipBlock": {"cidr": cidr}}], "ports": [{"protocol": "TCP", "port": port}]}
                    for cidrs, port in ((value["database_egress_cidrs"], 5432), (value["oidc_egress_cidrs"], 443))
                    for cidr in cidrs
                ],
            ],
        },
    })
    resources.append({
        "apiVersion": "policy/v1", "kind": "PodDisruptionBudget", "metadata": _metadata("arc-chat-gateway", namespace, labels),
        "spec": {"minAvailable": 1, "selector": {"matchLabels": selectors}},
    })
    return resources


def write_manifests(config_path: Path, output_dir: Path) -> list[Path]:
    config = validate_config(json.loads(config_path.read_text(encoding="utf-8")))
    resources = render_resources(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    output: list[Path] = []
    for index, resource in enumerate(resources, start=1):
        kind = str(resource["kind"]).lower()
        path = output_dir / f"{index:02d}-{kind}.json"
        path.write_text(json.dumps(resource, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        output.append(path)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Operator-supplied deployment JSON")
    parser.add_argument("output", type=Path, help="Directory for rendered kubectl JSON manifests")
    args = parser.parse_args()
    for path in write_manifests(args.config, args.output):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
