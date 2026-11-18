"""Bounded binary subprocess I/O; loss never establishes remote cancellation.

argv is trusted caller-built control data. Commands, credentials and material belong
on stdin. Stream readers/consumers run in daemon threads with bounded queues so a
blocked caller stream cannot defeat the local process deadline. Such callbacks
must eventually return: Python cannot forcibly cancel a blocked caller callback.
"""

from __future__ import annotations

import io
import math
import os
import queue
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import BinaryIO

_CHUNK = 65536
_MAX_BYTES = 5 * 1024**3


class BinaryTransportUnknown(RuntimeError):
    """The local transport failed; remote completion/cancellation is unknown."""


@dataclass(frozen=True)
class BinaryResult:
    stdout: bytes
    stderr: bytes
    returncode: int


class BinaryTransport:
    def run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes | BinaryIO = b"",
        max_input_bytes: int = 20 * 1024**2,
        max_stdout_bytes: int = 20 * 1024**2,
        max_stderr_bytes: int = 65536,
        timeout: float = 30,
        stdout_consumer: Callable[[bytes], object] | None = None,
    ) -> BinaryResult:
        """Run shell-free binary pipes; byte overflow/deadline raises typed unknown.

        Consumer stdout is counted in aggregate and never retained in the result.
        Nonzero local exit codes are returned to the protocol parser, not promoted
        to successful remote completion. Exception messages never include stderr.
        """
        if (
            not isinstance(argv, (list, tuple))
            or not 1 <= len(argv) <= 1000
            or any(type(arg) is not str or "\x00" in arg for arg in argv)
            or sum(len(arg) for arg in argv) > 100000
            or not argv[0]
        ):
            raise ValueError("invalid binary transport argv")
        if any(
            type(limit) is not int or not 0 <= limit <= _MAX_BYTES
            for limit in (max_input_bytes, max_stdout_bytes, max_stderr_bytes)
        ):
            raise ValueError("invalid binary transport byte bounds")
        if (
            type(timeout) not in {int, float}
            or not math.isfinite(timeout)
            or not 0 < timeout <= 86400
        ):
            raise ValueError("invalid binary transport deadline")
        if not isinstance(stdin, bytes) and not callable(getattr(stdin, "read", None)):
            raise ValueError("invalid binary transport input")
        if isinstance(stdin, bytes) and len(stdin) > max_input_bytes:
            raise ValueError("binary transport input limit exceeded")
        if stdout_consumer is not None and not callable(stdout_consumer):
            raise ValueError("invalid binary transport consumer")

        deadline = time.monotonic() + timeout
        source = io.BytesIO(stdin) if isinstance(stdin, bytes) else stdin
        incoming: queue.Queue[bytes | None] = queue.Queue(maxsize=2)
        outgoing: queue.Queue[bytes | None] = queue.Queue(maxsize=2)
        stopped, source_done, consumer_done, failed = (threading.Event() for _ in range(4))
        failure: list[str] = []

        def fail(reason: str) -> None:
            failure.append(reason)
            failed.set()

        def enqueue(target: queue.Queue[bytes | None], chunk: bytes | None) -> bool:
            while not stopped.is_set():
                try:
                    target.put(chunk, timeout=0.02)
                    return True
                except queue.Full:
                    continue
            return False

        def read_source() -> None:
            total = 0
            try:
                while not stopped.is_set():
                    amount = min(_CHUNK, max_input_bytes - total + 1)
                    chunk = source.read(amount)
                    if type(chunk) is not bytes or len(chunk) > amount:
                        fail("binary transport input stream failed")
                        return
                    total += len(chunk)
                    if total > max_input_bytes:
                        fail("binary transport input limit exceeded")
                        return
                    if not enqueue(incoming, chunk or None) or not chunk:
                        return
            except Exception:
                fail("binary transport input stream failed")
            finally:
                source_done.set()

        def consume() -> None:
            try:
                while not stopped.is_set():
                    try:
                        chunk = outgoing.get(timeout=0.02)
                    except queue.Empty:
                        continue
                    if chunk is None:
                        return
                    if stdout_consumer is not None:
                        stdout_consumer(chunk)
            except Exception:
                fail("binary transport stdout consumer failed")
            finally:
                consumer_done.set()

        try:
            process = subprocess.Popen(  # noqa: S603 - argv is a trusted structured caller boundary
                list(argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                start_new_session=True,
            )
        except (OSError, ValueError):
            raise BinaryTransportUnknown("binary transport could not start") from None
        selector = selectors.DefaultSelector()
        succeeded = False
        stdout, stderr = bytearray(), bytearray()
        counts = {"stdout": 0, "stderr": 0}
        pending_input = memoryview(b"")
        pending_output: bytes | None = None
        input_eof, output_eof, sent_consumer_eof = False, False, False
        try:
            if process.stdin is None or process.stdout is None or process.stderr is None:
                raise BinaryTransportUnknown("binary transport pipes unavailable")
            for stream, kind in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, kind)
            os.set_blocking(process.stdin.fileno(), False)
            threading.Thread(target=read_source, daemon=True).start()
            if stdout_consumer is not None:
                threading.Thread(target=consume, daemon=True).start()
            else:
                consumer_done.set()

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BinaryTransportUnknown("binary transport deadline exceeded")
                if failed.is_set():
                    raise BinaryTransportUnknown(failure[0])
                if pending_output is not None:
                    try:
                        outgoing.put_nowait(pending_output)
                    except queue.Full:
                        pass
                    else:
                        pending_output = None
                        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                if output_eof and stdout_consumer is not None and not sent_consumer_eof:
                    try:
                        outgoing.put_nowait(None)
                    except queue.Full:
                        pass
                    else:
                        sent_consumer_eof = True
                if not pending_input and not input_eof:
                    try:
                        chunk = incoming.get_nowait()
                    except queue.Empty:
                        pass
                    else:
                        if chunk is None:
                            input_eof = True
                            process.stdin.close()
                        else:
                            pending_input = memoryview(chunk)
                            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
                if (
                    not selector.get_map()
                    and pending_output is None
                    and process.poll() is not None
                    and source_done.is_set()
                    and input_eof
                    and consumer_done.is_set()
                ):
                    if failed.is_set():
                        raise BinaryTransportUnknown(failure[0])
                    succeeded = True
                    return BinaryResult(bytes(stdout), bytes(stderr), process.returncode)
                for key, _ in selector.select(min(0.02, remaining)):
                    if key.data == "stdin":
                        try:
                            written = os.write(key.fd, pending_input)
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            raise BinaryTransportUnknown(
                                "binary transport input pipe closed"
                            ) from None
                        pending_input = pending_input[written:]
                        if not pending_input:
                            selector.unregister(process.stdin)
                        continue
                    try:
                        data = os.read(key.fd, _CHUNK)
                    except BlockingIOError:
                        continue
                    if not data:
                        selector.unregister(key.fileobj)
                        if key.data == "stdout":
                            process.stdout.close()
                        else:
                            process.stderr.close()
                        if key.data == "stdout":
                            output_eof = True
                        continue
                    kind = key.data
                    counts[kind] += len(data)
                    limit = max_stdout_bytes if kind == "stdout" else max_stderr_bytes
                    if counts[kind] > limit:
                        raise BinaryTransportUnknown("binary transport output limit exceeded")
                    if kind == "stderr":
                        stderr.extend(data)
                    elif stdout_consumer is None:
                        stdout.extend(data)
                    else:
                        try:
                            outgoing.put_nowait(data)
                        except queue.Full:
                            pending_output = data
                            selector.unregister(process.stdout)
        except BinaryTransportUnknown:
            raise
        except Exception:
            raise BinaryTransportUnknown("binary transport I/O failed") from None
        finally:
            stopped.set()
            selector.close()
            if not succeeded:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=1)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
