"""AFFiNE 深链接的校验与规范化（核验 ADR 第 8.3 节）：纯字符串规则，不发请求。"""

import pytest

from app.domain.deeplinks import LinkError, build_url, parse_doc_link

BASE = "https://affine.example.com"
SUB = "https://example.com/affine"


def test_workspace_doc_link_is_normalized() -> None:
    link = parse_doc_link(
        f"{BASE}/workspace/0f1e2d3c-aaaa-bbbb-cccc-000000000001/AbC_dEf-12"
        "?refreshKey=x&mode=edgeless&blockIds=b1,b2&utm_source=mail#section",
        BASE,
    )
    assert link.link_kind == "workspace_doc"
    assert link.affine_workspace_id == "0f1e2d3c-aaaa-bbbb-cccc-000000000001"
    assert link.doc_id == "AbC_dEf-12"
    # 只保留白名单参数（按固定顺序），丢弃 refreshKey、其他参数与片段
    assert link.suffix == (
        "/workspace/0f1e2d3c-aaaa-bbbb-cccc-000000000001/AbC_dEf-12?mode=edgeless&blockIds=b1,b2"
    )
    assert build_url(BASE, link.suffix).startswith(f"{BASE}/workspace/")


def test_share_link_and_sub_path() -> None:
    share = parse_doc_link(f"{BASE}/share/ws1/doc1/", BASE)
    assert (share.link_kind, share.suffix) == ("share", "/share/ws1/doc1")
    sub = parse_doc_link(f"{SUB}/workspace/ws1/doc1?mode=page", SUB)
    assert sub.suffix == "/workspace/ws1/doc1?mode=page"
    assert build_url(SUB, sub.suffix) == f"{SUB}/workspace/ws1/doc1?mode=page"


def test_host_comparison_ignores_case_and_default_port() -> None:
    link = parse_doc_link("https://AFFINE.example.com:443/workspace/ws/doc", BASE)
    assert link.doc_id == "doc"
    dev = parse_doc_link("http://localhost:3010/workspace/ws/doc", "http://localhost:3010")
    assert dev.affine_workspace_id == "ws"


def test_unknown_mode_is_dropped() -> None:
    assert parse_doc_link(f"{BASE}/workspace/ws/doc?mode=slides", BASE).suffix == (
        "/workspace/ws/doc"
    )


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("javascript:alert(1)", "scheme_not_allowed"),
        ("data:text/html,<b>x</b>", "scheme_not_allowed"),
        ("file:///etc/passwd", "scheme_not_allowed"),
        ("http://affine.example.com/workspace/ws/doc", "scheme_not_allowed"),
        ("https://user:pw@affine.example.com/workspace/ws/doc", "credentials_not_allowed"),
        ("https://evil.example.com/workspace/ws/doc", "host_mismatch"),
        ("https://affine.example.com.evil.com/workspace/ws/doc", "host_mismatch"),
        ("https://affine.example.com:8443/workspace/ws/doc", "host_mismatch"),
        ("http://169.254.169.254/workspace/ws/doc", "scheme_not_allowed"),
        ("https://169.254.169.254/workspace/ws/doc", "host_mismatch"),
        ("https://affine.example.com:notaport/x", "host_mismatch"),
        ("https://affine.example.com/", "not_a_document"),
        ("https://affine.example.com/workspace/ws", "not_a_document"),
        ("https://affine.example.com/workspace/ws/all", "not_a_document"),
        ("https://affine.example.com/workspace/ws/collection/c1", "not_a_document"),
        ("https://affine.example.com/workspace/ws/doc/attachments/a1", "not_a_document"),
        ("https://affine.example.com/template/import", "not_a_document"),
        ("https://affine.example.com/workspace/w%20s/doc", "invalid_id"),
        ("https://affine.example.com/workspace/ws/" + "d" * 65, "invalid_id"),
    ],
)
def test_rejected_links(url: str, reason: str) -> None:
    with pytest.raises(LinkError) as raised:
        parse_doc_link(url, BASE)
    assert raised.value.reason == reason
    assert url not in str(raised.value)  # 错误消息不回显链接


def test_sub_path_must_match() -> None:
    with pytest.raises(LinkError) as raised:
        parse_doc_link("https://example.com/other/workspace/ws/doc", SUB)
    assert raised.value.reason == "host_mismatch"
    with pytest.raises(LinkError):
        parse_doc_link("https://example.com/affinex/workspace/ws/doc", SUB)
