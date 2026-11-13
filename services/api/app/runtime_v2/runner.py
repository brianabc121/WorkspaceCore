"""Bounded Kubernetes CLI boundary and fixed runner workload templates.

The controller is not enabled until storage, network enforcement and command
reconciliation have been verified in a real cluster.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from app.runtime_v2.kubernetes import KubernetesRuntime, RuntimeIdentity, normalized_policy


class OutcomeUnknown(RuntimeError):
    """A timed-out mutation may have reached Kubernetes and needs reconciliation."""


class PendingApply(OutcomeUnknown):
    """A requested Kubernetes resource is not observed yet."""


class PendingDeletion(OutcomeUnknown):
    """A confirmed deletion is still converging."""


class KubernetesCommandError(RuntimeError):
    pass


class Kubectl:
    def __init__(
        self, kubeconfig: str | None, *, executable: str = "kubectl", timeout: int = 30
    ) -> None:
        if kubeconfig is None:
            if (
                os.environ.get("KUBECONFIG")
                or not os.environ.get("KUBERNETES_SERVICE_HOST")
                or not all(
                    Path("/var/run/secrets/kubernetes.io/serviceaccount/" + name).is_file()
                    for name in ("ca.crt", "token")
                )
            ):
                raise ValueError("explicit context or mounted in-cluster identity required")
        elif not Path(kubeconfig).is_absolute():
            raise ValueError("kubeconfig path must be absolute")
        if not 1 <= timeout <= 120:
            raise ValueError("invalid timeout")
        self.kubeconfig = kubeconfig
        self.executable = executable
        self.timeout = timeout

    def call(self, args: list[str], *, input_data: str | None = None) -> str:
        try:
            result = subprocess.run(  # noqa: S603 - fixed executable and structured argv
                [
                    self.executable,
                    *(["--kubeconfig", self.kubeconfig] if self.kubeconfig else []),
                    "--request-timeout=20s",
                    "--insecure-skip-tls-verify=false",
                    *args,
                ],
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
            raise KubernetesCommandError(f"Kubernetes request failed (exit {result.returncode})")
        return result.stdout

    def validate_connection(self) -> None:
        from urllib.parse import urlsplit

        if self.kubeconfig is None:
            return  # constructor requires the CA-verified mounted service account

        config = json.loads(self.call(["config", "view", "--minify", "-o", "json"]))
        clusters = config.get("clusters") or []
        if len(clusters) != 1:
            raise ValueError("one Kubernetes cluster context is required")
        cluster = clusters[0].get("cluster") or {}
        # client-go v1.35.10 merges only nonzero scalar overrides: a false
        # CLI flag does not clear insecure=true in the selected kubeconfig.
        if cluster.get("insecure-skip-tls-verify", False) is not False:
            raise ValueError("Kubernetes TLS verification must be enabled")
        endpoint = urlsplit(str(cluster.get("server") or ""))
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
        ):
            raise ValueError("Kubernetes API requires a verified HTTPS endpoint")

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
        if not output.strip():
            return None
        value = json.loads(output)
        if not isinstance(value, dict):
            raise KubernetesCommandError("invalid Kubernetes object response")
        return value

    def create(self, item: dict[str, Any]) -> dict[str, Any]:
        try:
            observed = json.loads(
                self.call(["create", "-f", "-", "-o", "json"], input_data=json.dumps(item))
            )
            if not isinstance(observed, dict):
                raise OutcomeUnknown("create response unavailable")
            return observed
        except (KubernetesCommandError, ValueError) as error:
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
    if not isinstance(obj, dict) or not isinstance(obj.get("metadata"), dict):
        raise ValueError("malformed workload identity")
    labels = obj["metadata"].get("labels")
    if not isinstance(labels, dict):
        raise ValueError("malformed workload labels")
    if any(labels.get(key) != value for key, value in identity.labels.items()):
        raise ValueError("workload identity mismatch")


class KubernetesRunner:
    """Fixed-object provisioning; namespace and policy must be provisioned beforehand."""

    def __init__(self, api: Kubectl) -> None:
        self.api = api
        self._delete_uids: dict[tuple[str, int, str], str] = {}
        self._service_uids: dict[tuple[UUID, int], str] = {}

    def validate_create(self, identity: RuntimeIdentity, payload: dict[str, Any]) -> None:
        workload(identity, payload)

    def create(
        self,
        identity: RuntimeIdentity,
        payload: dict[str, Any],
        *,
        operation_id: str | None = None,
        receipt: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        claim, deployment = workload(identity, payload)
        if operation_id is not None:
            operation_id = str(UUID(operation_id))
            for obj in (claim, deployment):
                obj["metadata"]["annotations"] = {
                    "workspace.myworkspace.io/operation": operation_id
                }
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
                observed = self.api.create(obj)
                if receipt is not None:
                    if not isinstance(observed, dict):
                        raise OutcomeUnknown("create UID receipt unavailable")
                    uid = self._resource_uid(observed, identity, obj["metadata"]["name"])
                    receipt(resource, uid)
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
        metadata = deployment.get("metadata") or {}
        version, uid = metadata.get("resourceVersion"), metadata.get("uid")
        if not isinstance(version, str) or not version or not isinstance(uid, str) or not uid:
            raise ValueError("deployment UID or resource version missing")
        self._check_delete_uid(identity, "deployment", uid)
        patch = [
            {"op": "test", "path": "/metadata/uid", "value": uid},
            {"op": "test", "path": "/metadata/resourceVersion", "value": version},
            {"op": "replace", "path": "/spec/replicas", "value": replicas},
        ]
        try:
            self.api.call(
                [
                    "patch",
                    "deployment",
                    identity.pod_name,
                    "-n",
                    identity.namespace,
                    "--type=json",
                    "--patch",
                    json.dumps(patch),
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

    @staticmethod
    def _resource_uid(obj: dict[str, Any], identity: RuntimeIdentity, name: str) -> str:
        verify_identity(obj, identity)
        meta = obj.get("metadata") or {}
        uid = meta.get("uid")
        if (
            meta.get("name") != name
            or meta.get("namespace") != identity.namespace
            or not isinstance(uid, str)
            or not uid
        ):
            raise ValueError("resource name, namespace or UID mismatch")
        return uid

    def prepare_lifecycle(self, identity: RuntimeIdentity, kind: str) -> dict[str, Any]:
        provenance: dict[str, Any] = {}
        for resource, name in (
            ("deployment", identity.pod_name),
            ("pvc", "data-" + identity.environment_id.hex),
        ):
            obj = self.api.get_optional(resource, name, identity.namespace)
            if obj is None:
                if kind in ("start_env", "stop_env"):
                    raise ValueError("lifecycle resource missing")
                provenance[resource] = None
            else:
                uid = self._resource_uid(obj, identity, name)
                if kind == "create_env":
                    raise OutcomeUnknown("existing create resource requires reconciliation")
                self._check_delete_uid(identity, resource, uid)
                provenance[resource] = uid
        return provenance

    def reconcile_lifecycle(
        self, identity: RuntimeIdentity, kind: str, provenance: dict[str, Any]
    ) -> dict[str, Any]:
        observed = {}
        for resource, name in (
            ("deployment", identity.pod_name),
            ("pvc", "data-" + identity.environment_id.hex),
        ):
            obj = self.api.get_optional(resource, name, identity.namespace)
            expected = provenance.get(resource)
            if obj is not None:
                uid = self._resource_uid(obj, identity, name)
                if expected is None or uid != expected:
                    raise OutcomeUnknown("resource UID not proven by operation receipt")
            elif kind != "delete_env":
                raise OutcomeUnknown("lifecycle resource not observed")
            observed[resource] = obj
        selector = f"workspace.myworkspace.io/environment={identity.environment_id.hex}"
        listing = json.loads(
            self.api.call(["get", "pods", "-n", identity.namespace, "-l", selector, "-o", "json"])
        )
        if not isinstance(listing, dict):
            raise ValueError("invalid Pod inventory")
        pods = listing.get("items")
        if not isinstance(pods, list) or len(pods) > 1:
            raise OutcomeUnknown("ambiguous Pod inventory")
        for pod in pods:
            verify_identity(pod, identity)
            meta = pod["metadata"]
            pod_name = meta.get("name")
            if not isinstance(pod_name, str) or not pod_name:
                raise ValueError("Pod name missing")
            self._resource_uid(pod, identity, pod_name)
            if (pod.get("spec") or {}).get("nodeName") != identity.node_name:
                raise OutcomeUnknown("Pod bound outside configured node")
        result = {
            "ready": False,
            "deployment_uid": provenance.get("deployment"),
            "pvc_uid": provenance.get("pvc"),
            "pod_uid": None,
            "node": None,
            "pod_ip": None,
            "allocation_id": str(identity.allocation_id),
            "generation": identity.generation,
        }
        deployment = observed["deployment"]
        if kind == "delete_env":
            result["deleted"] = not deployment and not observed["pvc"] and not pods
            return result
        if deployment is None or observed["pvc"] is None:
            return result
        meta, spec, status = (
            deployment.get("metadata") or {},
            deployment.get("spec") or {},
            deployment.get("status") or {},
        )
        generation, seen = meta.get("generation"), status.get("observedGeneration")
        current = (
            type(generation) is int and type(seen) is int and generation > 0 and seen >= generation
        )
        if not current or meta.get("deletionTimestamp"):
            return result
        if kind == "stop_env":
            result["stopped"] = (
                spec.get("replicas") == 0
                and not pods
                and all(
                    status.get(key, 0) == 0
                    for key in (
                        "replicas",
                        "readyReplicas",
                        "availableReplicas",
                        "terminatingReplicas",
                    )
                )
            )
            return result
        if spec.get("replicas") != 1 or len(pods) != 1:
            return result
        pod = pods[0]
        podmeta, podstatus = pod.get("metadata") or {}, pod.get("status") or {}
        owners = [
            owner
            for owner in podmeta.get("ownerReferences") or []
            if owner.get("controller") is True
        ]
        if (
            len(owners) != 1
            or owners[0].get("kind") != "ReplicaSet"
            or not owners[0].get("name")
            or not owners[0].get("uid")
        ):
            raise OutcomeUnknown("Pod owner receipt missing")
        owner = owners[0]
        rs = self.api.get_optional("replicaset", owner["name"], identity.namespace)
        if not rs or self._resource_uid(rs, identity, owner["name"]) != owner["uid"]:
            raise OutcomeUnknown("ReplicaSet UID mismatch")
        parents = [
            parent
            for parent in (rs.get("metadata") or {}).get("ownerReferences") or []
            if parent.get("controller") is True
        ]
        if (
            len(parents) != 1
            or parents[0].get("kind") != "Deployment"
            or parents[0].get("name") != identity.pod_name
            or parents[0].get("uid") != provenance.get("deployment")
        ):
            raise OutcomeUnknown("Pod Deployment owner mismatch")
        result.update(
            {
                "pod_uid": podmeta["uid"],
                "node": identity.node_name,
                "pod_ip": podstatus.get("podIP"),
            }
        )
        result["ready"] = bool(
            not podmeta.get("deletionTimestamp")
            and podstatus.get("phase") == "Running"
            and any(
                condition.get("type") == "Ready" and condition.get("status") == "True"
                for condition in podstatus.get("conditions") or []
            )
        )
        return result

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
        key = (str(identity.allocation_id), identity.generation, kind)
        expected = self._delete_uids.setdefault(key, uid)
        if expected != uid:
            raise OutcomeUnknown(f"{kind} UID changed during deletion")

    def _delete_uid(self, path: str, uid: str, resource_version: str | None = None) -> None:
        options: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {"uid": uid},
            "propagationPolicy": "Foreground",
        }
        if resource_version is not None:
            options["preconditions"]["resourceVersion"] = resource_version
        try:
            self.api.call(["delete", "--raw", path, "-f", "-"], input_data=json.dumps(options))
        except KubernetesCommandError as error:
            raise OutcomeUnknown("delete result unknown; reconcile before retrying") from error

    def apply_private_service(
        self,
        identity: RuntimeIdentity,
        service_id: UUID,
        revision: int,
        port: int,
        protocol: str,
        *,
        expected_uid: str | None = None,
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
        previous_uid = expected_uid or self._service_uids.get((service_id, identity.generation))
        if previous_uid is not None and meta.get("uid") != previous_uid:
            raise OutcomeUnknown("service UID replaced since receipt")
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
        generation = labels.get("workspace.myworkspace.io/generation")
        if (
            not isinstance(generation, str)
            or not generation.isdigit()
            or int(generation) > identity.generation
            or (
                int(generation) == identity.generation
                and labels.get("workspace.myworkspace.io/allocation") != identity.allocation_id.hex
            )
        ):
            raise ValueError("stale service generation or allocation")
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
        if not meta.get("uid") or not meta.get("resourceVersion"):
            raise PendingApply("service UID or resource version not observed")
        address = spec.get("clusterIP")
        try:
            if not isinstance(address, str):
                raise ValueError("missing address")
            ip_address(address)
        except (ValueError, TypeError):
            raise PendingApply("service cluster IP not allocated") from None
        self._service_uids[(service_id, identity.generation)] = meta["uid"]
        return {
            "applied_revision": revision,
            "cluster_ip": address,
            "name": name,
            "namespace": identity.namespace,
            "uid": meta.get("uid"),
            "resource_version": meta["resourceVersion"],
        }

    @staticmethod
    def _domain_scope(identity: RuntimeIdentity) -> dict[str, str]:
        return {
            k: v
            for k, v in identity.labels.items()
            if k.endswith("/workspace") or k.endswith("/instance")
        }

    def ensure_domain(self, identity: RuntimeIdentity) -> dict[str, Any]:
        """Provision identity labels and deny all traffic before any workload exists."""
        namespace = self.api.get_optional("namespace", identity.namespace)
        scope = self._domain_scope(identity)
        if namespace is None:
            self.api.create(
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": identity.namespace,
                        "labels": scope,
                    },
                }
            )
            raise PendingApply("namespace creation pending observation")
        metadata = namespace.get("metadata") or {}
        if any((metadata.get("labels") or {}).get(k) != v for k, v in scope.items()):
            raise ValueError("namespace identity mismatch")
        if not metadata.get("uid") or metadata.get("deletionTimestamp"):
            raise PendingApply("namespace UID not observed or namespace terminating")
        self._apply_policy(
            identity,
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {
                    "name": "runner-default-deny",
                    "namespace": identity.namespace,
                    "labels": scope,
                },
                "spec": {
                    "podSelector": {},
                    "policyTypes": ["Ingress", "Egress"],
                    "ingress": [],
                    "egress": [],
                },
            },
        )
        return self.apply_domain_policy(identity)

    def _apply_policy(self, identity: RuntimeIdentity, desired: dict[str, Any]) -> dict[str, Any]:
        name = desired["metadata"]["name"]
        observed = self.api.get_optional("networkpolicy", name, identity.namespace)
        if observed is None:
            self.api.create(desired)
            raise PendingApply("policy creation pending observation")
        meta = observed.get("metadata") or {}
        uid, version = meta.get("uid"), meta.get("resourceVersion")
        if meta.get("deletionTimestamp") or not uid:
            raise PendingApply("policy UID not observed or deletion pending")
        annotations = desired["metadata"].get("annotations") or {}
        previous = meta.get("annotations") or {}
        for key in ("workspace.myworkspace.io/revision", "workspace.myworkspace.io/generation"):
            if key in previous and (
                key not in annotations or int(previous[key]) > int(annotations[key])
            ):
                raise ValueError("stale or missing policy revision or generation")
        if normalized_policy(observed.get("spec")) != normalized_policy(desired["spec"]) or (
            desired["metadata"].get("annotations") is not None
            and meta.get("annotations") != desired["metadata"]["annotations"]
        ):
            if not version:
                raise PendingApply("policy resource version not observed")
            patch = [
                {"op": "test", "path": "/metadata/uid", "value": uid},
                {"op": "test", "path": "/metadata/resourceVersion", "value": version},
                {
                    "op": "add",
                    "path": "/metadata/labels",
                    "value": desired["metadata"].get("labels", self._domain_scope(identity)),
                },
                {"op": "add", "path": "/metadata/annotations", "value": annotations},
                {"op": "replace", "path": "/spec", "value": desired["spec"]},
            ]
            try:
                self.api.call(
                    [
                        "patch",
                        "networkpolicy",
                        name,
                        "-n",
                        identity.namespace,
                        "--type=json",
                        "--patch",
                        json.dumps(patch),
                    ]
                )
            except KubernetesCommandError as error:
                raise OutcomeUnknown("policy update requires reconciliation") from error
            current = self.api.get_optional("networkpolicy", name, identity.namespace)
            current_meta = (current or {}).get("metadata") or {}
            if (
                not current
                or normalized_policy(current.get("spec")) != normalized_policy(desired["spec"])
                or current_meta.get("uid") != uid
                or (current_meta.get("annotations") or {}) != annotations
                or current_meta.get("deletionTimestamp")
                or not current_meta.get("resourceVersion")
            ):
                raise PendingApply("policy update pending observation")
            version = current_meta["resourceVersion"]
        return {
            "namespace": identity.namespace,
            "name": name,
            "uid": uid,
            "resource_version": version,
        }

    @staticmethod
    def _gateway_peer() -> dict[str, Any]:
        return {
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "runner-system"}},
            "podSelector": {
                "matchLabels": {"workspace.myworkspace.io/component": "network-gateway"}
            },
        }

    def apply_gateway_egress(
        self, identity: RuntimeIdentity, ports: list[int], revision: int | None = None
    ) -> dict[str, Any]:
        if any(type(port) is not int or not 30000 <= port <= 39999 for port in ports):
            raise ValueError("invalid private gateway port")
        if ports and revision is None:
            raise ValueError("gateway ports require policy revision")
        metadata: dict[str, Any] = {
            "name": "runner-gateway-egress",
            "namespace": identity.namespace,
            "labels": self._domain_scope(identity),
        }
        if revision is not None:
            if type(revision) is not int or revision < 1:
                raise ValueError("invalid gateway policy revision")
            metadata["annotations"] = {"workspace.myworkspace.io/revision": str(revision)}
        return self._apply_policy(
            identity,
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": metadata,
                "spec": {
                    "podSelector": {"matchLabels": self._domain_scope(identity)},
                    "policyTypes": ["Egress"],
                    "egress": (
                        [
                            {
                                "to": [self._gateway_peer()],
                                "ports": [
                                    {"protocol": "TCP", "port": port} for port in sorted(set(ports))
                                ],
                            }
                        ]
                        if ports
                        else []
                    ),
                },
            },
        )

    def apply_gateway_ingress(
        self, identity: RuntimeIdentity, service_id: UUID, revision: int, port: int, enabled: bool
    ) -> dict[str, Any] | None:
        name = "gateway-" + UUID(str(service_id)).hex
        if not enabled:
            return self._remove_policy(identity, name, revision)
        if (
            type(port) is not int
            or not 1 <= port <= 65535
            or type(revision) is not int
            or revision < 1
        ):
            raise ValueError("invalid gateway ingress")
        return self._apply_policy(
            identity,
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {
                    "name": name,
                    "namespace": identity.namespace,
                    "labels": identity.labels,
                    "annotations": {
                        "workspace.myworkspace.io/revision": str(revision),
                        "workspace.myworkspace.io/generation": str(identity.generation),
                    },
                },
                "spec": {
                    "podSelector": {"matchLabels": identity.labels},
                    "policyTypes": ["Ingress"],
                    "ingress": [
                        {
                            "from": [self._gateway_peer()],
                            "ports": [{"protocol": "TCP", "port": port}],
                        }
                    ],
                },
            },
        )

    def _remove_policy(
        self, identity: RuntimeIdentity, name: str, revision: int
    ) -> dict[str, Any] | None:
        observed = self.api.get_optional("networkpolicy", name, identity.namespace)
        if observed is None:
            return None
        meta = observed.get("metadata") or {}
        annotations = meta.get("annotations") or {}
        if int(annotations.get("workspace.myworkspace.io/revision", 0)) > revision:
            raise ValueError("stale policy revision")
        verify_identity(observed, identity)
        uid, version = meta.get("uid"), meta.get("resourceVersion")
        if not uid or not version:
            raise PendingApply("policy UID or version missing")
        path = f"/apis/networking.k8s.io/v1/namespaces/{identity.namespace}/networkpolicies/{name}"
        self._delete_uid(path, uid, version)
        if self.api.get_optional("networkpolicy", name, identity.namespace) is not None:
            raise PendingDeletion("gateway policy deletion pending")
        return {"uid": uid, "resource_version": version}

    def delete_private_service(
        self,
        identity: RuntimeIdentity,
        service_id: UUID,
        revision: int,
        *,
        expected_uid: str | None = None,
    ) -> dict[str, Any] | None:
        service_id = UUID(str(service_id))
        name = "svc-" + service_id.hex
        observed = self.api.get_optional("service", name, identity.namespace)
        if observed is None:
            return None
        verify_identity(observed, identity)
        meta = observed.get("metadata") or {}
        uid, version = meta.get("uid"), meta.get("resourceVersion")
        expected = expected_uid or self._service_uids.get((service_id, identity.generation))
        if expected is not None and uid != expected:
            raise OutcomeUnknown("service UID changed during revocation")
        if not uid or not version:
            raise PendingDeletion("service UID or resource version not observed")
        previous = int((meta.get("annotations") or {}).get("workspace.myworkspace.io/revision", 0))
        if previous < 1 or previous > revision:
            raise ValueError("stale service revocation revision")
        self._service_uids[(service_id, identity.generation)] = uid
        self._delete_uid(f"/api/v1/namespaces/{identity.namespace}/services/{name}", uid, version)
        if self.api.get_optional("service", name, identity.namespace) is not None:
            raise PendingDeletion("service deletion pending observation")
        return {"uid": uid, "resource_version": version}

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
        return self._apply_policy(identity, KubernetesRuntime.network_policy(identity))
