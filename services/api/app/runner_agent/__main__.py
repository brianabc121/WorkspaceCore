"""入口：`python -m runner_agent`（接入包的 compose.yaml 里这样启动）。"""

from __future__ import annotations

import logging
import os
import signal
import sys

from .agent import Agent, Config
from .client import Workspace
from .docker import Docker
from .proxy import EgressProxy, parse_blocked


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = Config.from_env(dict(os.environ))
    agent = Agent(
        config=config,
        docker=Docker(config.socket),
        workspace=Workspace(
            config.url,
            config.token,
            ca_file=config.ca_file,
            cert_file=config.cert_file,
            key_file=config.key_file,
        ),
        proxy=EgressProxy(parse_blocked(config.block)),
    )
    agent.start()

    def stop(_signum: int, _frame: object) -> None:
        agent.stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logging.getLogger("runner_agent").info("runner-agent 已启动，连接 %s", config.url)
    agent.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
