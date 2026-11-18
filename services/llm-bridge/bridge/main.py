"""`uvicorn bridge.main:app`。"""

from bridge.app import create_app
from bridge.pid1 import harden

# 管理器的环境变量里有管理令牌；后台作业、官方程序和它是同一个用户，
# 设成不可转储后它们读不了 /proc/<管理器>/environ（T-0292 审查，见 bridge/pid1.py）
harden()

app = create_app()
