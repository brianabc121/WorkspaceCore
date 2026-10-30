"""对真实 GitHub 的只读 Connector 测试（可选，T-0028）。

设置以下环境变量时运行，否则跳过（不受 REQUIRE_INTEGRATION 影响，CI 不运行）：
- TEST_GITHUB_TOKEN：细粒度 Personal Access Token，按核验 ADR 第 3 节给
  Metadata、Issues、Pull requests、Actions 的只读权限
- TEST_GITHUB_REPO：Token 能访问的仓库，形如 owner/repo

核对内存版 GitHub（tests/fake_github.py）依赖的真实行为：版本头被接受、
Issue 列表里的 PR 带 pull_request 键、ETag 条件请求返回 304 且不计额度、坏 Token 返回 401。
只发 GET 请求。
"""

import os

import pytest

from app.integrations.base import ConnectorError
from app.integrations.github import GitHubClient
from app.integrations.http import ConnectorHttp, OutboundPolicy

TOKEN = os.environ.get("TEST_GITHUB_TOKEN", "")
REPO = os.environ.get("TEST_GITHUB_REPO", "")

pytestmark = pytest.mark.skipif(
    not (TOKEN and REPO), reason="需要 TEST_GITHUB_TOKEN 与 TEST_GITHUB_REPO（真实 GitHub）"
)


def _client(token: str = TOKEN) -> GitHubClient:
    return GitHubClient(ConnectorHttp(policy=OutboundPolicy(), read_retries=0), token)


async def test_repository_and_lists_match_the_fake() -> None:
    client = _client()
    repo = await client.get_repo(REPO)
    assert repo.data["full_name"].lower() == REPO.lower()
    issues = await client.list_issues(REPO)
    assert all("pull_request" not in item for item in issues.items)
    runs = await client.list_runs(REPO, limit=5)
    assert all("conclusion" in run for run in runs.items)


async def test_conditional_request_does_not_count() -> None:
    client = _client()
    first = await client.list_runs(REPO, limit=1)
    used_before = client.remaining
    again = await client.list_runs(REPO, etag=first.etag, limit=1)
    assert again.not_modified
    assert client.remaining is not None and used_before is not None
    assert client.remaining >= used_before  # 304 不计额度（各区域的计数可能略有出入）


async def test_bad_token_is_401() -> None:
    with pytest.raises(ConnectorError) as raised:
        await _client("github_pat_" + "x" * 40).get_repo(REPO)
    assert raised.value.kind == "AUTH_FAILED"
    assert not raised.value.resource_level
