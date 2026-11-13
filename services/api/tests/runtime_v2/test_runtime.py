import uuid

import httpx
import pytest

from app.runtime_v2.kubernetes import KubernetesRuntime, RuntimeIdentity, UnsupportedOperation


def identity() -> RuntimeIdentity:
    return RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 3, "worker-a")


def test_pod_uses_required_affinity_and_fixed_security_context() -> None:
    ident = identity()
    pod = KubernetesRuntime.pod_manifest(
        ident, "registry.example/image@sha256:" + "a" * 64, 500, 256
    )
    spec = pod["spec"]
    assert "nodeName" not in spec
    assert spec["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
        "nodeSelectorTerms"
    ][0]["matchExpressions"][0]["values"] == ["worker-a"]
    assert spec["automountServiceAccountToken"] is False
    assert spec["containers"][0]["securityContext"]["allowPrivilegeEscalation"] is False
    assert spec["containers"][0]["resources"]["requests"] == {"cpu": "500m", "memory": "256Mi"}
    assert pod["metadata"]["labels"]["workspace.myworkspace.io/instance"] == ident.instance_id.hex


def test_manifest_rejects_mutable_images_and_invalid_resource_requests() -> None:
    with pytest.raises(ValueError):
        KubernetesRuntime.pod_manifest(identity(), "image:latest", 500, 256)
    with pytest.raises(ValueError):
        KubernetesRuntime.pod_manifest(identity(), "image@sha256:" + "a" * 64, 0, 256)


@pytest.mark.asyncio
async def test_receipt_uses_actual_binding_and_rejects_stale_generation() -> None:
    ident = identity()
    pod = KubernetesRuntime.pod_manifest(ident, "image@sha256:" + "a" * 64, 500, 256)
    pod["metadata"]["uid"] = "actual-uid"
    pod["spec"]["nodeName"] = "worker-a"
    pod["status"] = {
        "phase": "Running",
        "podIP": "10.0.0.2",
        "conditions": [{"type": "Ready", "status": "True"}],
    }

    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pod)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="https://cluster.invalid"
    ) as client:
        runtime = KubernetesRuntime(client)
        receipt = await runtime.inspect(ident)
        assert receipt["pod_uid"] == "actual-uid"
        assert receipt["node"] == "worker-a"
        assert receipt["ready"] is True
        pod["metadata"]["labels"]["workspace.myworkspace.io/generation"] = "2"
        with pytest.raises(ValueError, match="identity"):
            await runtime.inspect(ident)


@pytest.mark.asyncio
async def test_unsafe_legacy_operations_are_explicitly_unsupported() -> None:
    async with httpx.AsyncClient(base_url="https://cluster.invalid") as client:
        runtime = KubernetesRuntime(client)
        with pytest.raises(UnsupportedOperation):
            await runtime.execute("exec", {}, identity())


def test_network_policy_only_allows_same_workspace_and_instance() -> None:
    ident = identity()
    policy = KubernetesRuntime.network_policy(ident)
    scope = {
        "workspace.myworkspace.io/workspace": ident.workspace_id.hex,
        "workspace.myworkspace.io/instance": ident.instance_id.hex,
    }
    spec = policy["spec"]
    assert spec["podSelector"]["matchLabels"] == scope
    assert spec["ingress"] == [{"from": [{"podSelector": {"matchLabels": scope}}]}]
    assert spec["egress"][0] == {"to": [{"podSelector": {"matchLabels": scope}}]}
    assert len(spec["egress"]) == 2  # the other exception is restricted managed CoreDNS
    assert spec["policyTypes"] == ["Ingress", "Egress"]


def test_node_selector_uses_node_restriction_protected_label() -> None:
    ident = identity()
    pod = KubernetesRuntime.pod_manifest(ident, "image@sha256:" + "a" * 64, 500, 256)
    match = pod["spec"]["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0]
    assert match["key"] == "workspace.myworkspace.io.node-restriction.kubernetes.io/runner-node"
