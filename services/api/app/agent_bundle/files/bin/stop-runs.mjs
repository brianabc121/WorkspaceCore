#!/usr/bin/env node
// 终止正在执行的任务（T-0126，My Workspace docs/v2/AGENT_MESSAGING_PLAN.md 第 7.3 节）。
// 宿主机 crontab 每分钟在 OpenClaw 容器里运行一次：
//   * * * * * cd <接入包目录> && docker compose exec -T openclaw node /opt/myworkspace/bin/stop-runs.mjs
// 1. 向工作台领取终止令 POST /agent-api/v1/stops/claim（实例凭证；管理员或有权限的智能体下的令）；
// 2. 有终止令时，调本机网关的 sessions.list({ activeOnly: true }) 找出正在运行或排队的会话，
//    对每个会话调 sessions.abort({ key, clearQueued: true })（global / unknown 改带 agentId）（OpenClaw 2026.9.36 的网关 RPC：
//    docs/gateway/protocol/rpc-session-control.md、rpc-bootstrap-and-events.md；网关令牌不离开容器）；
// 3. 把中止了几个会话报回 POST /agent-api/v1/stops/{id}/result。
// 没有终止令时什么也不做（不调网关）。只用 Node 标准库。
import { execFile } from "node:child_process";
import { readFileSync } from "node:fs";
import http from "node:http";
import https from "node:https";
import { pathToFileURL } from "node:url";

const GATEWAY_TIMEOUT_MS = 30000;
const POST_TIMEOUT_MS = 10000;
// 字面量 global / unknown 会话按 OpenClaw 自己的归属规则处理：带上它的 agentId，不带 clearQueued
// （rpc-session-control.md；activeOnly 的结果里这两种行带着 agentId，rpc-bootstrap-and-events.md）
const SENTINELS = new Set(["global", "unknown"]);

// sessions.list 的结果里取要中止的会话：只取明确的 key 字段（hasActiveRun 为 false 的只是排队，也中止）
export function activeSessions(result) {
  const rows = Array.isArray(result) ? result : (result?.sessions ?? result?.items ?? []);
  if (!Array.isArray(rows)) throw new Error("unexpected sessions.list result");
  const out = [], seen = new Set();
  for (const row of rows) {
    if (!row || typeof row.key !== "string" || !row.key) continue;
    const agentId = SENTINELS.has(row.key) && typeof row.agentId === "string" && row.agentId ? row.agentId : undefined;
    const id = `${agentId ?? ""}\u0000${row.key}`;
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(agentId ? { key: row.key, agentId } : { key: row.key });
  }
  return out;
}

export const abortParams = ({ key, agentId }) =>
  SENTINELS.has(key) ? (agentId ? { key, agentId } : { key }) : { key, clearQueued: true };

export function failure(error) {
  return String(error?.message ?? error ?? "error").replace(/\s+/g, " ").trim().slice(0, 300) || "error";
}

function gatewayCall(method, params) {
  const cwd = process.env.MYWS_OPENCLAW_DIR || "/app";
  const args = ["openclaw.mjs", "gateway", "call", method, "--params", JSON.stringify(params), "--json"];
  return new Promise((resolve, reject) => {
    execFile(process.execPath, args, { cwd, timeout: GATEWAY_TIMEOUT_MS, maxBuffer: 16 * 1024 * 1024 }, (error, stdout) => {
      if (error) return reject(new Error(`${method} failed: ${error.code ?? error.signal ?? "error"}`));
      try { resolve(JSON.parse(stdout || "null")); } catch { reject(new Error(`${method} returned invalid json`)); }
    });
  });
}

function post(pathname, body) {
  const base = process.env.MYWS_AGENT_API, token = process.env.MYWS_INSTANCE_TOKEN;
  return new Promise((resolve, reject) => {
    const url = new URL(base.replace(/\/$/, "") + pathname);
    const payload = JSON.stringify(body);
    const headers = {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
      "content-length": Buffer.byteLength(payload),
    };
    const opts = { method: "POST", timeout: POST_TIMEOUT_MS, headers };
    if (url.protocol === "https:") {
      if (process.env.MYWS_CA_FILE) opts.ca = readFileSync(process.env.MYWS_CA_FILE);
      if (process.env.MYWS_CLIENT_CERT) { opts.cert = readFileSync(process.env.MYWS_CLIENT_CERT); opts.key = readFileSync(process.env.MYWS_CLIENT_KEY); }
    } else if (!["127.0.0.1", "localhost", "::1", process.env.MYWS_LOCAL_TUNNEL_HOST].filter(Boolean).includes(url.hostname)) {
      return reject(new Error("refusing plain http to a non-loopback host"));
    }
    const req = (url.protocol === "https:" ? https : http).request(url, opts, (res) => {
      const parts = [];
      res.on("data", (c) => parts.push(c));
      res.on("end", () => {
        if (res.statusCode !== 200) return reject(new Error(`workspace returned ${res.statusCode}`));
        try { resolve(JSON.parse(Buffer.concat(parts).toString("utf8") || "{}")); } catch { reject(new Error("invalid json")); }
      });
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
    req.end(payload);
  });
}

// 中止所有正在运行的会话，返回中止了几个；有一个失败就整体算失败（报回原因，管理员可以再下令）
export async function abortAll(call = gatewayCall) {
  const sessions = activeSessions(await call("sessions.list", { activeOnly: true }));
  for (const session of sessions) await call("sessions.abort", abortParams(session));
  return sessions.length;
}

export async function main({ call = gatewayCall, send = post } = {}) {
  if (!process.env.MYWS_AGENT_API || !process.env.MYWS_INSTANCE_TOKEN) {
    throw new Error("MYWS_AGENT_API / MYWS_INSTANCE_TOKEN not set");
  }
  const claimed = await send("/v1/stops/claim", {});
  const items = claimed.items ?? [];
  if (items.length === 0) return;
  let result;
  try { result = { status: "done", aborted: await abortAll(call) }; }
  catch (error) { result = { status: "failed", aborted: 0, error: failure(error) }; }
  for (const item of items) {
    await send(`/v1/stops/${encodeURIComponent(item.id)}/result`, result)
      .catch((error) => process.stderr.write(`stop-runs: result not reported: ${error.message}\n`));
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    process.stderr.write(`stop-runs: ${error.message}\n`);
    process.exit(1);
  });
}
