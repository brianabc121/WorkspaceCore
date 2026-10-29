"""导出 OpenAPI 到 services/api/openapi.json（前端据此生成 TypeScript 类型）。

用法（在 services/api 下）：
    uv run python -m scripts.export_openapi          # 重新生成
    uv run python -m scripts.export_openapi --check  # 只检查是否最新，CI 使用
"""

import argparse
import json
import sys
from pathlib import Path

from app.core.config import Settings
from app.main import create_app

OUTPUT = Path(__file__).resolve().parent.parent / "openapi.json"


def render() -> str:
    # 生产环境会关闭 /api/v1/openapi.json，但路由本身与环境无关，这里固定用开发配置导出
    app = create_app(Settings(app_env="development", log_level="WARNING"))
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查 openapi.json 是否最新")
    args = parser.parse_args()
    content = render()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != content:
            print("openapi.json 已过期：请运行 make openapi 并提交结果", file=sys.stderr)
            return 1
        print("openapi.json 是最新的")
        return 0
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"已写入 {OUTPUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
