"""官方程序的压缩钩子（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 4.3；核验记录第 1、2 节）。

管理器把这个文件原样复制到数据卷（`hooks/hook.py`），
经 `--settings` 交给 Claude Code / CodeBuddy Code，
由它们在压缩前后用 `<python> -I hook.py <事件>` 调用。脚本是固定的，内容不由模型决定；
只做两件事，不联网、不执行收到的任何内容：

- pre_compact：在标准输出里写一段固定的说明“压缩时要保留什么、摘要按哪几栏写”
  （Claude Code 把 PreCompact 钩子的输出并进压缩说明；CodeBuddy 没核实是否生效）；
- post_compact：从标准输入读官方程序给的 JSON（session_id、trigger、compact_summary），
  把摘要写到 `$BRIDGE_HOOK_DIR/summaries/<session_id>.jsonl`，由管理器在回合结束后取走。

钩子出任何错都安静退出（退出码 0，什么也不输出）：不能因为钩子让压缩失败或被阻止。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

PRESERVE = (
    "压缩时请务必保留：做过的决定和原因、还没解决的问题、改过的文件和跑过的关键命令、用户明确说过的要求。"
    "摘要请按这四栏写，每栏用短句：结论和决定；没解决的问题；涉及的文件和命令；后续要做的。"
)
MAX_SUMMARY_CHARS = 20_000
_SESSION = re.compile(r"^[\w.\-:]{1,100}$")


def _read_input() -> dict[str, object]:
    try:
        data = json.loads(sys.stdin.read(1_000_000) or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def post_compact(data: dict[str, object], root: Path) -> None:
    session = str(data.get("session_id") or "")
    summary = data.get("compact_summary")
    if not _SESSION.match(session) or not isinstance(summary, str) or not summary.strip():
        return
    trigger = data.get("trigger")
    line = {
        "at": time.time(),
        "trigger": trigger if trigger in ("auto", "manual") else None,
        "summary": summary[:MAX_SUMMARY_CHARS],
    }
    folder = root / "summaries"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{session}.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")


def install(data_dir: Path) -> Path:
    """把本文件原样复制到数据卷（`hooks/hook.py`）：官方程序按这个路径调用。返回脚本的路径。"""
    target = data_dir / "hooks" / "hook.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).read_bytes()
    if not target.exists() or target.read_bytes() != source:
        target.write_bytes(source)
        target.chmod(0o644)
    return target


def settings_json(python: str, script: Path) -> str:
    """交给官方程序 `--settings` 的钩子配置（Claude Code 与 CodeBuddy Code 的格式相同）。"""
    import shlex

    def command(event: str) -> list[dict[str, object]]:
        line = f"{shlex.quote(python)} -I {shlex.quote(str(script))} {event}"
        return [{"hooks": [{"type": "command", "command": line, "timeout": 10}]}]

    return json.dumps(
        {"hooks": {"PreCompact": command("pre_compact"), "PostCompact": command("post_compact")}}
    )


def main(argv: list[str]) -> int:
    event = argv[1] if len(argv) > 1 else ""
    try:
        if event == "pre_compact":
            _read_input()
            sys.stdout.write(PRESERVE)
        elif event == "post_compact":
            root = os.environ.get("BRIDGE_HOOK_DIR")
            if root:
                post_compact(_read_input(), Path(root))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
