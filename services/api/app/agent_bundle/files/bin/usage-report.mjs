#!/usr/bin/env node
// token 用量上报（T-0104，My Workspace docs/v2/TIMELINE_PLAN.md 第 2.4 节）。
// 宿主机 crontab 每分钟在 OpenClaw 容器里运行一次：
//   * * * * * cd <接入包目录> && docker compose exec -T openclaw node /opt/myworkspace/bin/usage-report.mjs
// 1. 调本机网关的 sessions.usage（只读，要 operator.read；网关令牌不离开容器）：
//    起止日期都是今天（UTC），每个会话一行（默认的 instance 分组），最多 1000 行；
// 2. 把每个会话今天的累计 token 发给工作台 POST /agent-api/v1/usage（实例凭证）。
//    会话只报 key 的 SHA-256；刚过零点的 10 分钟内昨天的也再报一次，免得丢掉零点前最后一分钟。
// 工作台按（实例、日期、会话）算增量，所以某一分钟没报上去，下一分钟会补上（累计值）。
// 正在刷新、还没算好的会话（usage 为 null、computing / refreshing / staleSince）报 null，工作台跳过。
// 字段已在 OpenClaw 2026.9.36 上核验（My Workspace 核验 ADR 2026-11-04 第 2 节）；取不到就不报。
// 只用 Node 标准库。
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import http from "node:http";
import https from "node:https";
import { pathToFileURL } from "node:url";

export const ROW_LIMIT = 1000;
export const SESSIONS_PER_REPORT = 500;
const GATEWAY_TIMEOUT_MS = 45000;
const POST_TIMEOUT_MS = 10000;
const DAY_MS = 24 * 60 * 60 * 1000;

export const utcDay = (date) => date.toISOString().slice(0, 10);

// 今天；刚过零点（UTC 00:00 ~ 00:09）时先补报昨天
export function daysToReport(now) {
  const today = utcDay(now);
  if (now.getUTCHours() === 0 && now.getUTCMinutes() < 10) {
    return [utcDay(new Date(now.getTime() - DAY_MS)), today];
  }
  return [today];
}

export const usageParams = (day) => ({
  mode: "utc",
  startDate: day,
  endDate: day,
  agentScope: "all",
  limit: ROW_LIMIT,
});

const count = (value) => (Number.isSafeInteger(value) && value >= 0 ? value : null);

export const sessionHash = (row) =>
  createHash("sha256").update(`${row.key}\n${row.sessionId ?? ""}`).digest("hex");

// sessions.usage 的结果 → 上报的会话列表。只取明确的四个字段，取不到或不是非负整数就报 null。
export function buildSessions(result) {
  if (!result || typeof result !== "object" || !Array.isArray(result.sessions)) {
    throw new Error("unexpected sessions.usage result");
  }
  const rows = new Map();
  for (const row of result.sessions) {
    if (!row || typeof row.key !== "string" || row.key === "") continue;
    const id = sessionHash(row);
    const usage = row.usage;
    const pending =
      usage === null ||
      typeof usage !== "object" ||
      row.computing === true ||
      usage.refreshing === true ||
      usage.staleSince !== undefined;
    const values = pending
      ? null
      : [count(usage.input), count(usage.output), count(usage.cacheRead), count(usage.cacheWrite)];
    if (values === null || values.some((v) => v === null)) {
      if (!rows.has(id)) rows.set(id, { session: id, usage: null });
      continue;
    }
    rows.set(id, {
      session: id,
      usage: { input: values[0], output: values[1], cache_read: values[2], cache_write: values[3] },
    });
  }
  return [...rows.values()];
}

// 会话多于返回的行数（超过 ROW_LIMIT）：少报的那些会话下次也补不上，写一条警告
export function truncated(result) {
  const total = result?.aggregates?.sessionCount;
  return typeof total === "number" && total > (result.sessions?.length ?? 0);
}

export function chunks(items, size) {
  if (items.length === 0) return [[]];
  const out = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

function gatewayCall(params) {
  const cwd = process.env.MYWS_OPENCLAW_DIR || "/app";
  const args = ["openclaw.mjs", "gateway", "call", "sessions.usage", "--params", JSON.stringify(params), "--json"];
  return new Promise((resolve, reject) => {
    execFile(process.execPath, args, { cwd, timeout: GATEWAY_TIMEOUT_MS, maxBuffer: 32 * 1024 * 1024 }, (error, stdout) => {
      if (error) return reject(new Error(`sessions.usage failed: ${error.code ?? error.signal ?? "error"}`));
      try { resolve(JSON.parse(stdout)); } catch { reject(new Error("sessions.usage returned invalid json")); }
    });
  });
}

function post(path, body) {
  const base = process.env.MYWS_AGENT_API, token = process.env.MYWS_INSTANCE_TOKEN;
  return new Promise((resolve, reject) => {
    const url = new URL(base.replace(/\/$/, "") + path);
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
      res.resume();
      res.on("end", () => (res.statusCode === 200 ? resolve() : reject(new Error(`workspace returned ${res.statusCode}`))));
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
    req.end(payload);
  });
}

export async function main(now = new Date()) {
  if (!process.env.MYWS_AGENT_API || !process.env.MYWS_INSTANCE_TOKEN) {
    throw new Error("MYWS_AGENT_API / MYWS_INSTANCE_TOKEN not set");
  }
  for (const day of daysToReport(now)) {
    const result = await gatewayCall(usageParams(day));
    if (truncated(result)) process.stderr.write(`usage-report: more than ${ROW_LIMIT} sessions on ${day}, some were not reported\n`);
    for (const sessions of chunks(buildSessions(result), SESSIONS_PER_REPORT)) {
      await post("/v1/usage", { day, sessions });
    }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    process.stderr.write(`usage-report: ${error.message}\n`);
    process.exit(1);
  });
}
