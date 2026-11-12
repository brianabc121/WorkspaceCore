"""Fixed read-only Linux identity probe; no user-provided paths or shell text."""

PHYSICAL_ID_SCRIPT = r"""# myworkspace-physical-id-v1
set -eu
machine=$(tr -d '\n' < /etc/machine-id)
[ "${#machine}" -eq 32 ]
case "$machine" in *[!0-9a-f]*) exit 1 ;; esac
printf 'machine-id:%s' "$machine" | sha256sum | awk '{print $1}'
"""
