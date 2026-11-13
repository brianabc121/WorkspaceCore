"""Gateway routes are observed in Kubernetes before a listener can be acknowledged."""

import importlib.util
from typing import Any

import pytest

from tests.runtime_v2.test_private_network import Api


def publisher(api: Api) -> Any:
    assert importlib.util.find_spec("app.runtime_v2.gateway_ports") is not None, (
        "automatic Service port publication is required"
    )
    from app.runtime_v2.gateway_ports import GatewayPortPublisher

    return GatewayPortPublisher(api)


def objects() -> Api:
    api = Api()
    labels = {"workspace.myworkspace.io/component": "network-gateway"}
    for name, kind in (("runner-gateway", "ClusterIP"), ("runner-gateway-public", "LoadBalancer")):
        api.create(
            {
                "kind": "Service",
                "metadata": {"name": name, "namespace": "runner-system", "labels": labels},
                "spec": {
                    "type": kind,
                    "selector": labels,
                    "ports": [
                        {"name": "disabled", "protocol": "TCP", "port": 65535, "targetPort": 65535}
                    ],
                    **(
                        {
                            "externalTrafficPolicy": "Local",
                            "allocateLoadBalancerNodePorts": False,
                            "loadBalancerSourceRanges": ["0.0.0.0/32"],
                        }
                        if kind == "LoadBalancer"
                        else {}
                    ),
                },
                "status": {"loadBalancer": {"ingress": [{"ip": "203.0.113.20"}]}},
            }
        )
    api.create(
        {
            "kind": "NetworkPolicy",
            "metadata": {
                "name": "runner-gateway-routes",
                "namespace": "runner-system",
                "labels": labels,
            },
            "spec": {
                "podSelector": {"matchLabels": labels},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": [],
            },
        }
    )
    return api


def test_dynamic_gateway_ports_and_withdrawal_are_exact_and_observed() -> None:
    api = objects()
    manager = publisher(api)
    binding = {
        "kind": "ingress",
        "source_instance_id": "00000000-0000-0000-0000-000000000005",
        "port": 30001,
        "active": True,
        "target": {
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "instance_id": "00000000-0000-0000-0000-000000000002",
            "environment_id": "00000000-0000-0000-0000-000000000003",
            "allocation_id": "00000000-0000-0000-0000-000000000004",
            "generation": 1,
            "port": 5432,
        },
        "source_cidrs": [],
    }
    manager.reconcile({"bindings": [binding], "denied_cidrs": ["10.0.0.0/8"]})
    service = api.get_optional("service", "runner-gateway", "runner-system")
    assert service is not None
    assert service["spec"]["ports"] == [
        {"name": "tcp-30001", "port": 30001, "targetPort": 30001, "protocol": "TCP"}
    ]
    policy = api.get_optional("networkpolicy", "runner-gateway-routes", "runner-system")
    assert policy is not None
    assert policy["spec"]["egress"][0]["ports"] == [{"port": 5432, "protocol": "TCP"}]
    manager.reconcile({"bindings": [], "denied_cidrs": ["10.0.0.0/8"]})
    service = api.get_optional("service", "runner-gateway", "runner-system")
    policy = api.get_optional("networkpolicy", "runner-gateway-routes", "runner-system")
    assert service is not None and policy is not None
    assert service["spec"]["ports"][0]["name"] == "disabled"
    assert policy["spec"]["ingress"] == []


def test_unowned_service_is_not_adopted() -> None:
    api = objects()
    api.objects[("service", "runner-gateway", "runner-system")]["metadata"]["labels"] = {}
    before = len(api.mutations)
    with pytest.raises(ValueError, match="identity"):
        publisher(api).reconcile({"bindings": [], "denied_cidrs": ["10.0.0.0/8"]})
    assert len(api.mutations) == before


def test_waiting_public_address_keeps_private_routes_available() -> None:
    api = objects()
    api.objects[("service", "runner-gateway-public", "runner-system")]["status"] = {
        "loadBalancer": {"ingress": []}
    }
    target = {
        "workspace_id": "00000000-0000-0000-0000-000000000001",
        "instance_id": "00000000-0000-0000-0000-000000000002",
        "environment_id": "00000000-0000-0000-0000-000000000003",
        "allocation_id": "00000000-0000-0000-0000-000000000004",
        "generation": 1,
        "port": 5432,
    }
    bindings = [
        {
            "kind": "ingress",
            "source_instance_id": "00000000-0000-0000-0000-000000000005",
            "port": 30001,
            "active": True,
            "target": target,
            "source_cidrs": [],
        },
        {
            "kind": "published",
            "port": 20001,
            "active": True,
            "target": target,
            "source_cidrs": ["203.0.113.4/32"],
        },
    ]
    snapshot: dict[str, Any] = {"bindings": bindings, "denied_cidrs": ["10.0.0.0/8"]}
    effective = publisher(api).reconcile(snapshot)
    assert effective["bindings"][0]["active"]
    assert not effective["bindings"][1]["active"]
    assert snapshot["bindings"][1]["active"]  # do not mutate the authoritative snapshot
    service = api.get_optional("service", "runner-gateway", "runner-system")
    assert service is not None and service["spec"]["ports"][0]["port"] == 30001
