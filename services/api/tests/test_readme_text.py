"""README 正文处理的纯函数（T-0122，方案第 9.4、9.6 节）。"""

import pytest

from app.domain import readme_text as rt

BODY = (
    "# 发布 2.0\n\n## 简介\n\n准备 2.0。\n\n## 成员\n"
    + rt.block("成员", "- 人：张三")
    + "\n\n## 代码与资源\n\n（由人或智能体填写）\n\n## 约定\n\n- 小步提交\n"
)


def test_merge_only_replaces_inside_markers() -> None:
    merged = rt.merge(BODY + "\n手写的结尾\n", {"成员": "- 人：张三、李四", "沟通": "不存在的块"})
    assert "- 人：张三、李四" in merged and "- 人：张三\n" not in merged
    assert merged.endswith("手写的结尾\n")
    assert "## 代码与资源\n\n（由人或智能体填写）" in merged
    assert "不存在的块" not in merged  # 没有这个标记就不补回去
    assert rt.merge(merged, {"成员": "- 人：张三、李四"}) == merged  # 没变化就完全一样
    assert rt.auto_names(merged) == ["成员"]


def test_removed_marker_stops_updates() -> None:
    handwritten = BODY.replace(rt.OPEN.format(name="成员") + "\n", "").replace(rt.CLOSE, "")
    assert rt.auto_names(handwritten) == []
    assert rt.merge(handwritten, {"成员": "新的"}) == handwritten


def test_edit_section_replace_append_and_new() -> None:
    replaced = rt.edit_section(BODY, "代码与资源", "- 仓库：git.example/repo", "replace")
    assert "## 代码与资源\n\n- 仓库：git.example/repo\n\n## 约定" in replaced
    appended = rt.edit_section(replaced, "约定", "- 合并前跑测试", "append")
    assert rt.section(appended, "约定") == "- 小步提交\n\n- 合并前跑测试"
    added = rt.edit_section(appended, "进展与交接", "做完了接口", "append")
    assert added.endswith("## 进展与交接\n\n做完了接口\n")
    assert rt.section(added, "成员") is not None and rt.auto_names(added) == ["成员"]
    assert rt.headings(added)[-1] == "进展与交接"


@pytest.mark.parametrize(
    ("title", "text", "code"),
    [
        ("成员", "改成员", "AUTO_SECTION"),
        ("", "x", "VALIDATION_FAILED"),
        ("a#b", "x", "VALIDATION_FAILED"),
        ("约定", "## 偷偷加一个部分", "VALIDATION_FAILED"),
        ("约定", "<!-- myws:auto 成员 -->", "VALIDATION_FAILED"),
        ("约定", "x" * (rt.MAX_SECTION + 1), "VALIDATION_FAILED"),
    ],
)
def test_edit_section_refuses(title: str, text: str, code: str) -> None:
    with pytest.raises(rt.SectionError) as error:
        rt.edit_section(BODY, title, text, "replace")
    assert error.value.code == code


def test_helpers() -> None:
    assert rt.is_auto(BODY, "成员") and not rt.is_auto(BODY, "约定")
    assert rt.checklist("要做：\n- [ ] 接口\n- [x] 文档\n普通一行\n* [ ] 测试") == (
        "- [ ] 接口\n- [x] 文档\n* [ ] 测试"
    )
    assert rt.first_paragraph("\n\n第一段\n续行\n\n第二段") == "第一段\n续行"
    assert rt.first_paragraph("长" * 700).endswith("…")
    parts = rt.split_blocks(BODY)
    assert [name for name, _ in parts] == [None, "成员", None]
    assert parts[1][1] == "- 人：张三"
