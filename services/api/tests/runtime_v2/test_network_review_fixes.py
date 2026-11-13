"""Regressions for real API normalization and colocated platform resources."""

from typing import Any

import pytest

from app.runtime_v2.runner import KubernetesRunner, PendingApply
from app.runtime_v2.telemetry import NodeTelemetry
from tests.runtime_v2.test_gateway_ports import objects, publisher
from tests.runtime_v2.test_node_telemetry import Api as NodeApi
from tests.runtime_v2.test_private_network import Api, identity


class OmittingApi(Api):
    def get_optional(
        self, kind: str, name: str, namespace: str | None = None
    ) -> dict[str, Any] | None:
        obj = super().get_optional(kind, name, namespace)
        if obj is not None:
            if obj["metadata"].get("annotations") == {}:
                obj["metadata"].pop("annotations")
            if kind == "networkpolicy":
                for key in ("ingress", "egress"):
                    if obj["spec"].get(key) == []:
                        obj["spec"].pop(key)
        return obj


def test_private_domain_converges_with_real_kubernetes_omitempty() -> None:
    api = OmittingApi()
    runner, ident = KubernetesRunner(api), identity()
    for _ in range(10):
        try:
            runner.ensure_domain(ident)
            break
        except PendingApply:
            continue
    else:
        pytest.fail("correct empty policies never converge under Kubernetes JSON serialization")
    prior = len(api.mutations)
    runner.ensure_domain(ident)
    assert len(api.mutations) == prior


def test_gateway_empty_bootstrap_converges_with_omitted_directions() -> None:
    api = OmittingApi()
    seeded = objects()
    api.objects, api.version = seeded.objects, seeded.version
    publisher(api).reconcile({"bindings": [], "denied_cidrs": ["10.42.0.0/16"]})
    mutations = len(api.mutations)
    publisher(api).reconcile({"bindings": [], "denied_cidrs": ["10.42.0.0/16"]})
    assert len(api.mutations) == mutations


def test_gateway_platform_pod_does_not_disable_node_reports() -> None:
    api = NodeApi()
    api.pods.append(
        {
            "metadata": {
                "name": "runner-gateway-abc",
                "namespace": "runner-system",
                "labels": {"workspace.myworkspace.io/component": "network-gateway"},
            },
            "spec": {"nodeName": "node-a"},
        }
    )
    containers, volumes, _ = NodeTelemetry(api, "node-a")._workloads(api.summary)
    assert containers == volumes == []


def test_incomplete_tenant_identity_remains_fail_closed() -> None:
    api = NodeApi()
    api.pods.append(
        {
            "metadata": {
                "name": "bad",
                "namespace": "oc-bad",
                "labels": {"workspace.myworkspace.io/environment": "not-an-identity"},
            },
            "spec": {"nodeName": "node-a"},
        }
    )
    with pytest.raises((KeyError, ValueError)):
        NodeTelemetry(api, "node-a")._workloads(api.summary)
