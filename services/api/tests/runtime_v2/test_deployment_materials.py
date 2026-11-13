"""Only authenticated reviewed bindings become exposed service ports."""

import copy
import runpy
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[4]
render = runpy.run_path(str(ROOT / "infra/runtime-v2/render_deployment.py"))["render"]


def inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    target: dict[str, Any] = {
        key + "_id": str(UUID(int=i))
        for i, key in enumerate(("workspace", "instance", "environment", "allocation"), 1)
    }
    target.update(
        generation=1, pod_uid="current", pod_ip="10.42.0.10", node_name="node-a", port=7070
    )
    snapshot: dict[str, Any] = {
        "revision": 1,
        "denied_cidrs": ["10.42.0.0/16"],
        "bindings": [
            {
                "record_id": str(UUID(int=i)),
                "kind": kind,
                "port": port,
                "revision": 1,
                "generation": 1,
                "active": True,
                "source_cidrs": ["198.51.100.0/24"] if kind == "published" else [],
                "target": copy.deepcopy(target),
            }
            for i, (kind, port) in enumerate((("ingress", 30001), ("published", 20005)), 8)
        ],
    }
    options: dict[str, Any] = {
        "image": "registry.example/runtime@sha256:" + "a" * 64,
        "control_url": "https://control.example",
        "credential_secret": "gateway-auth",
        "tls_secret": "gateway-tls",
        "ca_secret": "gateway-ca",
        "pod_cidrs": ["10.42.0.0/16"],
        "node_cidrs": ["10.10.0.0/24"],
        "platform_cidrs": ["10.20.0.0/24"],
        "control_cidrs": ["10.20.0.2/32"],
        "api_cidrs": ["10.10.0.2/32"],
    }
    return snapshot, options


def test_exact_ports_readonly_rbac_and_secret_refs() -> None:
    snapshot, options = inputs()
    objects = render(snapshot, options)["items"]
    services = {item["spec"]["type"]: item["spec"] for item in objects if item["kind"] == "Service"}
    assert [p["port"] for p in services["ClusterIP"]["ports"]] == [30001]
    assert [p["port"] for p in services["LoadBalancer"]["ports"]] == [20005]
    assert services["LoadBalancer"]["externalTrafficPolicy"] == "Local"
    assert services["LoadBalancer"]["allocateLoadBalancerNodePorts"] is False
    pod = next(item for item in objects if item["kind"] == "Deployment")["spec"]["template"]["spec"]
    assert pod["hostNetwork"] is False
    assert "hostPort" not in str(pod)
    token = next(value for value in pod["containers"][0]["env"] if value["name"].endswith("_TOKEN"))
    assert token["valueFrom"]["secretKeyRef"]["name"] == "gateway-auth"
    assert "value" not in token
    role = next(item for item in objects if item["kind"] == "ClusterRole")
    assert role["rules"] == [{"apiGroups": [""], "resources": ["pods"], "verbs": ["list"]}]


@pytest.mark.parametrize(
    "case",
    [
        "mutable",
        "ipv6",
        "empty",
        "origin",
        "duplicate",
        "wrong-port",
        "managed-source",
        "target-platform",
        "target-uid",
        "secret-object",
        "target-node",
    ],
)
def test_unsafe_inputs_refused(case: str) -> None:
    snapshot, options = inputs()
    first = snapshot["bindings"][0]
    if case == "mutable":
        options["image"] = "repo:latest"
    elif case == "ipv6":
        options["pod_cidrs"] = ["::/0"]
    elif case == "empty":
        options["node_cidrs"] = []
    elif case == "origin":
        options["control_url"] = "https://user:secret@example.com"
    elif case == "duplicate":
        snapshot["bindings"].append(copy.deepcopy(first))
    elif case == "wrong-port":
        first["port"] = 20001
    elif case == "managed-source":
        snapshot["bindings"][1]["source_cidrs"] = ["10.42.0.0/16"]
    elif case == "target-platform":
        first["target"]["pod_ip"] = "10.20.0.2"
    elif case == "target-uid":
        del first["target"]["pod_uid"]
    elif case == "secret-object":
        options["credential_secret"] = {"token": "do-not-render"}
    else:
        first["target"]["node_name"] = ""
    with pytest.raises((ValueError, KeyError)):
        render(snapshot, options)


def test_tombstones_do_not_expose_ports() -> None:
    snapshot, options = inputs()
    for binding in snapshot["bindings"]:
        binding["active"] = False
    items = render(snapshot, options)["items"]
    services = [item for item in items if item["kind"] == "Service"]
    assert len(services) == 2
    assert all(item["spec"]["ports"][0]["port"] == 65535 for item in services)
    policy = next(
        item
        for item in items
        if item["kind"] == "NetworkPolicy" and item["metadata"]["name"] == "runner-gateway-routes"
    )
    assert policy["spec"]["ingress"] == policy["spec"]["egress"] == []


HARNESS = runpy.run_path(str(ROOT / "infra/runtime-v2/acceptance_two_nodes.py"))


def test_acceptance_objects_use_production_domain_policy_and_two_node_affinity() -> None:
    run_id = UUID(int=90)
    objects, identities = HARNESS["test_objects"](
        "repo@sha256:" + "a" * 64, "node-a", "node-b", run_id
    )
    pods = [obj for obj in objects if obj["kind"] == "Pod"]
    namespaces = [obj for obj in objects if obj["kind"] == "Namespace"]
    assert len(pods) == 4 and len(namespaces) == 2
    assert identities["a1"].instance_id == identities["b1"].instance_id
    assert identities["a2"].instance_id == identities["b2"].instance_id
    assert identities["a1"].node_name != identities["b1"].node_name
    for pod in pods:
        assert pod["spec"]["automountServiceAccountToken"] is False
        assert pod["spec"]["hostNetwork"] is False
        assert pod["spec"]["runtimeClassName"] == "gvisor"
        assert "volumes" not in pod["spec"]
    for namespace in namespaces:
        assert namespace["metadata"]["labels"][HARNESS["LABEL"]] == str(run_id)
    compile(HARNESS["SERVER"], "network-test-server", "exec")
    compile(HARNESS["PROBE"], "network-test-probe", "exec")
    compile(HARNESS["STREAM"], "network-stream-probe", "exec")


def test_acceptance_refuses_mutable_images_single_node_and_missing_consent() -> None:
    from argparse import Namespace

    with pytest.raises(ValueError):
        HARNESS["test_objects"]("repo:latest", "node-a", "node-b", UUID(int=90))
    with pytest.raises(ValueError):
        HARNESS["test_objects"]("repo@sha256:" + "a" * 64, "node-a", "node-a", UUID(int=90))
    with pytest.raises(ValueError, match="allow-test-cluster"):
        HARNESS["run"](Namespace(allow_test_cluster=False))


@pytest.mark.parametrize(
    "role,source", [(None, "8.8.8.8"), ("allowed", "::1"), ("denied", "8.8.8.8")]
)
def test_public_probe_requires_explicit_role_ipv4_and_matching_reviewed_source(
    role: str | None, source: str
) -> None:
    plan = {
        "host": "gateway.example",
        "port": 20001,
        "external_source_expected": source,
        "source_cidrs": ["8.8.8.0/24"],
    }
    with pytest.raises(ValueError):
        HARNESS["public_checks"](plan, role)
