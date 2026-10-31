#!/bin/sh
# MongoDB 第一次初始化数据卷时执行（docker-entrypoint-initdb.d）：为 OpenIM 建一个只读写 openim_v3 的用户。
# 密码来自环境变量 OPENIM_MONGO_PASSWORD；root 账号只用于这一步。
set -eu
mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin <<EOF
db = db.getSiblingDB("openim_v3");
if (!db.getUser("openim")) {
  db.createUser({ user: "openim", pwd: "$OPENIM_MONGO_PASSWORD", roles: [{ role: "readWrite", db: "openim_v3" }] });
}
EOF
