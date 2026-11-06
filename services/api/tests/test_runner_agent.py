"""runner-agent 的纯函数（T-0135 / T-0136）：Docker 多路复用流、tar、查询参数。

与 Docker 的真实交互在 docs/decisions/2026-11-06-runner-verification.md 第 2 节（本地 Docker 实测）
与 CI 的运行服务器验收（T-0141）里验证。
"""

import io
import struct
import tarfile

from app.runner_agent.docker import _url, frames, tar_of


def _frame(kind: int, data: bytes) -> bytes:
    return bytes([kind, 0, 0, 0]) + struct.pack(">I", len(data)) + data


def test_frames_split_across_chunks() -> None:
    raw = _frame(1, b"hello ") + _frame(2, b"oops\n") + _frame(1, b"world\n")
    # 任意切碎：帧头、内容都可能被拆在两块里
    chunks = [raw[i : i + 3] for i in range(0, len(raw), 3)]
    assert list(frames(iter(chunks))) == [(1, b"hello "), (2, b"oops\n"), (1, b"world\n")]


def test_frames_ignore_incomplete_tail() -> None:
    raw = _frame(1, b"done") + _frame(1, b"partial")[:10]
    assert list(frames(iter([raw]))) == [(1, b"done")]


def test_tar_of_sets_owner_and_content() -> None:
    data = tar_of({"a/b.txt": b"x" * 5}, uid=1000, gid=1000)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        member = tar.getmember("a/b.txt")
        extracted = tar.extractfile(member)
        assert extracted is not None and extracted.read() == b"xxxxx"
        assert (member.uid, member.gid, member.size) == (1000, 1000, 5)


def test_query_encodes_filters_as_json() -> None:
    url = _url("/containers/json", {"all": 1, "filters": {"label": ["myworkspace.env"]}})
    assert url == "/containers/json?all=1&filters=%7B%22label%22%3A+%5B%22myworkspace.env%22%5D%7D"
    assert _url("/version", None) == "/version"
