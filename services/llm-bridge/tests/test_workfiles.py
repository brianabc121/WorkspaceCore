"""开发会话工作目录里的文件（T-0289）：按环境列会话、列目录、预览（脱敏）、下载原文件，
不能跳出工作目录（.. 和指向外面的符号链接），官方程序里同步来的会话不能浏览。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from bridge import workfiles
from tests.conftest import Bridge

SECRET = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


async def _dev_session(bridge: Bridge) -> tuple[str, str, Path]:
    env = await bridge.admin(
        "POST",
        "/admin/environments",
        json={"platform": "fake", "mode": "dev", "name": "仓库", "sandbox": "workspace_write"},
    )
    env_id = env.json()["id"]
    made = await bridge.tool(
        "owner", "llm_session_create", {"platform": "fake", "mode": "dev", "environment": env_id}
    )
    session_id = made["session_id"]
    workdir = Path(bridge.engine.store.get_session(session_id)["workdir"])
    return env_id, session_id, workdir


async def _get(bridge: Bridge, url: str, **params: Any) -> Any:
    return await bridge.admin("GET", url, params=params)


async def test_lists_the_sessions_of_an_environment_and_browses_their_workdir(
    bridge: Bridge,
) -> None:
    env_id, session_id, workdir = await _dev_session(bridge)
    (workdir / "src").mkdir()
    (workdir / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (workdir / "README.md").write_text(f"# 说明\n密钥 {SECRET}\n", encoding="utf-8")
    (workdir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")

    listed = (await _get(bridge, f"/admin/environments/{env_id}/sessions")).json()["sessions"]
    assert [(s["session_id"], s["browsable"]) for s in listed] == [(session_id, True)]
    missing = await _get(bridge, "/admin/environments/be_00000000000000000000/sessions")
    assert missing.status_code == 404

    top = (await _get(bridge, f"/admin/sessions/{session_id}/files")).json()
    assert top["path"] == ""
    names = [(e["name"], e["kind"]) for e in top["entries"]]
    assert names[0] == ("src", "dir")  # 文件夹在前
    assert ("README.md", "file") in names and ("logo.png", "file") in names
    inner = (await _get(bridge, f"/admin/sessions/{session_id}/files", path="src")).json()
    assert inner["path"] == "src" and [e["name"] for e in inner["entries"]] == ["app.py"]

    # 预览：文字先脱敏；二进制不给内容
    readme = (
        await _get(bridge, f"/admin/sessions/{session_id}/files/content", path="README.md")
    ).json()
    assert readme["binary"] is False and readme["media_type"] == "text/markdown"
    assert "# 说明" in readme["text"] and SECRET not in readme["text"]
    logo = (
        await _get(bridge, f"/admin/sessions/{session_id}/files/content", path="logo.png")
    ).json()
    assert logo["binary"] is True and logo["text"] == ""
    # 下载：原文件（人类决定允许下载原文件），按附件下载、不许浏览器猜类型
    raw = await _get(
        bridge, f"/admin/sessions/{session_id}/files/content", path="README.md", download="true"
    )
    assert raw.status_code == 200 and SECRET in raw.text
    assert raw.headers["content-type"] == "application/octet-stream"
    assert raw.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in raw.headers["content-disposition"]


async def test_cannot_leave_the_workdir(bridge: Bridge, tmp_path: Path) -> None:
    _, session_id, workdir = await _dev_session(bridge)
    outside = tmp_path / "outside.txt"
    outside.write_text("别人的文件", encoding="utf-8")
    os.symlink(outside, workdir / "escape.txt")
    os.symlink(tmp_path, workdir / "escape-dir")
    (workdir / "inner.txt").write_text("里面的", encoding="utf-8")
    os.symlink(workdir / "inner.txt", workdir / "inner-link.txt")

    url = f"/admin/sessions/{session_id}/files"
    dotdot = await _get(bridge, url, path="../")
    assert dotdot.status_code == 422
    assert (await _get(bridge, f"{url}/content", path="escape.txt")).status_code == 404
    assert (await _get(bridge, url, path="escape-dir")).status_code == 404
    assert (
        await _get(bridge, f"{url}/content", path="escape.txt", download="true")
    ).status_code == 404
    kinds = {e["name"]: e["kind"] for e in (await _get(bridge, url)).json()["entries"]}
    # 指向外面的符号链接列出来但打不开；指向里面的照常是文件
    assert kinds["escape.txt"] == "link" and kinds["escape-dir"] == "link"
    assert kinds["inner-link.txt"] == "file"
    inner = (await _get(bridge, f"{url}/content", path="inner-link.txt")).json()
    assert inner["text"] == "里面的"
    assert (await _get(bridge, url, path="no-such")).status_code == 404
    assert (await _get(bridge, url, path="inner.txt")).status_code == 422  # 不是文件夹


async def test_external_sessions_and_large_files(bridge: Bridge, monkeypatch: Any) -> None:
    _, session_id, workdir = await _dev_session(bridge)
    # 官方程序里同步来的会话：工作目录不在管理器的工作区里，不能浏览
    store = bridge.engine.store
    external = store.create_session(
        platform="fake",
        mode="dev",
        slot="fake",
        native_id="native-1",
        model=None,
        environment_id=None,
        caller_id=None,
        caller_label=None,
        origin="external",
        title="外面的",
        workdir="/etc",
        options={},
    )
    refused = await _get(bridge, f"/admin/sessions/{external['id']}/files")
    assert refused.status_code == 422 and "不能浏览" in refused.json()["error"]["message"]
    # 太大的：预览只读开头一段，下载拒绝
    monkeypatch.setattr(workfiles, "PREVIEW_BYTES", 8)
    monkeypatch.setattr(workfiles, "DOWNLOAD_BYTES", 16)
    (workdir / "big.txt").write_text("0123456789abcdefghij", encoding="utf-8")
    url = f"/admin/sessions/{session_id}/files/content"
    big = (await _get(bridge, url, path="big.txt")).json()
    assert big["truncated"] is True and big["text"] == "01234567" and big["downloadable"] is False
    too_big = await _get(bridge, url, path="big.txt", download="true")
    assert too_big.status_code == 422
