"""Run with python -m app.runtime_v2; disabled until operator validation."""

import fcntl
import logging
import os
import signal
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType
from typing import Any

from app.runner_agent.client import Workspace, WorkspaceError
from app.runtime_v2.config import ControllerConfig
from app.runtime_v2.controller import CommandJournal, Controller
from app.runtime_v2.network import PrivateNetworkReconciler
from app.runtime_v2.runner import Kubectl, KubernetesCommandError, KubernetesRunner, OutcomeUnknown
from app.runtime_v2.telemetry import NodeTelemetry, TelemetryUnavailable

logger = logging.getLogger(__name__)


@contextmanager
def controller_lock(journal: str) -> Iterator[None]:
    fd = os.open(journal + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("controller already running for this journal") from None
        yield
    finally:
        os.close(fd)


def install_signal_handlers(stopping: threading.Event) -> None:
    def stop(signum: int, frame: FrameType | None) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)


def run(controller: Controller, telemetry: Any, stopping: Any) -> int:
    delay = 1
    while not stopping.is_set():
        try:
            if controller.network is not None:
                controller.network.reconcile()
            controller.recover_pending()
            controller.poll_once(telemetry.capacity())
            try:
                telemetry.report(controller.workspace)
            except TelemetryUnavailable:
                logger.warning("Kubernetes capacity report unavailable; eligibility remains closed")
            delay = 1
        except WorkspaceError as error:
            if error.status in (401, 403) or not error.retryable:
                logger.error("workspace authentication or request rejected; controller stopping")
                return 3
            logger.warning("workspace unavailable; bounded retry scheduled")
            stopping.wait(delay)
            delay = min(delay * 2, 60)
        except (
            KubernetesCommandError,
            OutcomeUnknown,
            TelemetryUnavailable,
            ValueError,
            OSError,
            sqlite3.Error,
        ):
            logger.warning("controller dependency unavailable; bounded retry scheduled")
            stopping.wait(delay)
            delay = min(delay * 2, 60)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if os.environ.get("RUNTIME_V2_ENABLED") != "true":
        logger.error("runtime-v2 controller disabled; operator cluster validation is required")
        return 2
    try:
        config = ControllerConfig.from_environ(os.environ)
        api = Kubectl(config.kubeconfig)
        api.validate_connection()
        workspace = Workspace(
            config.url,
            config.token,
            ca_file=config.ca_file,
            cert_file=config.cert_file,
            key_file=config.key_file,
        )
        stopping = threading.Event()
        install_signal_handlers(stopping)
        with controller_lock(config.journal):
            runner = KubernetesRunner(api)
            journal = CommandJournal(config.journal)
            controller = Controller(
                runner,
                workspace,
                journal,
                workspace_id=config.workspace_id,
                host_id=config.host_id,
                node_name=config.node_name,
                stopping=stopping,
                network=PrivateNetworkReconciler(
                    runner,
                    workspace,
                    workspace_id=config.workspace_id,
                    host_id=config.host_id,
                    node_name=config.node_name,
                    journal_path=config.journal,
                ),
            )
            return run(controller, NodeTelemetry(api, config.node_name), stopping)
    except (
        ValueError,
        OSError,
        sqlite3.Error,
        KubernetesCommandError,
        OutcomeUnknown,
        RuntimeError,
    ):
        logger.error("controller configuration or Kubernetes TLS context invalid")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
