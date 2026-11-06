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

FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
# 基础镜像（锁定 digest）还没换上修复版本的系统包：构建时只升级 trivy 报出已有修复的那几个
# （2026-11-06：apt / libapt-pkg7.0 3.0.3 → 3.0.4，CVE-2026-109518）。上游出新构建后改 digest、删掉这一步。
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade apt libapt-pkg7.0 \
    && rm -rf /var/lib/apt/lists/*
# 知识库图片的本地存储（T-0103）：Compose 把卷 kb-files 挂到 /data/kb-files；新卷继承这个目录的属主
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /data/kb-files && chown app:app /data/kb-files && chmod 700 /data/kb-files
COPY --from=build /opt/venv /opt/venv
COPY --from=build --chown=root:root /app /app
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
USER app
EXPOSE 8000
# 访问日志由应用的 request_context 中间件以 JSON 输出，关闭 uvicorn 自带的访问日志
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
