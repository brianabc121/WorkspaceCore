"""知识库的纯函数（T-0098）：[[链接]] 解析与密钥扫描。"""

import uuid

from app.domain import knowledge_links as links
from app.domain import secret_scan


def test_links_cover_titles_aliases_sections_and_resources() -> None:
    task = uuid.uuid4()
    body = (
        "见 [[发布流程]] 和 [[发布流程|流程]]，以及 [[数据库迁移约定#回滚]]。\n"
        "跨库：[[工作台 V2/发布前检查清单]]；任务 [[task:" + str(task) + "]]。\n"
        "空的 [[ ]] 不算。"
    )
    refs = links.parse(body)
    assert [r.text for r in refs] == [
        "发布流程",
        "数据库迁移约定",
        "工作台 V2/发布前检查清单",
        f"task:{task}",
    ]
    qualified = refs[2]
    assert qualified.space_key == "工作台 v2" and qualified.rest_key == "发布前检查清单"
    assert refs[3].resource_type == "task" and refs[3].resource_id == task


def test_links_in_code_are_ignored() -> None:
    body = "正文 [[A]]\n```\n[[代码块里的]]\n```\n行内 `[[也不算]]` 结尾 [[B]]"
    assert [r.text for r in links.parse(body)] == ["A", "B"]


def test_link_count_is_capped() -> None:
    body = " ".join(f"[[n{i}]]" for i in range(links.MAX_LINKS + 50))
    assert len(links.parse(body)) == links.MAX_LINKS


def test_normalize_folds_width_case_and_spaces() -> None:
    assert links.normalize("  Ｒｅｌｅａｓｅ   Checklist ") == "release checklist"


def test_snippet_shows_the_line_around_the_link() -> None:
    body = "第一段。\n\n发布前按 [[发布前检查清单]] 逐项确认，再通知值班群。"
    assert "[[发布前检查清单]] 逐项确认" in links.snippet(body, "发布前检查清单")
    assert links.snippet(body, "不存在") == ""


def test_secret_scan_catches_common_secrets() -> None:
    samples = {
        "私钥": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc",
        "GitHub 令牌": "token ghp_" + "a" * 36,
        "AWS 访问密钥": "AKIA" + "ABCDEFGHIJKLMNOP",
        "模型服务的 API 密钥": "sk-proj-" + "x" * 30,
        "工作台实例令牌": "mwa_" + "y" * 40,
        "JWT 令牌": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturepart",
        "链接里的用户名和密码": "postgresql://admin:hunter22@db:5432/app",
        "密码或密钥的明文": "数据库密码：S3cretPassw0rd",
    }
    for label, text in samples.items():
        assert label in secret_scan.scan(text), label


def test_secret_scan_allows_references_to_the_vault() -> None:
    harmless = [
        "密码：见密钥库里的 db_password",
        "token: ${MYWS_INSTANCE_TOKEN}",
        "api_key = <在密钥库里>",
        "重启前先在群里报告，等审批通过再重启。",
        "Bearer 令牌放在请求头里",
    ]
    for text in harmless:
        assert secret_scan.scan(text) == [], text
