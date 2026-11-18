"""Fixed helper installation layout shared by producer and proof collection."""

from typing import Any, TypedDict

from app.runtime_v2.kubernetes import _IMAGE, RuntimeIdentity


class HelperLayout(TypedDict):
    volumes: list[dict[str, Any]]
    initContainers: list[dict[str, Any]]
    volumeMounts: list[dict[str, Any]]


def helper_layout(identity: RuntimeIdentity, pinned_image: str) -> HelperLayout:
    """Build only the fixed, nonprivileged helper protocol; no workload options."""
    if not isinstance(pinned_image, str) or not _IMAGE.fullmatch(pinned_image):
        raise ValueError("immutable helper image required")
    return {
        "volumes": [
            {
                "name": "workspace",
                "persistentVolumeClaim": {"claimName": "data-" + identity.environment_id.hex},
            },
            {"name": "runtime-helper", "emptyDir": {"sizeLimit": "64Mi"}},
            {"name": "runtime-secrets", "emptyDir": {"medium": "Memory", "sizeLimit": "32Mi"}},
            {
                "name": "runtime-identity",
                "downwardAPI": {
                    "items": [
                        {"path": "uid", "fieldRef": {"fieldPath": "metadata.uid"}},
                        {
                            "path": "allocation",
                            "fieldRef": {
                                "fieldPath": "metadata.labels["
                                "'workspace.myworkspace.io/allocation']"
                            },
                        },
                        {
                            "path": "generation",
                            "fieldRef": {
                                "fieldPath": "metadata.labels["
                                "'workspace.myworkspace.io/generation']"
                            },
                        },
                    ]
                },
            },
        ],
        "initContainers": [
            {
                "name": "install-runtime-helper",
                "image": pinned_image,
                "command": ["/helper", "install", "/runtime/helper"],
                "volumeMounts": [{"name": "runtime-helper", "mountPath": "/runtime"}],
                "resources": {
                    "requests": {"cpu": "50m", "memory": "32Mi"},
                    "limits": {"cpu": "50m", "memory": "32Mi"},
                },
                "securityContext": {
                    "privileged": False,
                    "allowPrivilegeEscalation": False,
                    "capabilities": {"drop": ["ALL"]},
                },
            }
        ],
        "volumeMounts": [
            {"name": "workspace", "mountPath": "/workspace"},
            {"name": "runtime-helper", "mountPath": "/runtime", "readOnly": True},
            {
                "name": "runtime-identity",
                "mountPath": "/run/workspace-identity",
                "readOnly": True,
            },
            {"name": "runtime-secrets", "mountPath": "/run/secrets"},
        ],
    }
