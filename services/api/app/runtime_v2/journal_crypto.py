"""Authenticated operation receipts with a private, non-replaceable companion key."""

from __future__ import annotations

import base64
import json
import os
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_ERROR = "operation receipt authentication unavailable"
_MARKER = "sealed_operation_receipt"


class ReceiptCipher:
    def __init__(self, journal_path: str) -> None:
        self.path = journal_path + ".key"

    @staticmethod
    def sealed(value: str) -> bool:
        parsed = json.loads(value)
        return type(parsed) is dict and _MARKER in parsed

    def _key(self, *, create: bool = False) -> bytes:
        try:
            try:
                fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    fd = os.open(
                        self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                    )
                except FileExistsError:
                    return self._key()
                try:
                    key = os.urandom(32)
                    if os.write(fd, key) != 32:
                        raise ValueError(_ERROR)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                directory = os.open(str(Path(self.path).parent), os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                return self._key()
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_size != 32
                ):
                    raise ValueError(_ERROR)
                key = os.read(fd, 33)
                if len(key) != 32:
                    raise ValueError(_ERROR)
                return key
            finally:
                os.close(fd)
        except (OSError, ValueError):
            raise ValueError(_ERROR) from None

    @staticmethod
    def _aad(command_id: str) -> bytes:
        try:
            command = str(UUID(command_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError(_ERROR) from None
        return ("operation-receipt:v1:" + command).encode("ascii")

    def encrypt(
        self,
        command_id: str,
        result: dict[str, Any],
        *,
        history: Iterable[tuple[str, str]],
    ) -> str:
        aad = self._aad(command_id)
        receipts = iter(history)
        first = next(receipts, None)
        key = self._key(create=first is None)
        # Authenticate every prior encrypted receipt using the SAME key bytes
        # used for the new ciphertext. The journal holds its write transaction
        # while this iterator is consumed; key loss/tamper cannot create mixed
        # key history or turn an unknown operation into a completed one.
        if first is not None:
            self._decrypt(first[0], first[1], key)
        for previous_id, previous_value in receipts:
            self._decrypt(previous_id, previous_value, key)
        nonce = os.urandom(12)
        raw = json.dumps(result, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ciphertext = AESGCM(key).encrypt(nonce, raw, aad)
        return json.dumps(
            {_MARKER: 1, "data": base64.b64encode(nonce + ciphertext).decode("ascii")},
            separators=(",", ":"),
        )

    def decrypt(self, command_id: str, value: str) -> dict[str, Any]:
        return self._decrypt(command_id, value, self._key())

    def _decrypt(self, command_id: str, value: str, key: bytes) -> dict[str, Any]:
        try:
            envelope = json.loads(value)
            if (
                set(envelope) != {_MARKER, "data"}
                or type(envelope[_MARKER]) is not int
                or envelope[_MARKER] != 1
            ):
                raise ValueError(_ERROR)
            raw = base64.b64decode(envelope["data"], validate=True)
            result: dict[str, Any] = json.loads(
                AESGCM(key).decrypt(raw[:12], raw[12:], self._aad(command_id))
            )
            if type(result) is not dict:
                raise ValueError(_ERROR)
            return result
        except (InvalidTag, ValueError, TypeError, KeyError):
            raise ValueError(_ERROR) from None
