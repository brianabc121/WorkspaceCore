"""按文件内容识别图片类型与尺寸（V2 OC-3，契约第 5.4 节：不只看 Content-Type）。

只读文件头，不解码像素：工作台只转存图片，不做处理，也就不引入图片解码库的攻击面。
支持 PNG、JPEG、GIF、WebP；其他内容一律不认。尺寸读不出来或为 0 的也不认
（OpenIM 的图片消息要求宽高，`pkg/apistruct/msg.go` 的 PictureBaseInfo）。
"""

import struct
from dataclasses import dataclass

MAX_DIMENSION = 20_000


@dataclass(frozen=True)
class ImageInfo:
    mime: str
    ext: str
    width: int
    height: int


def _png(data: bytes) -> tuple[int, int] | None:
    # 签名后第一个块必须是 IHDR：长度 13，宽、高各 4 字节大端
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _gif(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10:
        return None
    width, height = struct.unpack("<HH", data[6:10])
    return width, height


# JPEG 里带尺寸的帧头（SOF0–SOF15，去掉 DHT=C4、JPG=C8、DAC=CC）
_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def _jpeg(data: bytes) -> tuple[int, int] | None:
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # 填充字节
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # 没有长度的标记
            i += 2
            continue
        (length,) = struct.unpack(">H", data[i + 2 : i + 4])
        if length < 2:
            return None
        if marker in _SOF:
            if i + 9 > len(data):
                return None
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        if marker == 0xDA:  # 图像数据开始了还没见到帧头
            return None
        i += 2 + length
    return None


def _webp(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30:
        return None
    chunk = data[12:16]
    if chunk == b"VP8 ":
        # 关键帧：3 字节帧标记 + 起始码 9D 01 2A，之后 14 位宽、14 位高
        if data[23:26] != b"\x9d\x01\x2a":
            return None
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L":
        if data[20] != 0x2F:
            return None
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X":
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    return None


def sniff(data: bytes) -> ImageInfo | None:
    """识别图片；不是支持的类型、文件头损坏或尺寸不合理时返回 None。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime, ext, size = "image/png", "png", _png(data)
    elif data.startswith(b"\xff\xd8\xff"):
        mime, ext, size = "image/jpeg", "jpg", _jpeg(data)
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        mime, ext, size = "image/gif", "gif", _gif(data)
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime, ext, size = "image/webp", "webp", _webp(data)
    else:
        return None
    if size is None:
        return None
    width, height = size
    if not (0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION):
        return None
    return ImageInfo(mime=mime, ext=ext, width=width, height=height)
