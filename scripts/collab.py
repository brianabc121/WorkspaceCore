#!/usr/bin/env python3
"""Claude <-> Codex 协作状态工具（只依赖 Python 3.8+ 标准库）。

数据都在 .collab/ 下：
  tasks/T-xxxx*.md      一个任务一个文件，头部字段记录 owner（球权）和 status
  handoffs/*.md         一次交接一个文件，只增不改
  decisions/D-xxxx*.md  决策记录

协议说明见 AGENTS.md 和 .collab/README.md。
"""

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COLLAB = ROOT / ".collab"
TASKS_DIR = COLLAB / "tasks"
HANDOFFS_DIR = COLLAB / "handoffs"
DECISIONS_DIR = COLLAB / "decisions"
TEMPLATES_DIR = COLLAB / "templates"

AGENTS = ("claude", "codex")
ACTORS = AGENTS + ("human",)
OWNERS = ACTORS + ("unassigned",)
STATUSES = ("todo", "in_progress", "handoff", "blocked", "done", "cancelled")
ACTIVE = ("in_progress", "handoff", "blocked")
CLOSED = ("done", "cancelled")
HANDOFF_TYPES = ("continue", "rework", "escalate")
PRIORITIES = ("P0", "P1", "P2", "P3")
DECISION_STATUSES = ("proposed", "accepted", "superseded", "rejected")
PLACEHOLDER = "（待填写"
STALE_DAYS = 3

TASK_KEYS = ("id", "title", "owner", "status", "priority", "created", "updated")
HANDOFF_KEYS = ("task", "from", "to", "type", "created")
DECISION_KEYS = ("id", "title", "status", "date", "author")

GROUP_LABELS = {
    "claude": "Claude 手上",
    "codex": "Codex 手上",
    "human": "等人类处理",
    "unassigned": "未分配",
}
# 看板里同一组内的排序：最需要行动的排前面
STATUS_ORDER = {"handoff": 0, "in_progress": 1, "blocked": 2, "todo": 3}


class CollabError(Exception):
    pass


def die(msg):
    raise CollabError(msg)


def now():
    return dt.datetime.now(dt.timezone.utc)


def stamp():
    return now().strftime("%Y-%m-%d %H:%M UTC")


def today():
    return now().strftime("%Y-%m-%d")


def rel(path):
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def split_list(value):
    return [v.strip() for v in value.split(",") if v.strip()]


# ---------------------------------------------------------------- 文档读写

FM_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.S)


def parse_doc(text, where):
    text = text.replace("\r\n", "\n")
    m = FM_RE.match(text)
    if not m:
        die(f"{where}: 缺少用 --- 包裹的头部字段")
    meta = {}
    for n, line in enumerate(m.group(1).split("\n"), 2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            die(f"{where}:{n}: 头部字段应为 key: value")
        meta[key.strip()] = value.strip()
    return meta, text[m.end():]


class Doc:
    def __init__(self, path, meta, body):
        self.path, self.meta, self.body = path, meta, body

    @classmethod
    def load(cls, path):
        meta, body = parse_doc(path.read_text(encoding="utf-8"), rel(path))
        return cls(path, meta, body)

    def __getitem__(self, key):
        return self.meta.get(key, "")

    def save(self):
        head = "\n".join(f"{k}: {v}".rstrip() for k, v in self.meta.items())
        self.path.write_text(f"---\n{head}\n---\n{self.body}", encoding="utf-8")


def load_dir(directory, pattern="*.md"):
    """返回 (docs, errors)；解析失败的文件记入 errors 而不是中断。"""
    docs, errors = [], []
    for path in sorted(directory.glob(pattern)):
        try:
            docs.append(Doc.load(path))
        except CollabError as e:
            errors.append(str(e))
    return docs, errors


def load_strict(directory, pattern="*.md"):
    docs, errors = load_dir(directory, pattern)
    if errors:
        die("\n".join(errors) + "\n先修复上面的文件（可运行 check 查看全部问题）")
    return docs


def render_template(name, values):
    path = TEMPLATES_DIR / name
    if not path.exists():
        die(f"缺少模板 {rel(path)}")
    text = path.read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def norm_task_id(raw):
    m = re.fullmatch(r"(?:T-?)?(\d+)", raw.strip(), re.I)
    if not m:
        die(f"任务号格式应为 T-0001（收到 {raw!r}）")
    return f"T-{int(m.group(1)):04d}"


def owns_file(path, doc_id):
    return path.stem == doc_id or path.stem.startswith(doc_id + "-")


def get_task(raw_id):
    task_id = norm_task_id(raw_id)
    matches = [p for p in TASKS_DIR.glob(f"{task_id}*.md") if owns_file(p, task_id)]
    if not matches:
        die(f"找不到任务 {task_id}（.collab/tasks/{task_id}*.md）")
    if len(matches) > 1:
        die(f"任务 {task_id} 有多个文件：{', '.join(rel(p) for p in matches)}")
    return Doc.load(matches[0])


def handoffs_by_task(notes):
    by_task = {}
    for note in sorted(notes, key=lambda h: (h["created"], h.path.name)):
        by_task.setdefault(note["task"], []).append(note)
    return by_task


def append_log(task, who, message):
    message = " / ".join(line.strip() for line in message.strip().splitlines() if line.strip())
    body = task.body.rstrip("\n")
    if "## 进度日志" not in body:
        body += "\n\n## 进度日志"
    task.body = f"{body}\n- {stamp()} · {who} · {message}\n"
    task.meta["updated"] = today()


# ---------------------------------------------------------------- git（全部容错：失败返回 None）


def git(*args, timeout=60):
    try:
        r = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # 只去掉行尾：porcelain 输出的行首空格有意义
    return r.stdout.rstrip() if r.returncode == 0 else None


def main_ref():
    """共享主干的引用，优先 origin/HEAD 指向的分支。"""
    ref = git("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if ref:
        return ref
    for ref in ("refs/remotes/origin/main", "refs/remotes/origin/master", "refs/heads/main"):
        if git("rev-parse", "--verify", "--quiet", ref):
            return ref
    return None


def current_branch():
    """当前工作分支；处于主干或游离 HEAD 时返回空串。"""
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    main = main_ref() or ""
    if not branch or branch == "HEAD" or main.endswith("/" + branch):
        return ""
    return branch


def remote_refs():
    out = git("for-each-ref", "--format=%(refname)", "refs/remotes/origin") or ""
    return [r for r in out.splitlines() if r and not r.endswith("/HEAD")]


def ls_tree(ref, directory):
    out = git("ls-tree", "--name-only", ref, "--", directory.rstrip("/") + "/")
    return out.splitlines() if out else []


def dirty_outside_collab():
    out = git("status", "--porcelain")
    if not out:
        return []
    paths = [line[3:] for line in out.splitlines()]
    return [p for p in paths if not p.startswith(".collab/")]


# ---------------------------------------------------------------- 身份


def actor(args):
    who = (args.as_ or os.environ.get("COLLAB_AGENT", "")).strip().lower()
    if not who:
        die("请用 --as claude|codex|human 指明你的身份（或设置环境变量 COLLAB_AGENT）")
    if who not in ACTORS:
        die(f"身份必须是 {'/'.join(ACTORS)} 之一（收到 {who!r}）")
    return who


def require_owner(task, who):
    if who != "human" and task["owner"] != who:
        die(f"{task['id']} 的 owner 是 {task['owner']}，不是 {who}。只有 owner（或 human）能改它的状态。")


# ---------------------------------------------------------------- 展示


def latest_note_label(notes):
    if not notes:
        return ""
    n = notes[-1]
    return f"← {n['from']}·{n['type']} {n['created'][:10]}"


def updated_label(task):
    """更新日期；进行中/交接中/阻塞的任务超过 STALE_DAYS 天没动静时加上提醒。"""
    try:
        days = (now().date() - dt.date.fromisoformat(task["updated"])).days
    except ValueError:
        return task["updated"]
    if task["status"] in ACTIVE and days >= STALE_DAYS:
        return f"{task['updated']} ⚠ {days} 天未更新"
    return task["updated"]


def sort_key(task):
    return (STATUS_ORDER.get(task["status"], 9), task["priority"], task["id"])


def md_cell(text):
    return text.replace("|", "\\|") or "—"


def print_status(markdown):
    tasks = load_strict(TASKS_DIR, "T-*.md")
    notes = handoffs_by_task(load_strict(HANDOFFS_DIR))
    open_tasks = [t for t in tasks if t["status"] not in CLOSED]
    closed = [t for t in tasks if t["status"] in CLOSED]

    if markdown:
        print(f"## 任务看板（{len(open_tasks)} 个未关闭，{len(closed)} 个已关闭）\n")
    else:
        print(f"任务看板：{len(open_tasks)} 个未关闭，{len(closed)} 个已关闭（以当前检出的内容为准）")

    for owner in OWNERS:
        group = sorted((t for t in open_tasks if t["owner"] == owner), key=sort_key)
        label = f"{GROUP_LABELS[owner]} ({len(group)})"
        if markdown:
            print(f"### {label}\n")
            if not group:
                print("_无_\n")
                continue
            print("| 任务 | 状态 | 优先级 | 标题 | 分支 | 最近交接 | 更新 |")
            print("|---|---|---|---|---|---|---|")
            for t in group:
                print(
                    f"| {t['id']} | {t['status']} | {t['priority']} | {md_cell(t['title'])} "
                    f"| {md_cell(t['branch'])} | {md_cell(latest_note_label(notes.get(t['id'], [])))} "
                    f"| {md_cell(updated_label(t))} |"
                )
            print()
        else:
            print(f"\n■ {label}")
            for t in group:
                extra = latest_note_label(notes.get(t["id"], []))
                branch = f" [{t['branch']}]" if t["branch"] else ""
                print(
                    f"  {t['id']}  {t['status']:<11} {t['priority']}  {t['title']}"
                    f"{branch}  {extra}  (更新 {updated_label(t)})".rstrip()
                )

    ids = ", ".join(f"{t['id']}({t['status']})" for t in sorted(closed, key=lambda t: t["id"]))
    if markdown:
        print(f"### 已关闭 ({len(closed)})\n\n{ids or '_无_'}")
    else:
        print(f"\n✓ 已关闭 ({len(closed)}): {ids or '无'}")


def cmd_status(args):
    print_status(args.markdown)


def unmerged_handoffs(me, fetch):
    """其他远端分支上已提交、但还没进主干的、发给 me 的交接单。"""
    if fetch and git("fetch", "--quiet", "--prune", "origin", timeout=30) is None:
        print("（无法 fetch origin，以下基于本地已有的远端引用）")
    main = main_ref()
    known = set(ls_tree(main, ".collab/handoffs")) if main else set()
    known |= {rel(p) for p in HANDOFFS_DIR.glob("*.md")}
    found = {}
    for ref in remote_refs():
        if ref == main:
            continue
        for path in ls_tree(ref, ".collab/handoffs"):
            if path in known or path in found or not path.endswith(".md"):
                continue
            text = git("show", f"{ref}:{path}")
            if text is None:
                continue
            try:
                meta, _ = parse_doc(text, path)
            except CollabError:
                continue
            if meta.get("to") == me:
                found[path] = (ref.replace("refs/remotes/", "", 1), meta)
    return found


def cmd_inbox(args):
    me = actor(args)
    tasks = load_strict(TASKS_DIR, "T-*.md")
    notes = handoffs_by_task(load_strict(HANDOFFS_DIR))
    mine = sorted((t for t in tasks if t["owner"] == me), key=sort_key)

    def section(title, items, hint=""):
        print(f"\n■ {title} ({len(items)})")
        for line in items:
            print(f"  {line}")
        if items and hint:
            print(f"  → {hint}")

    print(f"{me} 的收件箱（{stamp()}）")

    pending = []
    for t in mine:
        if t["status"] != "handoff":
            continue
        last = notes.get(t["id"], [])
        note = last[-1] if last else None
        src = f"来自 {note['from']}·{note['type']}，交接单 {rel(note.path)}" if note else "（缺少交接单！）"
        pending.append(f"{t['id']} {t['priority']} {t['title']}  {src}")
    section("1. 待你接手的交接", pending,
            f"读完交接单、跑通其中的验证命令后：python3 scripts/collab.py start <任务号> --as {me}")

    section("2. 你进行中的任务",
            [f"{t['id']} {t['priority']} {t['title']}  (更新 {updated_label(t)})" for t in mine if t["status"] == "in_progress"])
    section("3. 你被阻塞的任务",
            [f"{t['id']} {t['priority']} {t['title']}" for t in mine if t["status"] == "blocked"])
    section("4. 已分配给你、待开始",
            [f"{t['id']} {t['priority']} {t['title']}" for t in mine if t["status"] == "todo"],
            f"python3 scripts/collab.py start <任务号> --as {me}")

    claimable = sorted((t for t in tasks if t["owner"] == "unassigned" and t["status"] == "todo"), key=sort_key)
    section("5. 未分配、可认领（需人类指派或同意）",
            [f"{t['id']} {t['priority']} {t['title']}" for t in claimable[:10]])

    others = [t for t in tasks if t["owner"] not in (me, "unassigned") and t["status"] in ACTIVE]
    section("6. 别人正在占用的路径（不要改动）",
            [f"{t['id']} {t['owner']}·{t['status']}  {t['paths'] or '（未声明 paths）'}" for t in others])

    if me != "human":
        future = unmerged_handoffs(me, fetch=not args.no_fetch)
        section("7. 尚未合并进主干的交接（仅供预告，合并后才生效）",
                [f"{m.get('task')} 来自 {m.get('from')}·{m.get('type')}  {path}  @ {ref}"
                 for path, (ref, m) in sorted(future.items())])


# ---------------------------------------------------------------- 写操作


def next_id(directory, prefix):
    """在工作区和所有远端分支里取最大编号 +1，减少并行分支撞号。"""
    pattern = re.compile(rf"{prefix}-(\d{{4}})")
    names = [p.name for p in directory.glob(f"{prefix}-*.md")]
    for ref in remote_refs():
        names += [Path(p).name for p in ls_tree(ref, rel(directory))]
    numbers = [int(m.group(1)) for n in names if (m := pattern.match(n))]
    return f"{prefix}-{max(numbers, default=0) + 1:04d}"


def slugify(text):
    """只从纯 ASCII 标题生成短名；中文标题用 --slug 指定，否则文件名只有编号。"""
    if not text.isascii():
        return ""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-")


def cmd_new(args):
    who = actor(args)
    owner = args.owner or "unassigned"
    if owner not in OWNERS:
        die(f"--owner 必须是 {'/'.join(OWNERS)} 之一")
    if args.priority not in PRIORITIES:
        die(f"--priority 必须是 {'/'.join(PRIORITIES)} 之一")
    depends = ", ".join(norm_task_id(d) for d in split_list(args.depends or ""))
    task_id = next_id(TASKS_DIR, "T")
    slug = slugify(args.slug or args.title)
    path = TASKS_DIR / (f"{task_id}-{slug}.md" if slug else f"{task_id}.md")
    text = render_template("task.md", {
        "id": task_id, "title": args.title.strip(), "owner": owner,
        "priority": args.priority, "paths": args.paths or "",
        "depends_on": depends, "date": today(),
    })
    meta, body = parse_doc(text, "templates/task.md")
    task = Doc(path, meta, body)
    append_log(task, who, f"创建任务（owner: {owner}）")
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    task.save()
    print(f"已创建 {rel(path)}\n请补全“目标”和“验收标准”两节。")


def cmd_decision(args):
    who = actor(args)
    dec_id = next_id(DECISIONS_DIR, "D")
    slug = slugify(args.slug or args.title)
    path = DECISIONS_DIR / (f"{dec_id}-{slug}.md" if slug else f"{dec_id}.md")
    text = render_template("decision.md", {
        "id": dec_id, "title": args.title.strip(), "date": today(), "author": who,
    })
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"已创建决策提案 {rel(path)}（status: proposed）\n"
          "影响协作协议或全局架构的决策只能由人类改成 accepted。")


def cmd_start(args):
    who = actor(args)
    task = get_task(args.task)
    status, owner, tid = task["status"], task["owner"], task["id"]
    is_human = who == "human"

    if status == "handoff":
        if owner != who and not is_human:
            die(f"{tid} 的交接是给 {owner} 的，不是给你的。")
        notes = handoffs_by_task(load_strict(HANDOFFS_DIR)).get(tid, [])
        message = f"接手交接 {notes[-1].path.name}" if notes else "接手交接"
    elif status == "todo":
        if owner not in (who, "unassigned") and not is_human:
            die(f"{tid} 已分配给 {owner}。")
        message = "开始任务"
    elif status == "blocked":
        require_owner(task, who)
        message = "解除阻塞，继续"
    elif status == "in_progress":
        if owner == who:
            print(f"{tid} 已经是你在进行中的任务，无需 start。")
            return
        die(f"{tid} 正由 {owner} 进行中。")
    else:
        die(f"{tid} 状态是 {status}，不能开始。重开需要人类决定。")

    task.meta["owner"] = who
    task.meta["status"] = "in_progress"
    branch = args.branch or current_branch()
    if branch:
        task.meta["branch"] = branch
    append_log(task, who, message + (f"：{args.note}" if args.note else ""))
    task.save()
    print(f"{tid} → in_progress（owner: {who}）")
    if status == "handoff":
        print("提醒：交接单里“不要改动”和“已知问题”两节务必看完。")


def cmd_handoff(args):
    who = actor(args)
    to, kind = args.to, args.type
    if to not in ACTORS or to == who:
        die(f"--to 必须是 {'/'.join(a for a in ACTORS if a != who)} 之一")
    if (kind == "escalate") != (to == "human"):
        die("交给 human 必须用 --type escalate；escalate 也只能交给 human。")
    task = get_task(args.task)
    require_owner(task, who)
    tid = task["id"]
    if task["status"] in CLOSED:
        die(f"{tid} 已经 {task['status']}，不能交接。")

    dirty = dirty_outside_collab()
    if dirty:
        print("警告：还有未提交的改动，交接单记录的 commit 不包含它们：\n  " + "\n  ".join(dirty[:10]))

    moment = now()
    branch = args.branch or current_branch() or task["branch"]
    base = f"{moment:%Y-%m-%d-%H%M}_{tid}_{who}-to-{to}"
    path, n = HANDOFFS_DIR / f"{base}.md", 2
    while path.exists():
        path, n = HANDOFFS_DIR / f"{base}-{n}.md", n + 1
    text = render_template("handoff.md", {
        "task": tid, "title": task["title"], "from": who, "to": to, "type": kind,
        "created": moment.strftime("%Y-%m-%d %H:%M UTC"), "branch": branch,
        "commit": git("rev-parse", "--short", "HEAD") or "",
        "summary": args.summary or "（待填写）",
    })
    HANDOFFS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

    task.meta["owner"] = to
    task.meta["status"] = "blocked" if to == "human" else "handoff"
    if branch:
        task.meta["branch"] = branch
    append_log(task, who, f"交接给 {to}（{kind}）→ {rel(path)}" + (f"：{args.summary}" if args.summary else ""))
    task.save()
    print(f"已生成交接单 {rel(path)}，{tid} → owner: {to}, status: {task['status']}\n"
          f"现在请把交接单里所有“{PLACEHOLDER}…）”填完——check 会拦截没填完的交接单。")


def cmd_set(args):
    who = actor(args)
    task = get_task(args.task)
    tid = task["id"]
    if args.owner:
        if who != "human":
            die("只有 human 能直接改 owner；代理之间转移请用 handoff。")
        if args.owner not in OWNERS:
            die(f"--owner 必须是 {'/'.join(OWNERS)} 之一")
    else:
        require_owner(task, who)
    if not args.status and not args.owner:
        die("至少指定 --status 或 --owner")
    if args.status == "handoff":
        die("交接请用 handoff 命令，它会同时生成交接单。")
    changes = []
    if args.owner and args.owner != task["owner"]:
        changes.append(f"owner {task['owner']} → {args.owner}")
        task.meta["owner"] = args.owner
    if args.status and args.status != task["status"]:
        if args.status == "done" and "- [ ]" in task.body:
            print("警告：任务里还有未勾选的验收标准（- [ ]），确认已全部满足再标 done。")
        changes.append(f"status {task['status']} → {args.status}")
        task.meta["status"] = args.status
    if not changes:
        print("没有变化。")
        return
    append_log(task, who, f"{'，'.join(changes)}：{args.note}")
    task.save()
    print(f"{tid}: {'，'.join(changes)}")


def cmd_log(args):
    who = actor(args)
    task = get_task(args.task)
    append_log(task, who, args.message)
    task.save()
    print(f"已追加到 {rel(task.path)} 的进度日志")


# ---------------------------------------------------------------- 校验


def overlaps(a, b):
    a, b = a.strip("/"), b.strip("/")
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def run_check():
    errors, warnings = [], []
    tasks, errs = load_dir(TASKS_DIR, "*.md")
    errors += errs
    notes, errs = load_dir(HANDOFFS_DIR)
    errors += errs
    decisions, errs = load_dir(DECISIONS_DIR)
    errors += errs

    by_id = {}
    for t in tasks:
        where = rel(t.path)
        missing = [k for k in TASK_KEYS if not t[k]]
        if missing:
            errors.append(f"{where}: 缺少字段 {', '.join(missing)}")
        tid = t["id"]
        if not re.fullmatch(r"T-\d{4}", tid):
            errors.append(f"{where}: id 应为 T-0000 格式")
        elif not owns_file(t.path, tid):
            errors.append(f"{where}: 文件名应以 {tid} 开头")
        if tid in by_id:
            errors.append(f"{where}: 任务号 {tid} 与 {rel(by_id[tid].path)} 重复（后合并的一方改号）")
        by_id[tid] = t
        if t["owner"] not in OWNERS:
            errors.append(f"{where}: owner 必须是 {'/'.join(OWNERS)} 之一")
        if t["status"] not in STATUSES:
            errors.append(f"{where}: status 必须是 {'/'.join(STATUSES)} 之一")
        if t["priority"] and t["priority"] not in PRIORITIES:
            errors.append(f"{where}: priority 必须是 {'/'.join(PRIORITIES)} 之一")
        if t["owner"] == "unassigned" and t["status"] not in ("todo", "cancelled"):
            errors.append(f"{where}: status 为 {t['status']} 的任务必须有 owner")

    for t in tasks:
        for dep in split_list(t["depends_on"]):
            if dep not in by_id:
                errors.append(f"{rel(t.path)}: depends_on 引用了不存在的任务 {dep}")

    for n in notes:
        where = rel(n.path)
        missing = [k for k in HANDOFF_KEYS if not n[k]]
        if missing:
            errors.append(f"{where}: 缺少字段 {', '.join(missing)}")
        if n["task"] and n["task"] not in by_id:
            errors.append(f"{where}: 指向不存在的任务 {n['task']}")
        for key in ("from", "to"):
            if n[key] and n[key] not in ACTORS:
                errors.append(f"{where}: {key} 必须是 {'/'.join(ACTORS)} 之一")
        if n["from"] and n["from"] == n["to"]:
            errors.append(f"{where}: from 和 to 不能相同")
        if n["type"] not in HANDOFF_TYPES:
            errors.append(f"{where}: type 必须是 {'/'.join(HANDOFF_TYPES)} 之一")
        elif (n["type"] == "escalate") != (n["to"] == "human"):
            errors.append(f"{where}: escalate 必须且只能交给 human")
        if PLACEHOLDER in n.body:
            errors.append(f"{where}: 交接单还有“{PLACEHOLDER}…）”没有填写")

    latest = {tid: ns[-1] for tid, ns in handoffs_by_task(notes).items()}
    for t in tasks:
        if t["status"] == "handoff":
            note = latest.get(t["id"])
            if not note:
                errors.append(f"{rel(t.path)}: status 为 handoff 但没有交接单")
            elif note["to"] != t["owner"]:
                errors.append(f"{rel(t.path)}: 最新交接单是给 {note['to']} 的，但 owner 是 {t['owner']}")

    active = [t for t in tasks if t["status"] in ACTIVE]
    for i, a in enumerate(active):
        for b in active[i + 1:]:
            if a["owner"] == b["owner"]:
                continue
            hits = sorted({f"{p}~{q}" for p in split_list(a["paths"]) for q in split_list(b["paths"]) if overlaps(p, q)})
            if hits:
                warnings.append(
                    f"{a['id']}({a['owner']}) 与 {b['id']}({b['owner']}) 的 paths 重叠：{', '.join(hits)}"
                )

    seen = {}
    for d in decisions:
        where = rel(d.path)
        missing = [k for k in DECISION_KEYS if not d[k]]
        if missing:
            errors.append(f"{where}: 缺少字段 {', '.join(missing)}")
        if not re.fullmatch(r"D-\d{4}", d["id"]):
            errors.append(f"{where}: id 应为 D-0000 格式")
        elif not owns_file(d.path, d["id"]):
            errors.append(f"{where}: 文件名应以 {d['id']} 开头")
        if d["id"] in seen:
            errors.append(f"{where}: 决策号 {d['id']} 与 {rel(seen[d['id']].path)} 重复")
        seen[d["id"]] = d
        if d["status"] not in DECISION_STATUSES:
            errors.append(f"{where}: status 必须是 {'/'.join(DECISION_STATUSES)} 之一")
    return errors, warnings


def cmd_check(args):
    errors, warnings = run_check()
    for w in warnings:
        print(f"警告：{w}")
    for e in errors:
        print(f"错误：{e}")
    if errors or (args.strict and warnings):
        print(f"check 未通过：{len(errors)} 个错误，{len(warnings)} 个警告")
        return 1
    print(f"check 通过（{len(warnings)} 个警告）")
    return 0


# ---------------------------------------------------------------- 入口


def build_parser():
    p = argparse.ArgumentParser(
        prog="collab.py",
        description="Claude ↔ Codex 协作状态工具。协议见 AGENTS.md / .collab/README.md",
    )
    sub = p.add_subparsers(dest="cmd", metavar="命令")
    sub.required = True
    who = argparse.ArgumentParser(add_help=False)
    who.add_argument("--as", dest="as_", metavar="身份", help="claude | codex | human（或设置 COLLAB_AGENT）")

    s = sub.add_parser("status", help="按 owner 分组显示任务看板")
    s.add_argument("--markdown", action="store_true", help="输出 Markdown（用于 CI Summary）")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("inbox", parents=[who], help="开工第一步：看哪些事等你处理")
    s.add_argument("--no-fetch", action="store_true", help="不 fetch 远端（离线环境）")
    s.set_defaults(func=cmd_inbox)

    s = sub.add_parser("new", parents=[who], help="新建任务")
    s.add_argument("--title", required=True)
    s.add_argument("--owner", help="默认 unassigned")
    s.add_argument("--priority", default="P2", help="P0-P3，默认 P2")
    s.add_argument("--paths", help="本任务会改动的目录/文件前缀，逗号分隔")
    s.add_argument("--depends", help="依赖的任务号，逗号分隔")
    s.add_argument("--slug", help="文件名里的英文短名，默认从标题生成")
    s.set_defaults(func=cmd_new)

    s = sub.add_parser("decision", parents=[who], help="新建决策提案 D-xxxx")
    s.add_argument("--title", required=True)
    s.add_argument("--slug")
    s.set_defaults(func=cmd_decision)

    s = sub.add_parser("start", parents=[who], help="开始/接手任务 → in_progress")
    s.add_argument("task")
    s.add_argument("--branch", help="记录工作分支，默认取当前分支")
    s.add_argument("-m", "--note", help="附加说明")
    s.set_defaults(func=cmd_start)

    s = sub.add_parser("handoff", parents=[who], help="交出球权并生成交接单")
    s.add_argument("task")
    s.add_argument("--to", required=True, choices=ACTORS)
    s.add_argument("--type", required=True, choices=HANDOFF_TYPES)
    s.add_argument("-m", "--summary", help="一句话摘要（写入交接单第 1 节）")
    s.add_argument("--branch", help="记录工作分支，默认取当前分支")
    s.set_defaults(func=cmd_handoff)

    s = sub.add_parser("set", parents=[who], help="改任务状态（done/blocked/...）")
    s.add_argument("task")
    s.add_argument("--status", choices=[x for x in STATUSES if x != "handoff"])
    s.add_argument("--owner", help="仅 human 可用")
    s.add_argument("-m", "--note", required=True, help="为什么改（写入日志）")
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("log", parents=[who], help="向任务进度日志追加一条")
    s.add_argument("task")
    s.add_argument("message")
    s.set_defaults(func=cmd_log)

    s = sub.add_parser("check", help="校验协作文件（CI 会运行）")
    s.add_argument("--strict", action="store_true", help="警告也算失败")
    s.set_defaults(func=cmd_check)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except CollabError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        # 输出被 head 之类截断时安静退出
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    sys.exit(main())
