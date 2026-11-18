"""开发会话工作目录里的文件（T-0289）：按环境列会话、列目录、预览（脱敏）、下载原文件，
不能跳出工作目录（.. 和指向外面的符号链接），官方程序里同步来的会话不能浏览。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

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
    (workdir / "big.txt").write_text("0123 4567 89abcdefghij", encoding="utf-8")
    url = f"/admin/sessions/{session_id}/files/content"
    big = (await _get(bridge, url, path="big.txt")).json()
    # 截断处被切开的词去掉（可能是半截密钥）
    assert big["truncated"] is True and big["text"] == "0123 " and big["downloadable"] is False
    too_big = await _get(bridge, url, path="big.txt", download="true")
    assert too_big.status_code == 422


# 假的私钥块：标记在运行时拼出来、内容一眼就是假的，免得源码本身被密钥扫描（gitleaks）当成私钥
PEM_KIND = "PRIVATE " + "KEY"
PEM_LINE = "fakepemline" * 5


@pytest.mark.parametrize("cut", [40, 70, 110])
def test_preview_masks_a_private_key_cut_by_the_limit(
    tmp_path: Path, monkeypatch: Any, cut: int
) -> None:
    # 审查意见：私钥块跨过预览的上限时，结束标记读不到，原来的规则认不出来，私钥的开头会原样露出
    text = (
        f"说明\n-----BEGIN {PEM_KIND}-----\n"
        + "\n".join([PEM_LINE] * 6)
        + f"\n-----END {PEM_KIND}-----\n尾巴\n"
    )
    (tmp_path / "key.pem").write_text(text, encoding="utf-8")
    monkeypatch.setattr(workfiles, "PREVIEW_BYTES", cut)
    shown = workfiles.preview(tmp_path, "key.pem")
    assert shown["truncated"] is True
    assert "fakepem" not in shown["text"] and "BEGIN" not in shown["text"]
    assert shown["text"].startswith("说明\n") and shown["text"].endswith("***")
    # 没截断的完整私钥照常整块遮住，后面的内容照常显示
    monkeypatch.setattr(workfiles, "PREVIEW_BYTES", 4096)
    whole = workfiles.preview(tmp_path, "key.pem")
    assert whole["truncated"] is False and whole["text"] == "说明\n***\n尾巴\n"


def test_preview_drops_a_token_cut_by_the_limit(tmp_path: Path, monkeypatch: Any) -> None:
    token = "ghp_" + "Ab1" * 12
    (tmp_path / "env.txt").write_text(f"TOKEN={token}\n", encoding="utf-8")
    monkeypatch.setattr(workfiles, "PREVIEW_BYTES", 30)  # 切在令牌中间：剩下的不到规则要求的长度
    shown = workfiles.preview(tmp_path, "env.txt")
    assert shown["truncated"] is True and "ghp_" not in shown["text"] and "Ab1" not in shown["text"]


def test_symlinks_are_followed_only_inside_the_workdir(tmp_path: Path) -> None:
    root = tmp_path / "work"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.txt").write_text("里面", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("外面", encoding="utf-8")
    os.symlink(root / "src", root / "abs-inside")  # 绝对路径、指向里面
    os.symlink("src/../src/a.txt", root / "rel-inside")  # 相对路径、绕一下还在里面
    os.symlink("../outside/secret.txt", root / "rel-out")  # .. 出去
    os.symlink(outside, root / "abs-out")
    os.symlink("loop-b", root / "loop-a")
    os.symlink("loop-a", root / "loop-b")

    assert workfiles.preview(root, "abs-inside/a.txt")["text"] == "里面"
    assert workfiles.preview(root, "abs-inside/a.txt")["path"] == "src/a.txt"  # 真实路径
    assert workfiles.preview(root, "rel-inside")["text"] == "里面"
    for path in ("rel-out", "abs-out/secret.txt", "loop-a"):
        with pytest.raises(workfiles.FileError) as refused:
            workfiles.preview(root, path)
        assert refused.value.code == "NOT_FOUND"
    kinds = {e["name"]: e["kind"] for e in workfiles.list_dir(root, "")["entries"]}
    assert kinds["abs-inside"] == "dir" and kinds["rel-inside"] == "file"
    assert kinds["rel-out"] == kinds["abs-out"] == kinds["loop-a"] == "link"


def test_download_reads_the_file_it_checked(tmp_path: Path) -> None:
    # 审查意见：检查完路径再按路径打开，中间可以把文件换成指出去的链接。
    # 现在检查和读取是同一个已打开的文件
    root = tmp_path / "work"
    root.mkdir()
    (root / "a.txt").write_text("检查过的内容", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("外面的内容", encoding="utf-8")
    found = workfiles.download(root, "a.txt")
    (root / "a.txt").unlink()
    os.symlink(secret, root / "a.txt")  # 检查之后换成指出去的链接
    assert b"".join(found.chunks()).decode("utf-8") == "检查过的内容"
    with pytest.raises(workfiles.FileError):
        workfiles.download(root, "a.txt")
    # 中间一级换成指出去的链接也不行
    (root / "dir").symlink_to(tmp_path)
    with pytest.raises(workfiles.FileError):
        workfiles.preview(root, "dir/secret.txt")
