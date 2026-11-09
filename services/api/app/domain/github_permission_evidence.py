"""GitHub 逐仓库读权限的核验快照（开发者页改造 T-0175，计划 Task 4）。

管理员“测试连接”时逐个仓库检查四项读权限（metadata / issues / pull_requests / actions），
这里把**实际得到的**检查结果存进 ``Integration.settings_json['github_permission_evidence']``，
开发者页只读这份快照，不触发测试连接，也不从 ``Repo.access`` 推导。

- 只存仓库全名、四项检查的名称 / 是否通过 / 错误码与核验时间，不存 Token、请求头或原始错误体。
- metadata 失败时其余三项没有执行，快照里就只有 metadata 一项，读取时其余三项是“未核验”。
- 测试中途被限流或服务不可用：只保存已经真正得到的那几项，没执行的不补结果；
  本次没有检查到的仓库保留上一次的快照和它自己的 checked_at。
- Token、仓库清单修改，集成停用 / 启用，或 Token 被拒（unauthorized）时整份清除。
"""

from datetime import UTC, datetime
from typing import Any, get_args

from pydantic import ValidationError

from app.models import Integration
from app.schemas.integrations import RepositoryPermissionCheck

KEY = "github_permission_evidence"
SCHEMA_VERSION = 1

# 显示顺序与“完整”的判断都按这四项（RepositoryPermissionCheck.name 的枚举）
CHECK_NAMES: tuple[str, ...] = get_args(RepositoryPermissionCheck.model_fields["name"].annotation)


def _key(full_name: str) -> str:
    return full_name.lower()  # GitHub 的仓库名不区分大小写


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _sanitize(checks: list[RepositoryPermissionCheck]) -> list[dict[str, Any]]:
    """只留下四项已知检查的名称、结果与错误码；同一项重复时保留第一次。"""
    seen: set[str] = set()
    stored: list[dict[str, Any]] = []
    for check in checks:
        if check.name in seen:
            continue
        seen.add(check.name)
        stored.append({"name": check.name, "ok": check.ok, "code": check.code})
    return stored


def record(
    integration: Integration,
    results: dict[str, list[RepositoryPermissionCheck]],
    *,
    checked_at: datetime | None = None,
) -> None:
    """保存这次测试实际得到的逐仓库结果；没检查到的仓库保留旧快照（各自的 checked_at）。"""
    if not results:
        return
    moment = (checked_at or datetime.now(UTC)).isoformat()
    current = _load(integration)
    repositories = dict(current["repositories"]) if current else {}
    for full_name, checks in results.items():
        stored = _sanitize(checks)
        if not stored:
            continue  # 一项都没执行：不写空结果，也不覆盖旧快照
        repositories[_key(full_name)] = {
            "full_name": full_name,
            "checked_at": moment,
            "checks": stored,
        }
    integration.settings_json = {
        **integration.settings_json,
        KEY: {"schema_version": SCHEMA_VERSION, "checked_at": moment, "repositories": repositories},
    }


def clear(integration: Integration) -> bool:
    """整份清除；返回是否真的删掉了什么。"""
    if KEY not in integration.settings_json:
        return False
    settings = dict(integration.settings_json)
    settings.pop(KEY, None)
    integration.settings_json = settings
    return True


def _load(integration: Integration) -> dict[str, Any] | None:
    value = integration.settings_json.get(KEY)
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return None  # 没有快照，或是认不出的旧格式：一律当作未核验
    repositories = value.get("repositories")
    if not isinstance(repositories, dict):
        return None
    return {"repositories": repositories}


def read(
    integration: Integration, full_name: str
) -> tuple[datetime | None, list[RepositoryPermissionCheck]]:
    """读取一个仓库的快照：(checked_at, checks)。没有或格式不对时为 (None, [])。

    只返回能通过校验的检查项，按四项的固定顺序排列；不在当前仓库清单里的仓库由调用方先排除。
    """
    snapshot = _load(integration)
    entry = snapshot["repositories"].get(_key(full_name)) if snapshot else None
    if not isinstance(entry, dict) or not isinstance(entry.get("checks"), list):
        return None, []
    if _key(str(entry.get("full_name") or "")) != _key(full_name):
        return None, []
    checked_at = _parse_time(entry.get("checked_at"))
    if checked_at is None:
        return None, []
    found: dict[str, RepositoryPermissionCheck] = {}
    for raw in entry["checks"]:
        try:
            check = RepositoryPermissionCheck.model_validate(raw)
        except ValidationError:
            continue
        found.setdefault(check.name, check)
    checks = [found[name] for name in CHECK_NAMES if name in found]
    return (checked_at, checks) if checks else (None, [])


def complete(checks: list[RepositoryPermissionCheck]) -> bool:
    """四项都有实际结果（不论通过与否）才算完整。"""
    return {c.name for c in checks} == set(CHECK_NAMES)
