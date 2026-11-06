"""IM 附件：图片、文件、视频（T-0119，docs/v2/AGENT_MESSAGING_PLAN.md 第 4.2 ~ 4.5 节）。

- 识别：以文件头为准（图片沿用 app/domain/images.py；MP4 / MOV 看 `ftyp`，从 `mvhd` 读时长），
  不信扩展名和对方给的 Content-Type；读不出时长的视频按文件发。
- 不允许：可执行程序与脚本、网页类（扩展名或文件头任一命中）。
- 文字类附件过密钥扫描（与知识库同一套，app/domain/secret_scan.py）。
- 网址：只连公网地址，每次跳转都重新检查（最多 3 次），连接 5 秒、总共 10 秒，边下边数字节。
- 元素字段见核验记录 2.1（OpenIM v3.8.3-patch.18 `pkg/apistruct/msg.go`）：视频必须带封面和时长，
  智能体没给封面时用工作台自带的默认封面图。
"""

import asyncio
import base64
import binascii
import hashlib
import math
import struct
import uuid
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

import httpx
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain import secret_scan
from app.domain.images import sniff
from app.integrations.base import ConnectorError
from app.integrations.http import OutboundPolicy
from app.integrations.openim import ObjectStore, OpenIMClient
from app.models import AgentInstance, ImGroup, ImGroupMember, ImMedia


class AttachmentError(Exception):
    """附件不能发：code 与 MCP 工具错误码一致（调用方换成 ToolCallError）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


MediaKind = Literal["image", "file", "video"]
CONTENT_PICTURE = 102
CONTENT_VIDEO = 104
CONTENT_FILE = 105
CONTENT_FOR_KIND: dict[str, int] = {
    "image": CONTENT_PICTURE,
    "video": CONTENT_VIDEO,
    "file": CONTENT_FILE,
}

MB = 1024 * 1024
LIMITS: dict[str, int] = {"image": 10 * MB, "file": 20 * MB, "video": 50 * MB}
INLINE_LIMIT = 512 * 1024
TEXT_FILE_LIMIT = 1 * MB
ATTACHMENTS_PER_HOUR = 30
BYTES_PER_DAY = 200 * MB
HOUR_KEY = "im_attach_hour:{instance_id}"
DAY_KEY = "im_attach_bytes:{instance_id}"
FETCH_CONNECT_SECONDS = 5.0
FETCH_TOTAL_SECONDS = 10.0
MAX_REDIRECTS = 3
THUMB_SIZE = 256
MEDIA_TTL = timedelta(days=1)

BLOCKED_EXTENSIONS = frozenset(
    {
        "exe", "msi", "bat", "cmd", "com", "scr", "pif", "ps1", "psm1", "vbs", "vbe", "js",
        "jse", "wsf", "wsh", "hta", "sh", "bash", "zsh", "csh", "apk", "aab", "ipa", "dmg",
        "pkg", "deb", "rpm", "jar", "app", "lnk", "reg", "dll", "so", "dylib", "html", "htm",
        "xhtml", "shtml", "svg", "svgz", "mht", "mhtml", "xml", "xsl",
    }
)  # fmt: skip
TEXT_EXTENSIONS = frozenset(
    {
        "txt", "md", "markdown", "csv", "tsv", "json", "jsonl", "yaml", "yml", "toml", "ini",
        "cfg", "conf", "env", "log", "sql", "py", "ts", "tsx", "vue", "go", "rs", "java", "kt",
        "c", "h", "cpp", "hpp", "cs", "rb", "php", "swift", "patch", "diff",
    }
)  # fmt: skip
TEXT_MIME = {"md": "text/markdown", "csv": "text/csv", "json": "application/json"}
# 文件头命中这些就当成可执行或网页（扩展名改过也拦）
BLOCKED_MAGIC = (b"MZ", b"\x7fELF", b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"#!")
HTML_MARKERS = (b"<!doctype html", b"<html", b"<svg", b"<script")


@dataclass(frozen=True)
class Detected:
    kind: MediaKind
    mime: str
    ext: str
    width: int = 0
    height: int = 0
    duration_seconds: int | None = None


@dataclass
class Prepared:
    """一个附件准备好之后：已上传、已有媒体行，等着放进发件箱。"""

    media: ImMedia
    content_type: int
    content_json: dict[str, Any]
    summary: str
    cover: ImMedia | None = None


# ---------------------------------------------------------------- 识别


def _ext(name: str | None) -> str:
    if not name or "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()[:16]


def safe_file_name(name: str | None, fallback: str) -> str:
    raw = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    cleaned = "".join(c for c in raw if c.isprintable() and c not in '<>:"|?*').strip(" .")
    return (cleaned or fallback)[:128]


def mp4_duration(data: bytes) -> int | None:
    """MP4 / MOV 的 `mvhd`：v0 是 32 位时间、v1 是 64 位；返回向上取整的秒数。"""
    at = data.find(b"mvhd")
    if at < 4:
        return None
    body = data[at + 4 :]
    if len(body) < 32:
        return None
    version = body[0]
    try:
        if version == 1:
            timescale, duration = struct.unpack(">IQ", body[20:32])
        else:
            timescale, duration = struct.unpack(">II", body[12:20])
    except struct.error:
        return None
    if timescale <= 0 or duration <= 0:
        return None
    seconds = math.ceil(duration / timescale)
    return seconds if 0 < seconds <= 24 * 3600 else None


def detect(data: bytes, name: str | None, wanted: str = "auto") -> Detected:
    ext = _ext(name)
    head = data[:512].lstrip().lower()
    if ext in BLOCKED_EXTENSIONS or data.startswith(BLOCKED_MAGIC) or head.startswith(HTML_MARKERS):
        raise AttachmentError(
            "ATTACHMENT_TYPE_NOT_ALLOWED", "不允许发送可执行程序、脚本或网页类文件"
        )
    image = sniff(data)
    if image is not None and wanted in ("auto", "image"):
        return Detected("image", image.mime, image.ext, image.width, image.height)
    if wanted == "image":
        raise AttachmentError(
            "ATTACHMENT_TYPE_NOT_ALLOWED", "kind 是 image，但内容不是 PNG / JPEG / GIF / WebP 图片"
        )
    if len(data) >= 12 and data[4:8] == b"ftyp" and wanted in ("auto", "video"):
        brand = data[8:12]
        mime, video_ext = ("video/quicktime", "mov") if brand == b"qt  " else ("video/mp4", "mp4")
        duration = mp4_duration(data)
        if duration is not None:
            return Detected("video", mime, video_ext, duration_seconds=duration)
        if wanted == "video":
            raise AttachmentError(
                "ATTACHMENT_TYPE_NOT_ALLOWED", "读不出视频时长；可以改用 kind=file 按文件发"
            )
    if wanted == "video":
        raise AttachmentError(
            "ATTACHMENT_TYPE_NOT_ALLOWED", "kind 是 video，但内容不是 MP4 / MOV 视频"
        )
    if image is not None:
        return Detected("file", image.mime, image.ext)
    mime = TEXT_MIME.get(
        ext, "text/plain" if ext in TEXT_EXTENSIONS else "application/octet-stream"
    )
    return Detected("file", mime, ext or "bin")


def _texty(data: bytes, ext: str) -> str | None:
    if ext not in TEXT_EXTENSIONS and ext:
        return None
    try:
        return data[: 2 * MB].decode("utf-8")
    except UnicodeDecodeError:
        return None


def check_secrets(data: bytes, ext: str) -> None:
    text = _texty(data, ext)
    if text is not None and secret_scan.scan(text):
        raise AttachmentError(
            "ATTACHMENT_SECRET_BLOCKED",
            "附件里像是有令牌、私钥或密码，没有发送；去掉这些内容后再发",
        )


# ---------------------------------------------------------------- 来源


async def fetch_url(url: str, limit: int, transport: httpx.AsyncBaseTransport | None) -> bytes:
    """只取公网地址；每次跳转都重新检查；超过 limit 立即断开。"""
    policy = OutboundPolicy()
    current = url
    timeout = httpx.Timeout(FETCH_TOTAL_SECONDS, connect=FETCH_CONNECT_SECONDS)
    try:
        async with asyncio.timeout(FETCH_TOTAL_SECONDS):
            async with httpx.AsyncClient(
                transport=transport, timeout=timeout, follow_redirects=False
            ) as client:
                for _ in range(MAX_REDIRECTS + 1):
                    if urlsplit(current).scheme not in ("http", "https"):
                        raise AttachmentError("ATTACHMENT_FETCH_FAILED", "只支持 http / https 网址")
                    try:
                        pinned = await policy.check(current)
                    except ConnectorError:
                        raise AttachmentError(
                            "ATTACHMENT_FETCH_FAILED", "这个网址不能访问（只能取公网地址）"
                        ) from None
                    target = httpx.URL(current)
                    headers = {"User-Agent": "MyWorkspace-Attachment/1.0", "Accept": "*/*"}
                    extensions: dict[str, Any] = {}
                    if pinned:
                        headers["Host"] = target.netloc.decode("ascii")
                        if target.scheme == "https":
                            extensions["sni_hostname"] = target.host
                        target = target.copy_with(host=pinned)
                    request = client.build_request(
                        "GET", target, headers=headers, extensions=extensions
                    )
                    response = await client.send(request, stream=True)
                    try:
                        if response.is_redirect:
                            location = response.headers.get("location", "")
                            current = urljoin(current, location)
                            continue
                        if response.status_code >= 400:
                            raise AttachmentError(
                                "ATTACHMENT_FETCH_FAILED", f"网址返回 HTTP {response.status_code}"
                            )
                        declared = int(response.headers.get("content-length") or 0)
                        if declared > limit:
                            raise _too_large(limit)
                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > limit:
                                raise _too_large(limit)
                            chunks.append(chunk)
                        return b"".join(chunks)
                    finally:
                        await response.aclose()
                raise AttachmentError("ATTACHMENT_FETCH_FAILED", "跳转次数太多")
    except TimeoutError:
        raise AttachmentError(
            "ATTACHMENT_FETCH_FAILED", f"{int(FETCH_TOTAL_SECONDS)} 秒内没有取完"
        ) from None
    except httpx.HTTPError:
        raise AttachmentError("ATTACHMENT_FETCH_FAILED", "取不到这个网址") from None


def _too_large(limit: int) -> AttachmentError:
    return AttachmentError("ATTACHMENT_TOO_LARGE", f"附件超过 {limit // MB} MB")


def _base64(value: str) -> bytes:
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise AttachmentError("VALIDATION_FAILED", "data_base64 不是合法的 base64") from None
    if len(data) > INLINE_LIMIT:
        raise AttachmentError("ATTACHMENT_TOO_LARGE", "内联附件最多 512 KB；大文件请给网址")
    return data


async def instance_conversations(db: AsyncSession, instance: AgentInstance) -> set[str]:
    """实例自己的会话：与管理员的私聊（会话 ID 里有它）和当前所在的群。"""
    groups = (
        await db.scalars(
            select(ImGroup.group_id)
            .join(ImGroupMember, ImGroupMember.group_pk == ImGroup.id)
            .where(ImGroupMember.instance_id == instance.id, ImGroup.dismissed_at.is_(None))
        )
    ).all()
    return {f"sg_{g}" for g in groups}


async def _ref(
    db: AsyncSession, client: OpenIMClient, instance: AgentInstance, ref: str
) -> tuple[bytes, str | None]:
    kind, _, ident = ref.partition(":")
    try:
        ref_id = uuid.UUID(ident)
    except ValueError:
        raise AttachmentError("VALIDATION_FAILED", "ref 的格式是 im_media:<ID>") from None
    if kind != "im_media":
        raise AttachmentError(
            "VALIDATION_FAILED", "ref 只支持 im_media:<ID>（你的会话里收到或发出过的媒体）"
        )
    media = await db.scalar(
        select(ImMedia).where(
            ImMedia.id == ref_id,
            ImMedia.workspace_id == instance.workspace_id,
            ImMedia.status == "used",
        )
    )
    allowed = (
        media is not None
        and media.conversation_id is not None
        and (
            instance.im_user_id in media.conversation_id
            or media.conversation_id in await instance_conversations(db, instance)
        )
    )
    if media is None or not allowed:
        raise AttachmentError("ATTACHMENT_NOT_FOUND", "找不到这个媒体，或它不在你的会话里")
    try:
        data, _ = await ObjectStore(client).download(media.object_name)
    except ConnectorError:
        raise AttachmentError("EXTERNAL_UNAVAILABLE", "暂时取不到这个媒体，稍后再试") from None
    return data, media.file_name or f"{media.id}.{_ext(media.object_name) or 'bin'}"


# ---------------------------------------------------------------- 元素


def media_url(base: str, media_id: uuid.UUID, *, thumb: bool = False) -> str:
    return f"{base}/im/obj/m/{media_id}" + ("?thumb=1" if thumb else "")


def picture_elem(media: ImMedia, base: str) -> dict[str, Any]:
    """OpenIM 的 PictureElem：原图与大图是同一个地址，缩略图带 thumb=1（最长边 256）。"""
    scale = min(1.0, THUMB_SIZE / max(media.width, media.height))

    def info(url: str, width: int, height: int) -> dict[str, Any]:
        return {
            "uuid": str(media.id),
            "type": media.mime,
            "size": media.size,
            "width": width,
            "height": height,
            "url": url,
        }

    full = media_url(base, media.id)
    return {
        "sourcePath": "",
        "sourcePicture": info(full, media.width, media.height),
        "bigPicture": info(full, media.width, media.height),
        "snapshotPicture": info(
            media_url(base, media.id, thumb=True),
            max(1, round(media.width * scale)),
            max(1, round(media.height * scale)),
        ),
    }


def file_elem(media: ImMedia, base: str) -> dict[str, Any]:
    return {
        "filePath": "",
        "uuid": str(media.id),
        "sourceUrl": media_url(base, media.id),
        "fileName": media.file_name or f"{media.id}",
        "fileSize": media.size,
        "fileType": media.mime,
    }


def video_elem(media: ImMedia, cover: ImMedia, base: str) -> dict[str, Any]:
    return {
        "videoPath": "",
        "videoUUID": str(media.id),
        "videoUrl": media_url(base, media.id),
        "videoType": media.mime,
        "videoSize": media.size,
        "duration": media.duration_seconds or 1,
        "snapshotPath": "",
        "snapshotUUID": str(cover.id),
        "snapshotSize": cover.size,
        "snapshotUrl": media_url(base, cover.id),
        "snapshotWidth": cover.width,
        "snapshotHeight": cover.height,
    }


def element_for(
    media: ImMedia, base: str, cover: ImMedia | None = None
) -> tuple[int, dict[str, Any]]:
    if media.kind == "video":
        if cover is None:
            raise ValueError("视频要有封面")
        return CONTENT_VIDEO, video_elem(media, cover, base)
    if media.kind == "file":
        return CONTENT_FILE, file_elem(media, base)
    return CONTENT_PICTURE, picture_elem(media, base)


def _png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(tag + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", crc)

    row = b"\x00" + bytes(rgb) * width
    raw = zlib.compress(row * height, 9)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


# 默认的视频封面：320 × 180 的深灰底图（方案第 11 节 D9，不引入 ffmpeg）
DEFAULT_COVER = _png(320, 180, (55, 65, 81))


# ---------------------------------------------------------------- 上传


async def store_media(
    db: AsyncSession,
    client: OpenIMClient,
    *,
    workspace_id: uuid.UUID,
    data: bytes,
    detected: Detected,
    file_name: str | None,
    instance_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> ImMedia:
    media_id = uuid.uuid4()
    object_name = f"myws/{workspace_id}/{'agent/' if instance_id else ''}{media_id}.{detected.ext}"
    try:
        await ObjectStore(client).upload(object_name, data, detected.mime)
    except ConnectorError:
        raise AttachmentError("EXTERNAL_UNAVAILABLE", "暂时传不上去，稍后再试") from None
    media = ImMedia(
        id=media_id,
        workspace_id=workspace_id,
        uploaded_by=user_id,
        instance_id=instance_id,
        kind=detected.kind,
        file_name=file_name,
        duration_seconds=detected.duration_seconds,
        sha256=hashlib.sha256(data).hexdigest(),
        mime=detected.mime,
        size=len(data),
        width=detected.width,
        height=detected.height,
        object_name=object_name,
        expires_at=datetime.now(UTC) + MEDIA_TTL,
    )
    db.add(media)
    await db.flush()
    return media


@dataclass
class AttachmentSource:
    url: str | None = None
    data_base64: str | None = None
    text_content: str | None = None
    ref: str | None = None
    filename: str | None = None
    kind: str = "auto"


async def _bytes_of(
    db: AsyncSession,
    client: OpenIMClient,
    instance: AgentInstance,
    source: AttachmentSource,
    transport: httpx.AsyncBaseTransport | None,
) -> tuple[bytes, str | None]:
    if source.data_base64 is not None:
        return _base64(source.data_base64), source.filename
    if source.text_content is not None:
        data = source.text_content.encode("utf-8")
        if len(data) > TEXT_FILE_LIMIT:
            raise AttachmentError("ATTACHMENT_TOO_LARGE", "文字成文件最多 1 MB")
        if not source.filename or _ext(source.filename) not in TEXT_EXTENSIONS:
            raise AttachmentError(
                "VALIDATION_FAILED",
                "text_content 要带 filename，扩展名如 .md / .txt / .csv / .json",
            )
        return data, source.filename
    if source.ref is not None:
        data, name = await _ref(db, client, instance, source.ref)
        return data, source.filename or name
    if source.url is not None:
        limit = LIMITS.get(source.kind, max(LIMITS.values()))
        data = await fetch_url(source.url, limit, transport)
        name = source.filename or urlsplit(source.url).path.rsplit("/", 1)[-1] or None
        return data, name
    raise AttachmentError("VALIDATION_FAILED", "附件要给 url、data_base64、text_content、ref 之一")


async def prepare(
    db: AsyncSession,
    settings: Settings,
    redis: Redis | None,
    client: OpenIMClient,
    instance: AgentInstance,
    sources: Sequence[tuple[AttachmentSource, AttachmentSource | None]],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[Prepared]:
    """取附件、检查、上传；返回要放进发件箱的元素。失败时整批都不发。"""
    if not instance.im_media_enabled:
        raise AttachmentError(
            "ATTACHMENT_FORBIDDEN", "管理员还没给你打开发图片、文件、视频的权限；可以只发文字"
        )
    if not settings.openim_object_url:
        raise AttachmentError("EXTERNAL_UNAVAILABLE", "IM 的对象存储没有配置，暂时不能发附件")
    base = settings.im_object_base_url
    prepared: list[Prepared] = []
    total = 0
    for source, cover_source in sources:
        data, name = await _bytes_of(db, client, instance, source, transport)
        detected = detect(data, name, source.kind)
        limit = LIMITS[detected.kind]
        if len(data) > limit:
            raise _too_large(limit)
        if not data:
            raise AttachmentError("VALIDATION_FAILED", "附件是空的")
        check_secrets(data, _ext(name) or detected.ext)
        total += len(data)
        display = safe_file_name(name, f"附件.{detected.ext}")
        cover: ImMedia | None = None
        if detected.kind == "video":
            cover_data = DEFAULT_COVER
            if cover_source is not None:
                cover_data, _ = await _bytes_of(db, client, instance, cover_source, transport)
            cover_info = detect(cover_data, None, "image")
            if len(cover_data) > LIMITS["image"]:
                raise _too_large(LIMITS["image"])
            cover = await store_media(
                db,
                client,
                workspace_id=instance.workspace_id,
                data=cover_data,
                detected=cover_info,
                file_name=None,
                instance_id=instance.id,
            )
        media = await store_media(
            db,
            client,
            workspace_id=instance.workspace_id,
            data=data,
            detected=detected,
            file_name=display if detected.kind != "image" else None,
            instance_id=instance.id,
        )
        content_type, content = element_for(media, base, cover)
        label = {"image": "[图片]", "file": f"[文件] {display}", "video": f"[视频] {display}"}
        prepared.append(Prepared(media, content_type, content, label[detected.kind], cover))
    if redis is not None:
        await _quota(redis, instance.id, len(prepared), total)
    return prepared


async def _quota(redis: Redis, instance_id: uuid.UUID, count: int, size: int) -> None:
    hour = HOUR_KEY.format(instance_id=instance_id)
    day = DAY_KEY.format(instance_id=instance_id)
    used = int(await redis.get(hour) or 0)
    used_bytes = int(await redis.get(day) or 0)
    if used + count > ATTACHMENTS_PER_HOUR:
        raise AttachmentError("RATE_LIMITED", f"每小时最多发 {ATTACHMENTS_PER_HOUR} 个附件")
    if used_bytes + size > BYTES_PER_DAY:
        raise AttachmentError("RATE_LIMITED", f"每天附件合计最多 {BYTES_PER_DAY // MB} MB")
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incrby(hour, count)
        pipe.expire(hour, 3600, nx=True)
        pipe.incrby(day, size)
        pipe.expire(day, 86400, nx=True)
        await pipe.execute()


async def quota_left(redis: Redis | None, instance_id: uuid.UUID) -> tuple[int, int]:
    if redis is None:
        return ATTACHMENTS_PER_HOUR, BYTES_PER_DAY
    used = int(await redis.get(HOUR_KEY.format(instance_id=instance_id)) or 0)
    used_bytes = int(await redis.get(DAY_KEY.format(instance_id=instance_id)) or 0)
    return max(0, ATTACHMENTS_PER_HOUR - used), max(0, BYTES_PER_DAY - used_bytes)
