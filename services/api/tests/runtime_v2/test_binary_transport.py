"""Exercise actual pipe pressure and subprocesses, including ambiguous termination."""

import io
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from app.runtime_v2 import binary_transport


def run(code: str, **kwargs: Any) -> binary_transport.BinaryResult:
    return binary_transport.BinaryTransport().run([sys.executable, "-c", code], **kwargs)


def test_binary_duplex_avoids_pipe_deadlock() -> None:
    payload = bytes(range(256)) * 8192
    result = binary_transport.BinaryTransport().run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'x'*200000); "
            "sys.stdout.buffer.flush(); data=sys.stdin.buffer.read(); "
            "sys.stdout.buffer.write(data); sys.stderr.buffer.write(b'err')",
        ],
        stdin=payload,
        max_input_bytes=len(payload),
        max_stdout_bytes=len(payload) + 200000,
        timeout=5,
    )
    assert result.stdout == b"x" * 200000 + payload
    assert result.stderr == b"err"
    assert result.returncode == 0


@pytest.mark.parametrize("channel", ["stdout", "stderr"])
def test_output_bomb_is_bounded_unknown(channel: str) -> None:
    with pytest.raises(binary_transport.BinaryTransportUnknown, match="output limit") as error:
        run(
            f"import sys; sys.{channel}.buffer.write(b'synthetic-secret'*1000000)",
            max_stdout_bytes=1000,
            max_stderr_bytes=1000,
            timeout=3,
        )
    assert "synthetic-secret" not in str(error.value)


def test_streamed_input_limit_is_unknown_and_not_success() -> None:
    with pytest.raises(binary_transport.BinaryTransportUnknown, match="input limit"):
        run(
            "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
            stdin=io.BytesIO(b"x" * 1001),
            max_input_bytes=1000,
            timeout=3,
        )


def test_known_oversize_input_rejected_before_start(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    with pytest.raises(ValueError, match="input"):
        run(
            f"from pathlib import Path; Path({str(marker)!r}).touch()",
            stdin=b"too long",
            max_input_bytes=1,
        )
    assert not marker.exists()


def test_deadline_kills_local_process_and_does_not_claim_remote_cancel(tmp_path: Path) -> None:
    pid = tmp_path / "pid"
    started = time.monotonic()
    with pytest.raises(binary_transport.BinaryTransportUnknown, match="deadline"):
        run(
            f"import os,time; open({str(pid)!r},'w').write(str(os.getpid())); time.sleep(10)",
            timeout=0.3,
        )
    assert time.monotonic() - started < 2
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def test_streaming_stdout_retains_no_capture_and_counts_all_bytes() -> None:
    chunks: list[int] = []
    result = binary_transport.BinaryTransport().run(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x'*2000000)"],
        stdout_consumer=lambda chunk: chunks.append(len(chunk)),
        max_stdout_bytes=2000000,
        timeout=5,
    )
    assert result.stdout == b""
    assert sum(chunks) == 2000000
    assert max(chunks) <= 65536
    with pytest.raises(binary_transport.BinaryTransportUnknown, match="output limit"):
        run(
            "import sys; sys.stdout.buffer.write(b'x'*1001)",
            stdout_consumer=lambda chunk: None,
            max_stdout_bytes=1000,
        )


def test_partial_stream_reads_and_nonzero_exit_are_preserved() -> None:
    class Partial(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            return super().read(min(size if size is not None and size >= 0 else 7, 7))

    result = binary_transport.BinaryTransport().run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.exit(7)",
        ],
        stdin=Partial(b"a\x00b" * 3000),
        max_input_bytes=9000,
        max_stdout_bytes=9000,
    )
    assert result.stdout == b"a\x00b" * 3000
    assert result.returncode == 7


def test_error_is_generic_when_stream_or_consumer_raises() -> None:
    def consumer(chunk: bytes) -> None:
        raise ValueError("synthetic-secret")

    with pytest.raises(binary_transport.BinaryTransportUnknown) as error:
        run("print('hello')", stdout_consumer=consumer)
    assert "synthetic-secret" not in str(error.value)


def test_open_inherited_pipe_cannot_escape_deadline() -> None:
    with pytest.raises(binary_transport.BinaryTransportUnknown, match="deadline"):
        run(
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'])",
            timeout=0.3,
        )


def test_blocking_input_cannot_escape_deadline() -> None:
    class Slow(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            time.sleep(1)
            return b""

    started = time.monotonic()
    with pytest.raises(binary_transport.BinaryTransportUnknown, match="deadline"):
        run("import sys; sys.stdin.buffer.read()", stdin=Slow(), timeout=0.2)
    assert time.monotonic() - started < 0.8


def test_blocking_consumer_cannot_escape_deadline() -> None:
    started = time.monotonic()
    with pytest.raises(binary_transport.BinaryTransportUnknown, match="deadline"):
        run("print('hello')", stdout_consumer=lambda chunk: time.sleep(1), timeout=0.2)
    assert time.monotonic() - started < 0.8
