"""Bounded Kubernetes CLI boundary and fixed runner workload templates.

The controller is not enabled until storage, network enforcement and command
reconciliation have been verified in a real cluster.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from uuid import UUID

from app.runtime_v2.kubernetes import KubernetesRuntime, RuntimeIdentity


class OutcomeUnknown(RuntimeError):
    """A timed-out mutation may have reached Kubernetes and needs reconciliation."""


class PendingApply(OutcomeUnknown):
    """A requested Kubernetes resource is not observed yet."""


class PendingDeletion(OutcomeUnknown):
    """A confirmed deletion is still converging."""


class KubernetesCommandError(RuntimeError):
    pass


class Kubectl:
    def __init__(self, kubeconfig: str, *, executable: str = "kubectl", timeout: int = 30) -> None:
        if not Path(kubeconfig).is_absolute():
            raise ValueError("kubeconfig path must be absolute")
        if not 1 <= timeout <= 120:
            raise ValueError("invalid timeout")
        self.kubeconfig = kubeconfig
        self.executable = executable
        self.timeout = timeout

    def call(self, args: list[str], *, input_data: str | None = None) -> str:
        try:
            result = subprocess.run(  # noqa: S603 - fixed executable and structured argv
                [self.executable, "--kubeconfig", self.kubeconfig, "--request-timeout=20s", *args],
                input=input_data,
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise OutcomeUnknown(
                "Kubernetes request timed out; reconcile before retrying"
            ) from error
        if result.returncode:
            raise KubernetesCommandError(
                f"kubectl failed ({result.returncode}): {result.stderr[:1000]}"
            )
        return result.stdout

    def get(self, kind: str, name: str, namespace: str) -> dict[str, Any]:
        value = json.loads(self.call(["get", kind, name, "-n", namespace, "-o", "json"]))
        if not isinstance(value, dict):
            raise KubernetesCommandError("invalid Kubernetes object response")
        return value

    def get_optional(
        self, kind: str, name: str, namespace: str | None = None
    ) -> dict[str, Any] | None:
        args = ["get", kind, name, "--ignore-not-found", "-o", "json"]
        if namespace:
            args += ["-n", namespace]
        output = self.call(args)
        return json.loads(output) if output.strip() else None

    def create(self, item: dict[str, Any]) -> None:
        try:
            self.call(["create", "-f", "-"], input_data=json.dumps(item))
        except KubernetesCommandError as error:
            # kubectl may have lost a response after the API server committed.
            raise OutcomeUnknown(
                "create result unknown; reconcile object before retrying"
            ) from error


def workload(
    identity: RuntimeIdentity, payload: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build only a PVC and a single replica Deployment; never accepts YAML."""
    if any(
        payload.get(k)
        for k in (
            "secrets",
            "secret_values",
            "files",
            "setup",
            "snapshot",
            "copy_from_volume",
            "template_build",
        )
    ):
        raise ValueError("initial files, setup, secrets, copy and restore are not supported")
    if payload.get("network_mode", "none") != "none":
        raise ValueError("public egress is not supported")
    if payload.get("user", "user") != "user" or payload.get("runtime", "gvisor") != "gvisor":
        raise ValueError("only non-root gVisor workloads are supported")
    disk = payload.get("disk_gb")
    if type(disk) is not int or not 1 <= disk <= 1000:
        raise ValueError("invalid disk quota")
    cpu = payload.get("cpus")
    memory = payload.get("memory_mb")
    if not isinstance(cpu, int | float) or isinstance(cpu, bool) or not isinstance(memory, int):
        raise ValueError("invalid resources")
    pod = KubernetesRuntime.pod_manifest(
        identity, str(payload.get("image") or ""), int(cpu * 1000), memory
    )
    claim_name = "data-" + identity.environment_id.hex
    podspec = pod["spec"]
    podspec["restartPolicy"] = "Always"
    podspec["runtimeClassName"] = "gvisor"
    podspec["securityContext"].update({"runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000})
    podspec["volumes"] = [{"name": "workspace", "persistentVolumeClaim": {"claimName": claim_name}}]
    container = podspec["containers"][0]
    container["workingDir"] = "/workspace"
    container["volumeMounts"] = [{"name": "workspace", "mountPath": "/workspace"}]
    command = payload.get("start_command")
    if command:
        if not isinstance(command, list) or not all(isinstance(arg, str) for arg in command):
            raise ValueError("invalid start command")
        container["command"] = command
    else:
        container["command"] = ["sleep", "infinity"]
    if payload.get("env"):
        raise ValueError("environment injection is not supported")
    claim = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": claim_name,
            "namespace": identity.namespace,
            "labels": identity.labels,
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": f"{disk}Gi"}},
        },
    }
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": identity.pod_name,
            "namespace": identity.namespace,
            "labels": identity.labels,
        },
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": identity.labels},
            "template": {"metadata": {"labels": identity.labels}, "spec": podspec},
        },
    }
    return claim, deployment


def verify_identity(obj: dict[str, Any], identity: RuntimeIdentity) -> None:
    labels = (obj.get("metadata") or {}).get("labels") or {}
    if any(labels.get(key) != value for key, value in identity.labels.items()):
        raise ValueError("workload identity mismatch")


class KubernetesRunner:
    """Fixed-object provisioning; namespace and policy must be provisioned beforehand."""

    def __init__(self, api: Kubectl) -> None:
        self.api = api
        self._delete_uids: dict[tuple[str, str], str] = {}

    def validate_create(self, identity: RuntimeIdentity, payload: dict[str, Any]) -> None:
        workload(identity, payload)

    def create(self, identity: RuntimeIdentity, payload: dict[str, Any]) -> dict[str, Any]:
        claim, deployment = workload(identity, payload)
        namespace = self.api.get_optional("namespace", identity.namespace)
        labels = ((namespace or {}).get("metadata") or {}).get("labels") or {}
        scope = {
            k: v
            for k, v in identity.labels.items()
            if k.endswith("/workspace") or k.endswith("/instance")
        }
        if any(labels.get(k) != v for k, v in scope.items()):
            raise ValueError("namespace identity mismatch")
        policy = self.api.get_optional("networkpolicy", "runner-domain", identity.namespace)
        if not policy or policy.get("spec") != KubernetesRuntime.network_policy(identity)["spec"]:
            raise ValueError("required network policy missing or mismatched")
        for resource, obj in [("pvc", claim), ("deployment", deployment)]:
            existing = self.api.get_optional(resource, obj["metadata"]["name"], identity.namespace)
            if existing is None:
                self.api.create(obj)
            else:
                verify_identity(existing, identity)
                # No blind adoption of an existing object with possibly different storage/spec.
                raise OutcomeUnknown(f"existing {resource} requires reconciliation")
        return {
            "accepted": True,
            "ready": False,
            "allocation_id": str(identity.allocation_id),
            "generation": identity.generation,
        }

    def scale(self, identity: RuntimeIdentity, replicas: int) -> dict[str, Any]:
        if replicas not in (0, 1):
            raise ValueError("invalid replica count")
        deployment = self.api.get_optional("deployment", identity.pod_name, identity.namespace)
        if deployment is None:
            raise ValueError("deployment does not exist")
        verify_identity(deployment, identity)
        version = (deployment.get("metadata") or {}).get("resourceVersion")
        if not isinstance(version, str) or not version.isdigit():
            raise ValueError("deployment has no resource version")
        try:
            self.api.call(
                [
                    "scale",
                    "deployment",
                    identity.pod_name,
                    "-n",
                    identity.namespace,
                    f"--replicas={replicas}",
                    f"--resource-version={version}",
                ]
            )
        except KubernetesCommandError as error:
            raise OutcomeUnknown("scale outcome requires reconciliation") from error
        return {
            "accepted": True,
            "ready": False,
            "generation": identity.generation,
            "allocation_id": str(identity.allocation_id),
        }

    def observe(self, identity: RuntimeIdentity) -> dict[str, Any]:
        selector = f"workspace.myworkspace.io/environment={identity.environment_id.hex}"
        result = json.loads(
            self.api.call(["get", "pods", "-n", identity.namespace, "-l", selector, "-o", "json"])
        )
        pods = result.get("items")
        if not isinstance(pods, list):
            raise ValueError("invalid Pod list")
        if len(pods) > 1:
            raise ValueError("multiple Pods for one allocation")
        if not pods:
            return {
                "ready": False,
                "pod_uid": None,
                "node": None,
                "pod_ip": None,
                "allocation_id": str(identity.allocation_id),
                "generation": identity.generation,
            }
        pod = pods[0]
        verify_identity(pod, identity)
        metadata, spec, status = (
            pod.get("metadata") or {},
            pod.get("spec") or {},
            pod.get("status") or {},
        )
        node, uid = spec.get("nodeName"), metadata.get("uid")
        ready = bool(
            uid
            and node == identity.node_name
            and not metadata.get("deletionTimestamp")
            and status.get("phase") == "Running"
            and any(
                c.get("type") == "Ready" and c.get("status") == "True"
                for c in status.get("conditions") or []
            )
        )
        return {
            "ready": ready,
            "pod_uid": uid,
            "node": node,
            "pod_ip": status.get("podIP"),
            "allocation_id": str(identity.allocation_id),
            "generation": identity.generation,
        }

    def delete(self, identity: RuntimeIdentity) -> dict[str, Any]:
        """Request foreground deletion with UID fencing; never delete attached data."""
        deployment = self.api.get_optional("deployment", identity.pod_name, identity.namespace)
        if deployment is not None:
            verify_identity(deployment, identity)
            uid = (deployment.get("metadata") or {}).get("uid")
            if not uid:
                raise ValueError("deployment UID missing")
            self._check_delete_uid(identity, "deployment", uid)
            path = f"/apis/apps/v1/namespaces/{identity.namespace}/deployments/{identity.pod_name}"
            if not (deployment.get("metadata") or {}).get("deletionTimestamp"):
                self._delete_uid(path, uid)
            if (
                self.api.get_optional("deployment", identity.pod_name, identity.namespace)
                is not None
            ):
                raise PendingDeletion("deployment deletion pending")
        if self.observe(identity).get("pod_uid") is not None:
            raise PendingDeletion("Pod termination pending")
        claim_name = "data-" + identity.environment_id.hex
        claim = self.api.get_optional("pvc", claim_name, identity.namespace)
        if claim is not None:
            verify_identity(claim, identity)
            uid = (claim.get("metadata") or {}).get("uid")
            if not uid:
                raise ValueError("PVC UID missing")
            self._check_delete_uid(identity, "pvc", uid)
            path = f"/api/v1/namespaces/{identity.namespace}/persistentvolumeclaims/{claim_name}"
            if not (claim.get("metadata") or {}).get("deletionTimestamp"):
                self._delete_uid(path, uid)
            if self.api.get_optional("pvc", claim_name, identity.namespace) is not None:
                raise PendingDeletion("PVC deletion pending")
        return {
            "deleted": True,
            "allocation_id": str(identity.allocation_id),
            "generation": identity.generation,
        }

    def _check_delete_uid(self, identity: RuntimeIdentity, kind: str, uid: str) -> None:
        key = (str(identity.allocation_id), kind)
        expected = self._delete_uids.setdefault(key, uid)
        if expected != uid:
            raise OutcomeUnknown(f"{kind} UID changed during deletion")

    def _delete_uid(self, path: str, uid: str) -> None:
        options = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {"uid": uid},
            "propagationPolicy": "Foreground",
        }
        try:
            self.api.call(["delete", "--raw", path, "-f", "-"], input_data=json.dumps(options))
        except KubernetesCommandError as error:
            raise OutcomeUnknown("delete result unknown; reconcile before retrying") from error

    def apply_private_service(
        self, identity: RuntimeIdentity, service_id: UUID, revision: int, port: int, protocol: str
    ) -> dict[str, Any]:
        """Create a ClusterIP Service and return only its observed API receipt."""
        from ipaddress import ip_address
        from uuid import UUID

        service_id = UUID(str(service_id))
        if (
            type(revision) is not int
            or revision < 1
            or type(port) is not int
            or not 1 <= port <= 65535
        ):
            raise ValueError("invalid service revision or port")
        if protocol not in ("TCP", "UDP"):
            raise ValueError("unsupported service protocol")
        namespace = self.api.get_optional("namespace", identity.namespace)
        ns_labels = ((namespace or {}).get("metadata") or {}).get("labels") or {}
        scope = {
            k: v
            for k, v in identity.labels.items()
            if k.endswith("/workspace") or k.endswith("/instance")
        }
        if any(ns_labels.get(k) != v for k, v in scope.items()):
            raise ValueError("namespace identity mismatch")
        policy = self.api.get_optional("networkpolicy", "runner-domain", identity.namespace)
        if not policy or policy.get("spec") != KubernetesRuntime.network_policy(identity)["spec"]:
            raise ValueError("required network policy missing or mismatched")
        name = "svc-" + service_id.hex
        service: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": name,
                "namespace": identity.namespace,
                "labels": identity.labels,
                "annotations": {"workspace.myworkspace.io/revision": str(revision)},
            },
            "spec": {
                "type": "ClusterIP",
                "selector": identity.labels,
                "ports": [
                    {"name": "service", "port": port, "targetPort": port, "protocol": protocol}
                ],
            },
        }
        observed = self.api.get_optional("service", name, identity.namespace)
        if observed is None:
            self.api.create(service)
            raise PendingApply("service creation pending observation")
        spec, meta = observed.get("spec") or {}, observed.get("metadata") or {}
        labels = meta.get("labels") or {}
        stable = {
            key: value
            for key, value in identity.labels.items()
            if key.endswith("/workspace")
            or key.endswith("/instance")
            or key.endswith("/environment")
        }
        if any(labels.get(key) != value for key, value in stable.items()):
            raise ValueError("service identity mismatch")
        current_revision = (meta.get("annotations") or {}).get("workspace.myworkspace.io/revision")
        if not isinstance(current_revision, str) or not current_revision.isdigit():
            raise ValueError("service revision missing")
        if int(current_revision) > revision:
            raise ValueError("stale service revision")
        if int(current_revision) < revision:
            uid, resource_version = meta.get("uid"), meta.get("resourceVersion")
            if not uid or not isinstance(resource_version, str) or not resource_version.isdigit():
                raise PendingApply("service UID or resource version not observed")
            if (
                spec.get("type") != "ClusterIP"
                or spec.get("externalIPs")
                or spec.get("externalName")
            ):
                raise ValueError("unsafe existing service")
            patch = [
                {"op": "test", "path": "/metadata/uid", "value": uid},
                {"op": "test", "path": "/metadata/resourceVersion", "value": resource_version},
                {"op": "replace", "path": "/metadata/labels", "value": identity.labels},
                {
                    "op": "replace",
                    "path": "/metadata/annotations/workspace.myworkspace.io~1revision",
                    "value": str(revision),
                },
                {"op": "replace", "path": "/spec/selector", "value": identity.labels},
                {"op": "replace", "path": "/spec/ports", "value": service["spec"]["ports"]},
            ]
            try:
                self.api.call(
                    [
                        "patch",
                        "service",
                        name,
                        "-n",
                        identity.namespace,
                        "--type=json",
                        "--patch",
                        json.dumps(patch),
                    ]
                )
            except KubernetesCommandError as error:
                raise OutcomeUnknown("service update outcome requires reconciliation") from error
            raise PendingApply("service update pending observation")
        verify_identity(observed, identity)
        if (
            spec.get("type") != "ClusterIP"
            or spec.get("externalIPs")
            or spec.get("externalName")
            or spec.get("selector") != identity.labels
        ):
            raise ValueError("service selector or type mismatch")
        ports = spec.get("ports") or []
        if (
            len(ports) != 1
            or any(
                ports[0].get(k) != v
                for k, v in {"port": port, "targetPort": port, "protocol": protocol}.items()
            )
            or ports[0].get("nodePort")
        ):
            raise ValueError("service port mismatch")
        if not meta.get("uid"):
            raise PendingApply("service UID not observed")
        address = spec.get("clusterIP")
        try:
            if not isinstance(address, str):
                raise ValueError("missing address")
            ip_address(address)
        except (ValueError, TypeError):
            raise PendingApply("service cluster IP not allocated") from None
        return {
            "applied_revision": revision,
            "cluster_ip": address,
            "name": name,
            "namespace": identity.namespace,
            "uid": meta.get("uid"),
        }

    def apply_domain_policy(self, identity: RuntimeIdentity) -> dict[str, Any]:
        """Install the fixed policy in an already managed namespace.

        A Kubernetes API receipt alone does not prove Cilium enforcement.
        """
        namespace = self.api.get_optional("namespace", identity.namespace)
        labels = ((namespace or {}).get("metadata") or {}).get("labels") or {}
        scope = {
            k: v
            for k, v in identity.labels.items()
            if k.endswith("/workspace") or k.endswith("/instance")
        }
        if any(labels.get(k) != v for k, v in scope.items()):
            raise ValueError("namespace identity mismatch")
        desired = KubernetesRuntime.network_policy(identity)
        observed = self.api.get_optional("networkpolicy", "runner-domain", identity.namespace)
        if observed is None:
            self.api.create(desired)
            raise PendingApply("policy creation pending observation")
        if observed.get("spec") != desired["spec"]:
            raise ValueError("existing policy mismatch")
        uid = (observed.get("metadata") or {}).get("uid")
        if not uid:
            raise PendingApply("policy UID not observed")
        return {"namespace": identity.namespace, "name": "runner-domain", "uid": uid}
