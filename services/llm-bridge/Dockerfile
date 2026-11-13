# 大模型会话桥（docs/v2/LLM_BRIDGE_PLAN.md）：Python 服务 + 三家官方命令行（需要 Node）。
# 版本锁定与核验见 docs/decisions/2026-11-13-llm-bridge-verification.md、docs/versions.md。

FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS build
RUN pip install --no-cache-dir uv==0.12.24
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY bridge ./bridge

# 官方命令行：版本写死，装进 /opt/cli
FROM node:22.23.4-trixie-slim@sha256:be1e2a3dc2c4fd68cdfadad2a696a280e0d2749098bee509230174d72fb768c6 AS cli
WORKDIR /opt/cli
RUN npm install --omit=dev --no-audit --no-fund \
      @openai/codex@0.169.0 \
      @anthropic-ai/claude-code@2.1.323 \
      @tencent-ai/codebuddy-code@2.184.0 \
    && npm cache clean --force

FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
# git：开发环境克隆仓库；bubblewrap：Codex 的沙箱；ca-certificates：访问各平台
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade apt libapt-pkg7.0 zlib1g \
    && apt-get install -y --no-install-recommends git bubblewrap ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 bridge \
    && useradd --system --uid 10001 --gid bridge --home-dir /data/home --shell /usr/sbin/nologin bridge \
    && mkdir -p /data && chown bridge:bridge /data && chmod 700 /data
COPY --from=cli /usr/local/bin/node /usr/local/bin/node
COPY --from=cli --chown=root:root /opt/cli /opt/cli
COPY --from=build /opt/venv /opt/venv
COPY --from=build --chown=root:root /app /app
ENV PATH=/opt/venv/bin:/opt/cli/node_modules/.bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BRIDGE_DATA_DIR=/data \
    # 官方命令行不要自动更新、不要上报遥测：版本由镜像锁定
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
WORKDIR /app
USER bridge
VOLUME /data
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=3).status == 200 else 1)"
CMD ["uvicorn", "bridge.main:app", "--host", "0.0.0.0", "--port", "8787", "--no-access-log"]
