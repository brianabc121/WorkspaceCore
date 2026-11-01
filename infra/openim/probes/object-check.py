"""OpenIM 对象存储检查（V2 OC-3）：上传、下载、图片缩略图都要能用。

ADR：docs/decisions/2026-10-31-v2-oc3-openim-patched-image.md 第 8 节。
用管理员令牌走 OpenIM 的上传接口：initiate_multipart_upload → 按预签名地址 PUT → complete_multipart_upload
→ access_url → 下载比对。三项检查：
  1. 小文件（走“预签名单次上传”分支）；
  2. 12 MB 文件（走分片上传分支，3 片）；
  3. PNG 图片取 64×64 缩略图（OpenIM 读原图、缩放、写回缓存）。
对象存储必须满足：单次 PUT 的 ETag 是内容的 MD5；支持 CopyObject 且 ETag 不变（openimsdk/tools s3/cont）。

  python3 -I object-check.py --api http://openim-server:10002 --secret "$OPENIM_SECRET"
预签名地址（IMENV_MINIO_EXTERNALADDRESS）必须能从运行脚本的地方访问，CI 里在 openim 网络的容器中运行。
退出码：0 全部通过；1 有失败。只用标准库。
"""

import argparse
import hashlib
import json
import os
import struct
import sys
import urllib.request
import uuid
import zlib
from urllib.parse import urlencode

PART_SEPARATOR = ","  # openimsdk/tools s3/cont/consts.go


def post(api: str, path: str, body: dict, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json", "operationID": uuid.uuid4().hex}
    if token:
        headers["token"] = token
    req = urllib.request.Request(api + path, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if data.get("errCode") != 0:
        raise RuntimeError(f"{path}: errCode={data.get('errCode')} {data.get('errMsg')}")
    return data.get("data") or {}


def with_query(url: str, query: list[dict] | None) -> str:
    pairs = [(kv["key"], v) for kv in query or [] for v in kv.get("values", [])]
    return url if not pairs else url + ("&" if "?" in url else "?") + urlencode(pairs)


def headers_of(items: list[dict] | None) -> dict:
    return {kv["key"]: kv["values"][0] for kv in items or [] if kv.get("values")}


def get(url: str) -> tuple[bytes, str]:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read(), resp.headers.get("Content-Type") or ""


def upload(api: str, tok: str, data: bytes, name: str, content_type: str) -> None:
    part_size = post(api, "/object/part_size", {"size": len(data)}, tok)["size"]
    parts = [data[i : i + part_size] for i in range(0, len(data), part_size)]
    part_hashes = [hashlib.md5(p).hexdigest() for p in parts]
    file_hash = hashlib.md5(PART_SEPARATOR.join(part_hashes).encode()).hexdigest()
    init = post(api, "/object/initiate_multipart_upload", {
        "hash": file_hash, "size": len(data), "partSize": part_size, "maxParts": -1,
        "cause": "object-check", "name": name, "contentType": content_type,
    }, tok)
    up = init.get("upload")
    if not up:
        # 同样内容（同一个哈希）已经存在：OpenIM 直接返回现成的地址，不再上传
        return
    sign = up["sign"]
    for i, part in enumerate(parts, start=1):
        sp = next(p for p in sign["parts"] if p["partNumber"] == i)
        url = with_query(sp.get("url") or sign.get("url"), (sign.get("query") or []) + (sp.get("query") or []))
        headers = {**headers_of(sign.get("header")), **headers_of(sp.get("header"))}
        req = urllib.request.Request(url, data=part, headers=headers, method="PUT")
        with urllib.request.urlopen(req, timeout=60) as resp:
            etag = (resp.headers.get("ETag") or "").strip('"')
        if etag != part_hashes[i - 1]:
            raise RuntimeError(f"第 {i} 片的 ETag 不是内容的 MD5：{etag}")
    post(api, "/object/complete_multipart_upload", {
        "uploadID": up["uploadID"], "parts": part_hashes, "name": name,
        "contentType": content_type, "cause": "object-check",
    }, tok)


def png(width: int, height: int) -> bytes:
    rows = b"".join(
        b"\x00" + b"".join(bytes([(x * 7) % 256, (y * 5) % 256, 128]) for x in range(width)) for y in range(height)
    )

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    # 随机文本块让每次的内容（哈希）不同，确保真的走一遍上传，而不是命中去重
    text = chunk(b"tEXt", b"Comment\x00" + uuid.uuid4().hex.encode())
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + text + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def check_roundtrip(api: str, tok: str, size: int) -> str:
    data = os.urandom(size)
    name = f"object-check/{uuid.uuid4().hex}.bin"
    upload(api, tok, data, name, "application/octet-stream")
    got, _ = get(post(api, "/object/access_url", {"name": name}, tok)["url"])
    if got != data:
        raise RuntimeError(f"下载内容不一致（{len(got)} 字节）")
    return f"{size} 字节，下载比对一致"


def check_thumbnail(api: str, tok: str) -> str:
    data = png(400, 300)
    name = f"object-check/{uuid.uuid4().hex}.png"
    upload(api, tok, data, name, "image/png")
    query = {"type": "image", "width": "64", "height": "64"}
    body, ctype = get(post(api, "/object/access_url", {"name": name, "query": query}, tok)["url"])
    if body[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError(f"缩略图不是 PNG（{ctype}，{len(body)} 字节）")
    width, height = struct.unpack(">II", body[16:24])
    if max(width, height) > 64:
        raise RuntimeError(f"缩略图尺寸 {width}×{height} 超过 64")
    return f"400×300 → {width}×{height}，{len(body)} 字节"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--secret", required=True)
    args = ap.parse_args()
    api = args.api.rstrip("/")
    tok = post(api, "/auth/get_admin_token", {"secret": args.secret, "userID": "imAdmin"})["token"]
    checks = [
        ("小文件（预签名单次上传）", lambda: check_roundtrip(api, tok, 200_000)),
        ("12 MB 文件（分片上传）", lambda: check_roundtrip(api, tok, 12_000_000)),
        ("图片缩略图", lambda: check_thumbnail(api, tok)),
    ]
    failed = 0
    for name, fn in checks:
        try:
            print(f"✅ {name}：{fn()}")
        except Exception as exc:  # 每项独立判断，最后汇总
            failed += 1
            print(f"❌ {name}：{exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
