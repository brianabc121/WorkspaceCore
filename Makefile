# My Workspace 根目录命令（docs/DEVELOPMENT_SPEC.md 第 11.3 节）。
# 前端部分调用 apps/web 的 npm scripts；apps/web 尚不存在时自动跳过（见 docs/decisions/2026-10-29-sprint-0-contracts.md）。

SHELL := /bin/bash
.DEFAULT_GOAL := help

API_DIR := services/api
WEB_DIR := apps/web
UV ?= uv
NPM ?= npm
COMPOSE ?= docker compose
HAS_WEB := $(wildcard $(WEB_DIR)/package.json)
# 读取 .env 里的 HTTP_PORT（没有时用 8080），供 make smoke 使用
HTTP_PORT := $(or $(shell sed -n 's/^HTTP_PORT=//p' .env 2>/dev/null),8080)

define skip_web
	@echo "跳过前端：$(WEB_DIR)/package.json 尚不存在"
endef

.PHONY: help env setup dev dev-api dev-web up down ps logs migrate migrate-local smoke \
	lint lint-api lint-web format test test-api test-web build-web backup restore-verify

help: ## 列出可用命令
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-15s %s\n", $$1, $$2}'

env: ## 从 .env.example 生成 .env，并随机生成密码与密钥（已存在则不改）
	@python3 scripts/init_env.py

setup: env ## 准备本地开发环境：生成 .env 并安装前后端依赖
	cd $(API_DIR) && $(UV) sync
ifneq ($(HAS_WEB),)
	$(NPM) --prefix $(WEB_DIR) ci
else
	$(skip_web)
endif

dev: ## 本地开发：同时启动 API（热重载，:8000）和前端（:5173）
	@trap 'kill 0' INT TERM EXIT; \
	(cd $(API_DIR) && $(UV) run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000) & \
	if [ -n "$(HAS_WEB)" ]; then $(NPM) --prefix $(WEB_DIR) run dev; else echo "前端尚未创建，只启动 API"; wait; fi

dev-api: ## 只启动 API（热重载）
	cd $(API_DIR) && $(UV) run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

dev-web: ## 只启动前端开发服务器
	$(NPM) --prefix $(WEB_DIR) run dev

up: env ## 构建并启动 core 服务，等待全部健康检查通过
	$(COMPOSE) up -d --build --wait --wait-timeout 300
	@echo "已启动：http://127.0.0.1:$(HTTP_PORT)  首次启动后请运行 make migrate"

down: ## 停止服务；保留数据卷，不会删除数据
	$(COMPOSE) down

ps: ## 查看服务状态
	$(COMPOSE) ps

logs: ## 跟踪全部服务日志（Ctrl+C 退出）
	$(COMPOSE) logs -f --tail=100

migrate: ## 在运行中的 api 容器里把数据库迁移到最新版本（需先 make up）
	$(COMPOSE) exec -T api alembic upgrade head

migrate-local: ## 本机直接运行迁移（使用环境变量 DATABASE_URL）
	cd $(API_DIR) && $(UV) run alembic upgrade head

smoke: ## 经 Caddy 检查健康、就绪、前端页面与安全响应头
	scripts/smoke.sh http://127.0.0.1:$(HTTP_PORT)

lint: lint-api lint-web ## 代码质量检查（前后端）

lint-api:
	cd $(API_DIR) && $(UV) run ruff check . && $(UV) run ruff format --check . && $(UV) run mypy app migrations tests

lint-web:
ifneq ($(HAS_WEB),)
	$(NPM) --prefix $(WEB_DIR) run lint
	$(NPM) --prefix $(WEB_DIR) run typecheck
else
	$(skip_web)
endif

format: ## 自动格式化后端代码
	cd $(API_DIR) && $(UV) run ruff format . && $(UV) run ruff check --fix .

test: test-api test-web ## 运行测试（前后端）

test-api: ## 后端测试；设置 TEST_DATABASE_URL / TEST_REDIS_URL 时同时跑集成测试
	cd $(API_DIR) && $(UV) run pytest

test-web:
ifneq ($(HAS_WEB),)
	$(NPM) --prefix $(WEB_DIR) run test
else
	$(skip_web)
endif

build-web: ## 构建前端生产包
	$(NPM) --prefix $(WEB_DIR) run build

backup: ## 备份（尚未实现，计划在 S6-04）
	@echo "make backup 尚未实现：计划在 Sprint 6（S6-04）完成"; exit 1

restore-verify: ## 在隔离环境验证恢复（尚未实现，计划在 S6-04）
	@echo "make restore-verify 尚未实现：计划在 Sprint 6（S6-04）完成"; exit 1
