"""IM 媒体的 HTTP 应答（T-0119）：图片内联，文件一律按附件下载，视频支持 Range（拖动进度条）。"""

import re
from urllib.parse import quote

from fastapi import Response

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def media_response(
    data: bytes,
    mime: str,
    *,
    kind: str,
    file_name: str | None,
    range_header: str | None,
    cache: str,
) -> Response:
    headers = {
        "Cache-Control": cache,
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Accept-Ranges": "bytes" if kind == "video" else "none",
    }
    if kind == "file":
        name = file_name or "file"
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
        # 文件不按声明的类型渲染，防止被当成网页执行
        mime = "application/octet-stream"
    else:
        headers["Content-Disposition"] = "inline"
    if kind == "video" and range_header:
        match = _RANGE.match(range_header.strip())
        size = len(data)
        if match and (match.group(1) or match.group(2)):
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else size - 1
            else:
                start = max(0, size - int(match.group(2)))
                end = size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                return Response(
                    status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"}
                )
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            return Response(
                content=data[start : end + 1], status_code=206, media_type=mime, headers=headers
            )
    return Response(content=data, media_type=mime, headers=headers)
