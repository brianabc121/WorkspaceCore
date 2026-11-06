#!/usr/bin/env node
// 唤醒（T-0121，My Workspace docs/v2/AGENT_MESSAGING_PLAN.md 第 7.2 节）。
// 宿主机 crontab 每分钟在 OpenClaw 容器里运行一次：
//   * * * * * cd <接入包目录> && docker compose exec -T openclaw node /opt/myworkspace/bin/wakeup.mjs
// 1. 向工作台领取到期的唤醒 POST /agent-api/v1/wakeups/claim（实例凭证；领取后工作台不会再发给别人）；
// 2. 每个唤醒在容器里跑一轮：node openclaw.mjs agent --session-key <会话> --message-file <提示词>
//    （OpenClaw 2026.9.36 docs/tools/agent-send.md；不加 --deliver：要发消息时智能体自己用 im_send）；
// 3. 把结果（完成 / 失败与原因）报回 POST /agent-api/v1/wakeups/{id}/result。
// 同一时间只跑一个本脚本（锁文件）；一轮最多跑 10 分钟。只用 Node 标准库。
import { execFile } from "node:child_process";
import { closeSync, mkdtempSync, openSync, readFileSync, rmSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import http from "node:http";
import https from "node:https";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

export const RUN_TIMEOUT_SECONDS = 600;
const POST_TIMEOUT_MS = 10000;
const LOCK = process.env.MYWS_WAKEUP_LOCK || path.join(os.tmpdir(), "myworkspace-wakeup.lock");
// 锁文件超过一轮的时长加余量还在，就是上次异常退出留下的
const STALE_LOCK_MS = (RUN_TIMEOUT_SECONDS + 120) * 1000;

// 会话键只允许字母、数字和 : _ - .（工作台生成，这里再挡一次）
const KEY = /^[A-Za-z0-9:_.\-]{1,200}$/;

export function agentArgs(item, file) {
  if (!item || typeof item.id !== "string" || !KEY.test(item.session_key ?? "")) throw new Error("bad wakeup");
  return ["openclaw.mjs", "agent", "--session-key", item.session_key, "--message-file", file,
    "--timeout", String(RUN_TIMEOUT_SECONDS), "--json"];
}

// 只回报一行原因（不带模型输出、会话内容）
export function failure(error) {
  const text = String(error?.message ?? error ?? "error").replace(/\s+/g, " ").trim();
  return text.slice(0, 300) || "error";
}

function lock() {
  try {
    const fd = openSync(LOCK, "wx");
    writeFileSync(fd, String(process.pid));
    closeSync(fd);
    return true;
  } catch {
    try {
      if (Date.now() - statSync(LOCK).mtimeMs > STALE_LOCK_MS) { unlinkSync(LOCK); return lock(); }
    } catch { /* 另一个进程刚删掉 */ }
    return false;
  }
}

function runAgent(item) {
  const cwd = process.env.MYWS_OPENCLAW_DIR || "/app";
  const dir = mkdtempSync(path.join(os.tmpdir(), "myws-wakeup-"));
  const file = path.join(dir, "message.md");
  writeFileSync(file, item.message, { encoding: "utf8", mode: 0o600 });
  return new Promise((resolve, reject) => {
    execFile(process.execPath, agentArgs(item, file),
      { cwd, timeout: (RUN_TIMEOUT_SECONDS + 30) * 1000, maxBuffer: 8 * 1024 * 1024 },
      (error, _stdout, stderr) => {
        rmSync(dir, { recursive: true, force: true });
        if (error) {
          const last = String(stderr || "").trim().split("\n").pop() || "";
          return reject(new Error(`openclaw agent exited ${error.code ?? error.signal ?? "error"}${last ? `: ${last}` : ""}`));
        }
        resolve();
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

export async function main({ run = runAgent, send = post } = {}) {
  if (!process.env.MYWS_AGENT_API || !process.env.MYWS_INSTANCE_TOKEN) {
    throw new Error("MYWS_AGENT_API / MYWS_INSTANCE_TOKEN not set");
  }
  const claimed = await send("/v1/wakeups/claim", {});
  for (const item of claimed.items ?? []) {
    let result = { status: "done" };
    try { await run(item); } catch (error) { result = { status: "failed", error: failure(error) }; }
    await send(`/v1/wakeups/${encodeURIComponent(item.id)}/result`, result)
      .catch((error) => process.stderr.write(`wakeup: result not reported: ${error.message}\n`));
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (!lock()) process.exit(0); // 上一次还在跑
  main()
    .catch((error) => { process.stderr.write(`wakeup: ${error.message}\n`); process.exitCode = 1; })
    .finally(() => { try { unlinkSync(LOCK); } catch { /* 已经没了 */ } });
}
