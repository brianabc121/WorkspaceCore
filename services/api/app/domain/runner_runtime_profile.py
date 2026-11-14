"""Finite Kubernetes admission profile shared by placement and payload creation."""

import math
import re
from typing import Any

SUPPORTED_PROFILE = "gvisor-immutable-v1"
KUBERNETES_COMMANDS = frozenset({"create_env", "start_env", "stop_env", "delete_env"})
_IMAGE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/:\-]*@sha256:[a-f0-9]{64}$")
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


class UnsupportedRuntimeProfile(ValueError):
    """The request cannot be implemented by the managed Kubernetes runner."""


def kubernetes_profile(spec: dict[str, Any]) -> dict[str, Any]:
    """Return supported workload values, without changing the Docker specification."""
    result = dict(spec)
    image = spec.get("template_image") or spec.get("image")
    digest = spec.get("image_digest")
    if (
        spec.get("template_image")
        and isinstance(image, str)
        and "@" not in image
        and isinstance(digest, str)
        and _DIGEST.fullmatch(digest)
    ):
        image += "@" + digest
    if not isinstance(image, str) or not _IMAGE.fullmatch(image):
        raise UnsupportedRuntimeProfile("Kubernetes 仅支持已解析的 image@sha256 不可变镜像")
    if spec.get("runtime", "gvisor") != "gvisor" or spec.get("user", "user") != "user":
        raise UnsupportedRuntimeProfile("Kubernetes 仅支持 gVisor 非 root 工作负载")
    if spec.get("network", spec.get("network_mode", "none")) != "none":
        raise UnsupportedRuntimeProfile("Kubernetes 当前仅支持 network=none 的实例私网")
    for field in (
        "env",
        "secrets",
        "secret_values",
        "steps",
        "setup",
        "files",
        "snapshot",
        "clone",
        "copy_from_volume",
        "template_build",
        "command",
    ):
        if spec.get(field):
            raise UnsupportedRuntimeProfile(f"Kubernetes 当前不支持 {field}")
    for field, maximum in (("memory_mb", 1048576), ("disk_gb", 1000)):
        value = spec.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or not float(value).is_integer()
            or not 1 <= value <= maximum
        ):
            raise UnsupportedRuntimeProfile(f"Kubernetes 的 {field} 必须是 1..{maximum} 的整数")
        result[field] = int(value)
    cpus = spec.get("cpus")
    if (
        isinstance(cpus, bool)
        or not isinstance(cpus, int | float)
        or not math.isfinite(cpus)
        or not 1 <= cpus * 1000 <= 64000
    ):
        raise UnsupportedRuntimeProfile("Kubernetes 的 cpus 必须在 0.001..64 范围内")
    command = spec.get("start_command")
    if command is not None and (
        not isinstance(command, list) or not command or not all(isinstance(v, str) for v in command)
    ):
        raise UnsupportedRuntimeProfile("Kubernetes 的 start_command 必须是规范化的非空字符串数组")
    result["image"] = image
    return result
