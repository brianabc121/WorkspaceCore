#!/usr/bin/env python3
"""把 .collab/ 的状态镜像到 GitHub Issues。只在 CI 里运行（.github/workflows/collab-sync.yml）。

- 维护一个带 collab-board 标签的“协作看板” issue，内容是 main 上最新的任务看板；
- main 上每出现一张新交接单，就开一个 collab-handoff issue 提醒，附上可直接复制给接收方的开工话术；
- 任务不再停在那张交接单上（已接手 / 已处理）后，自动关闭对应的提醒 issue。

issue 只是镜像，事实来源仍是 .collab/ 下的文件。
本地预览（不访问 GitHub）：python3 scripts/collab_sync.py --dry-run --before <提交>
"""

import argparse
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stdout

import collab

BOARD_LABEL = "collab-board"
HANDOFF_LABEL = "collab-handoff"
LABELS = {
    BOARD_LABEL: ("0e8a16", "协作看板（CI 自动维护）"),
    HANDOFF_LABEL: ("d93f0b", "交接提醒（CI 自动维护）"),
}
MARKER_RE = re.compile(r"<!-- collab-note: (\S+) -->")


class GitHub:
    def __init__(self, repo, token, dry_run):
        self.repo, self.token, self.dry_run = repo, token, dry_run

    def call(self, method, path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(
            f"https://api.github.com/repos/{self.repo}{path}", data=body, method=method
        )
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else None

    def write(self, method, path, data, what):
        print(f"[dry-run] {what}" if self.dry_run else what)
        return None if self.dry_run else self.call(method, path, data)

    def issues(self, label, state):
        if self.dry_run:
            return []
        found, page = [], 1
        while True:
            query = urllib.parse.urlencode({"labels": label, "state": state, "per_page": 100, "page": page})
            batch = self.call("GET", f"/issues?{query}")
            found += [i for i in batch if "pull_request" not in i]
            if len(batch) < 100:
                return found
            page += 1

    def ensure_labels(self):
        if self.dry_run:
            return
        for name, (color, description) in LABELS.items():
            try:
                self.call("POST", "/labels", {"name": name, "color": color, "description": description})
            except urllib.error.HTTPError as e:
                if e.code != 422:  # 422：标签已存在
                    raise


def added_notes(before):
    """before..HEAD 之间新增的交接单路径。"""
    if not before or not before.strip("0"):
        return []
    if collab.git("cat-file", "-e", f"{before}^{{commit}}") is None:
        print(f"找不到提交 {before}（可能被强推覆盖），跳过新交接单检测")
        return []
    out = collab.git("diff", "--name-only", "--diff-filter=A", before, "HEAD", "--", ".collab/handoffs/")
    return [p for p in (out or "").splitlines() if p.endswith(".md")]


def note_section(note, number):
    m = re.search(rf"^## {number}\.[^\n]*\n(.*?)(?=^## |\Z)", note.body, re.S | re.M)
    return m.group(1).strip() if m else ""


def is_waiting(note, tasks, latest):
    """任务是否还停在这张交接单上、等接收方处理。"""
    task = tasks.get(note["task"])
    last = latest.get(note["task"])
    return (
        task is not None
        and last is not None
        and last.path == note.path
        and task["status"] in ("handoff", "blocked")
        and task["owner"] == note["to"]
    )


def kickoff_prompt(note, path):
    task, to = note["task"], note["to"]
    if to == "codex":
        return (
            "在 Codex 里选择本仓库的 **main** 分支，发送：\n\n"
            f"> 你的协作身份是 codex。按 AGENTS.md 的协作协议开工：先运行 "
            f"`python3 scripts/collab.py inbox --as codex --no-fetch`，接手 {task}（交接单 `{path}`），"
            "处理完后按收工流程结束。"
        )
    if to == "claude":
        return (
            "新开一个 Claude Code 会话（选择本仓库），发送：\n\n"
            f"> 按 AGENTS.md 的协作协议开工：接手 {task}（交接单 `{path}`），处理完后按收工流程提 PR。"
        )
    return (
        "这是上报给你的事项。读完交接单做出决定后，把任务交给某个代理，例如对它说：\n\n"
        f"> {task} 按〈你的决定〉处理，交给你了。按 AGENTS.md 的协作协议开工，处理完后按收工流程提 PR。"
    )


def handoff_issue(note, path, task, repo, sha):
    title = f"[交接] {note['task']} {note['from']} → {note['to']}：{task['title']}"
    body = "\n".join([
        f"<!-- collab-note: {path} -->",
        f"**{note['from']} → {note['to']}** · `{note['type']}` · {note['created']}",
        "",
        "### 摘要",
        note_section(note, 1),
        "",
        "### 交给接收方的工作",
        note_section(note, 3),
        "",
        "### 怎么开工",
        kickoff_prompt(note, path),
        "",
        f"完整交接单：[{path}](https://github.com/{repo}/blob/{sha}/{path})",
        "",
        f"> 本 issue 由 CI 自动创建，{note['task']} 被接手后会自动关闭，不需要手动处理。",
    ])
    return title, body


def board_body(sha):
    buf = io.StringIO()
    with redirect_stdout(buf):
        collab.print_status(markdown=True)
    return (
        "> 由 CI 根据 main 上的 `.collab/` 自动生成，请不要手动编辑；事实来源是仓库里的文件。\n"
        f"> 更新于 {collab.stamp()}（提交 {sha[:7]}）。超过 {collab.STALE_DAYS} 天没动静的任务会标 ⚠。\n\n"
        f"{buf.getvalue().rstrip()}\n\n"
        "本地查看：`python3 scripts/collab.py status`；操作手册见 `.collab/README.md`。\n"
    )


def sync(gh, before):
    sha = collab.git("rev-parse", "HEAD") or "HEAD"
    tasks = {t["id"]: t for t in collab.load_strict(collab.TASKS_DIR, "T-*.md")}
    notes = collab.load_strict(collab.HANDOFFS_DIR)
    by_path = {collab.rel(n.path): n for n in notes}
    latest = {tid: ns[-1] for tid, ns in collab.handoffs_by_task(notes).items()}

    gh.ensure_labels()
    reminded = set()
    for issue in gh.issues(HANDOFF_LABEL, "all"):
        m = MARKER_RE.search(issue.get("body") or "")
        if m:
            reminded.add(m.group(1))

    for path in added_notes(before):
        note = by_path.get(path)
        if note is None or path in reminded or not is_waiting(note, tasks, latest):
            continue
        title, body = handoff_issue(note, path, tasks[note["task"]], gh.repo, sha)
        gh.write("POST", "/issues", {"title": title, "body": body, "labels": [HANDOFF_LABEL]},
                 f"创建交接提醒：{title}")

    for issue in gh.issues(HANDOFF_LABEL, "open"):
        m = MARKER_RE.search(issue.get("body") or "")
        note = by_path.get(m.group(1)) if m else None
        if note is None or is_waiting(note, tasks, latest):
            continue
        task = tasks.get(note["task"])
        state = f"owner: {task['owner']}，status: {task['status']}" if task else "任务已不存在"
        number = issue["number"]
        gh.write("POST", f"/issues/{number}/comments",
                 {"body": f"{note['task']} 当前 {state}，这张交接单已被处理，自动关闭。"},
                 f"评论交接提醒 #{number}")
        gh.write("PATCH", f"/issues/{number}", {"state": "closed", "state_reason": "completed"},
                 f"关闭交接提醒 #{number}")

    body = board_body(sha)
    boards = gh.issues(BOARD_LABEL, "open")
    if boards:
        number = boards[0]["number"]
        gh.write("PATCH", f"/issues/{number}", {"body": body}, f"更新看板 issue #{number}")
    else:
        gh.write("POST", "/issues", {"title": "📋 协作看板", "body": body, "labels": [BOARD_LABEL]},
                 "创建看板 issue")
    if gh.dry_run:
        print("\n----- 看板 issue 内容 -----\n" + body)


def main():
    p = argparse.ArgumentParser(description="把 .collab/ 的状态同步到 GitHub Issues")
    p.add_argument("--before", default="", help="本次推送之前的提交，用来找出新增的交接单")
    p.add_argument("--dry-run", action="store_true", help="只打印将要做的操作，不访问 GitHub")
    args = p.parse_args()
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not args.dry_run and not (repo and token):
        sys.exit("需要环境变量 GITHUB_REPOSITORY 和 GITHUB_TOKEN（本地预览请加 --dry-run）")
    try:
        sync(GitHub(repo or "OWNER/REPO", token, args.dry_run), args.before)
    except collab.CollabError as e:
        sys.exit(f"错误：{e}")
    except urllib.error.HTTPError as e:
        sys.exit(f"GitHub API 错误 {e.code}：{e.read().decode(errors='replace')[:500]}")


if __name__ == "__main__":
    main()
