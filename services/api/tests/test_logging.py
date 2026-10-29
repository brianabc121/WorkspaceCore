import json
import logging

from app.core.logging import JsonFormatter, request_id_var


def _format(record: logging.LogRecord) -> dict[str, object]:
    result: dict[str, object] = json.loads(JsonFormatter().format(record))
    return result


def test_log_line_is_json_with_extra_fields() -> None:
    record = logging.makeLogRecord(
        {"name": "app.test", "levelname": "INFO", "msg": "你好 %s", "args": ("世界",), "path": "/x"}
    )
    payload = _format(record)
    assert payload["message"] == "你好 世界"
    assert payload["logger"] == "app.test"
    assert payload["path"] == "/x"
    assert "request_id" not in payload


def test_log_line_includes_current_request_id() -> None:
    token = request_id_var.set("req_abc")
    try:
        payload = _format(logging.makeLogRecord({"msg": "hello"}))
    finally:
        request_id_var.reset(token)
    assert payload["request_id"] == "req_abc"
