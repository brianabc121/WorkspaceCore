# 打过补丁的 OpenIM Server（V2 OC-3，ADR docs/decisions/2026-10-31-v2-oc0-openclaw-openim-verification.md 第 6 节）。
#
# 不要直接 docker build：用 infra/openim/server/build.sh。它负责准备构建目录：
#   1. git clone 上游 v3.8.3-patch.18，并校验提交 SHA；
#   2. 按顺序应用 infra/openim/patches/*.patch；
#   3. 把这个 Dockerfile 复制进去，再执行 docker build。
#
# 和上游 Dockerfile 的区别：
# - 基础镜像、mage 版本全部锁定，不用 latest；
# - RELEASE=true：去掉调试符号（-trimpath -s -w），不用 upx 压缩；
# - 构建时运行补丁自带的单元测试；
# - 以非 root 用户（10001）运行。
#
# 本机走 TLS 拦截代理时，可以把代理 CA 作为 BuildKit secret 传入（id=proxy_ca），只在下载依赖时使用，不进镜像。

# 基础镜像来源：默认 Docker Hub；被限流时可以换成同一 digest 的镜像站（例如 mirror.gcr.io/library），digest 保证内容相同
ARG BASE_REGISTRY=docker.io/library

FROM ${BASE_REGISTRY}/golang:1.26.9-alpine3.24@sha256:3082400e369fa24d5fc60bca20edab3f6d604e0c5a690ec66b295eff4dd87ade AS builder

ENV CGO_ENABLED=0 GOTOOLCHAIN=local
WORKDIR /openim-server

RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    go install github.com/magefile/mage@v1.17.2

COPY go.mod go.sum ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    go mod download && go mod verify

COPY . .
# 补丁自带的单元测试（只测消息网关的发送校验，不需要外部服务）
RUN go test -count=1 -run TestCheckClientMsg ./internal/msggateway/
RUN RELEASE=true COMPRESS=false mage build \
    && mage -compile ./mage -ldflags "-s -w"

FROM ${BASE_REGISTRY}/alpine:3.24.2@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6

RUN addgroup -S -g 10001 openim && adduser -S -D -H -u 10001 -G openim openim
WORKDIR /openim-server
COPY --from=builder /openim-server/_output/bin ./_output/bin
COPY --from=builder /openim-server/config ./config
COPY --from=builder /openim-server/start-config.yml ./start-config.yml
COPY --from=builder /openim-server/mage ./mage
# mage start 会在 _output 下建 logs、tmp 等目录；其余文件对运行用户只读
RUN mkdir -p _output/logs _output/tmp _output/export _output/tools \
    && chown -R openim:openim _output/logs _output/tmp _output/export _output/tools

LABEL org.opencontainers.image.title="openim-server (My Workspace patched)" \
      org.opencontainers.image.version="v3.8.3-patch.18-myws.2" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/openimsdk/open-im-server" \
      org.opencontainers.image.revision="120cee6d5783959a02ea1acfa414589fe5aae90f"

USER 10001:10001
ENTRYPOINT ["sh", "-c", "./mage start && sleep infinity"]
