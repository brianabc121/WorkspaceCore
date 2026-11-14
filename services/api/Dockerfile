# API 与 Worker 共用的镜像：
#   api:    uvicorn app.main:app（默认 CMD）
#   worker: arq app.jobs.worker.WorkerSettings --custom-log-dict app.jobs.worker.LOG_CONFIG
#   迁移:   alembic upgrade head
# 版本说明见 docs/decisions/2026-10-29-backend-stack.md 与 2026-10-30-sprint-0-integration.md。

FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS build
RUN pip install --no-cache-dir uv==0.12.24
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# 只装生产依赖，且必须与 uv.lock 完全一致
RUN uv sync --locked --no-dev --no-install-project
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

# 大模型客户端的一键部署（T-0234）：把 MCP 管理器的构建文件带进镜像（/opt/myworkspace/llm-bridge），
# 部署时由工作台上传到服务器上现场构建。compose.yaml 用 additional_contexts 把 services/llm-bridge
# 作为名叫 llm-bridge-src 的构建上下文传进来，顶替下面这个空阶段。
# 直接 `docker build services/api`（没有这个上下文）时用的就是空阶段：镜像照样能构建，只是不能一键部署。
FROM scratch AS llm-bridge-src

FROM build AS llm-bridge-files
COPY --from=llm-bridge-src / /src/
# 只留构建要用的：Dockerfile、依赖锁文件、源码；测试、虚拟环境、缓存不带
RUN mkdir -p /out && cd /src \
    && for f in Dockerfile pyproject.toml uv.lock .dockerignore; do \
         if [ -f "$f" ]; then cp "$f" /out/; fi; \
       done \
    && if [ -d bridge ]; then \
         cp -r bridge /out/bridge && find /out -name __pycache__ -prune -exec rm -rf {} +; \
       fi

FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
# 基础镜像（锁定 digest）还没换上修复版本的系统包：构建时只升级 trivy 报出已有修复的那几个
# （2026-11-06：apt / libapt-pkg7.0 3.0.3 → 3.0.4，CVE-2026-109518；
#   2026-11-07：zlib1g → 1:1.3.dfsg+really1.3.1-1+deb13u1，CVE-2026-76844 / CVE-2026-85091）。
# 上游出新构建后改 digest、删掉这一步。
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade apt libapt-pkg7.0 zlib1g \
    && rm -rf /var/lib/apt/lists/*
# 知识库图片的本地存储（T-0103）：Compose 把卷 kb-files 挂到 /data/kb-files；新卷继承这个目录的属主。
# 新闻早报（T-0152）：卷 trendradar-config 挂到 /var/lib/workspace/trendradar，TrendRadar 以 10001 组只读
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /data/kb-files && chown app:app /data/kb-files && chmod 700 /data/kb-files \
    && mkdir -p /var/lib/workspace/trendradar \
    && chown app:app /var/lib/workspace/trendradar && chmod 750 /var/lib/workspace/trendradar
COPY --from=build /opt/venv /opt/venv
COPY --from=build --chown=root:root /app /app
COPY --from=llm-bridge-files --chown=root:root /out /opt/myworkspace/llm-bridge
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
USER app
EXPOSE 8000
# 访问日志由应用的 request_context 中间件以 JSON 输出，关闭 uvicorn 自带的访问日志
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
