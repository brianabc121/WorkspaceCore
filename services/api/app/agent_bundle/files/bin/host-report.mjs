#!/usr/bin/env node
// 服务器状态上报（T-0113、T-0146，My Workspace docs/v2/AGENT_OPS_PLAN.md 第 5 节、
// docs/v2/MULTI_OPENCLAW_PLAN.md 第 5 节）。宿主机 crontab 每分钟在 OpenClaw 容器里运行一次：
//   * * * * * cd <接入包目录> && docker compose exec -T openclaw node /opt/myworkspace/bin/host-report.mjs
// 发给工作台 POST /agent-api/v1/host-metrics：
// - 整台服务器：/proc/stat、/proc/loadavg、/proc/meminfo、/proc/uptime 与两个分区的统计
//   （这些在容器里读到的就是整台服务器的数值，核验 ADR 2026-11-04 第 1 节、2026-11-07 第 2 节）；
// - 这个实例：自己容器的 cgroup（CPU 用量、内存、进程数）、数据目录大小（每 10 分钟一次）、
//   自己网卡的收发字节（每个实例有自己的网络命名空间）；
// - 服务器指纹：boot_id 的 HMAC（盐是 MYWS_HOST_SALT），同一台服务器上的实例相同，不报原值。
// 只报数字：不报进程、文件名、环境变量、IP 地址；网卡名只用来找默认路由那块网卡，不上报。
// CPU 使用率和网速由工作台用两次上报的累计值之差算出。只用 Node 标准库。
import { createHmac } from "node:crypto";
import { lstatSync, readdirSync, readFileSync, statfsSync } from "node:fs";
import http from "node:http";
import https from "node:https";
import os from "node:os";
import { pathToFileURL } from "node:url";

const POST_TIMEOUT_MS = 10000;
// 测试用：从另一个目录读 /proc 与 /sys（默认就是真实的 /proc、/sys）
const PROC = () => process.env.MYWS_PROC_ROOT || "/proc";
const SYS = () => process.env.MYWS_SYS_ROOT || "/sys";
// 接入包目录在容器里的挂载点（OpenClaw 的会话与记忆在这里）；容器根目录在 Docker 数据目录上
export const DATA_PATH = "/home/node/.openclaw";

const read = (path) => readFileSync(path, "utf8");
const num = (value) => (Number.isFinite(value) && value >= 0 ? Math.round(value) : null);

// /proc/stat 第一行：cpu user nice system idle iowait irq softirq steal ...（单位 jiffies）
export function cpuTimes(text) {
  const line = text.split("\n").find((l) => l.startsWith("cpu "));
  if (!line) return null;
  const parts = line.trim().split(/\s+/).slice(1, 9).map(Number);
  if (parts.length < 4 || parts.some((v) => !Number.isFinite(v))) return null;
  const total = parts.reduce((a, b) => a + b, 0);
  const idle = parts[3] + (parts[4] ?? 0);
  return { total, idle };
}

// /proc/loadavg：1 / 5 / 15 分钟负载
export function loadavg(text) {
  const [a, b, c] = text.trim().split(/\s+/).map(Number);
  return [a, b, c].every(Number.isFinite) ? [a, b, c] : null;
}

// /proc/meminfo（kB）→ 字节
export function meminfo(text) {
  const values = {};
  for (const line of text.split("\n")) {
    const m = /^(\w+):\s+(\d+)\s*kB/.exec(line);
    if (m) values[m[1]] = Number(m[2]) * 1024;
  }
  if (!("MemTotal" in values) || !("MemAvailable" in values)) return null;
  return {
    total: values.MemTotal,
    available: values.MemAvailable,
    swap_total: values.SwapTotal ?? 0,
    swap_free: values.SwapFree ?? 0,
  };
}

// /proc/net/route 里目标为 00000000 的一行：默认路由那块网卡
export function defaultInterface(text) {
  for (const line of text.split("\n").slice(1)) {
    const cols = line.trim().split(/\s+/);
    if (cols.length > 2 && cols[1] === "00000000") return cols[0];
  }
  return null;
}

// /proc/net/dev 里这块网卡的收、发累计字节（第 1、9 列）
export function interfaceBytes(text, name) {
  for (const line of text.split("\n")) {
    const idx = line.indexOf(":");
    if (idx < 0 || line.slice(0, idx).trim() !== name) continue;
    const cols = line.slice(idx + 1).trim().split(/\s+/).map(Number);
    if (cols.length >= 9 && Number.isFinite(cols[0]) && Number.isFinite(cols[8])) {
      return { rx: cols[0], tx: cols[8] };
    }
  }
  return null;
}

// 容器内存上限：cgroup v2 的 memory.max，或 v1 的 memory.limit_in_bytes；没有上限（max 或极大值）时为 null
export function memoryLimit(v2, v1, physical) {
  for (const text of [v2, v1]) {
    if (text == null) continue;
    const value = text.trim();
    if (value === "max" || value === "") return null;
    const bytes = Number(value);
    if (Number.isFinite(bytes) && bytes > 0) return physical && bytes >= physical ? null : bytes;
  }
  return null;
}

function tryRead(path) {
  try { return read(path); } catch { return null; }
}

// 只有一个数字的 cgroup 文件
function cgroupNumber(text) {
  if (text == null) return null;
  const value = Number(text.trim());
  return Number.isFinite(value) && value >= 0 ? value : null;
}

// memory.stat 之类“键 值”格式的文件
export function keyValues(text) {
  const values = {};
  for (const line of (text ?? "").split("\n")) {
    const [key, value] = line.trim().split(/\s+/);
    if (key && value !== undefined && Number.isFinite(Number(value))) values[key] = Number(value);
  }
  return values;
}

// 自己容器的 cgroup：v2（/sys/fs/cgroup/cgroup.controllers 存在）或 v1（每个控制器一个目录）。
// 内存已用 = 当前用量 − 可回收的文件缓存（inactive_file），与 docker stats 的算法相同
export function containerUsage(sys) {
  const root = `${sys}/fs/cgroup`;
  const v2 = tryRead(`${root}/cgroup.controllers`) != null;
  let usec = null, current = null, inactive = 0, pids = null;
  if (v2) {
    usec = keyValues(tryRead(`${root}/cpu.stat`)).usage_usec ?? null;
    current = cgroupNumber(tryRead(`${root}/memory.current`));
    inactive = keyValues(tryRead(`${root}/memory.stat`)).inactive_file ?? 0;
    pids = cgroupNumber(tryRead(`${root}/pids.current`));
  } else {
    const ns = cgroupNumber(tryRead(`${root}/cpuacct/cpuacct.usage`));
    usec = ns == null ? null : Math.floor(ns / 1000);
    current = cgroupNumber(tryRead(`${root}/memory/memory.usage_in_bytes`));
    inactive = keyValues(tryRead(`${root}/memory/memory.stat`)).total_inactive_file ?? 0;
    pids = cgroupNumber(tryRead(`${root}/pids/pids.current`));
  }
  if (usec == null && current == null && pids == null) return null;
  return {
    cpu_usec: num(usec),
    mem_bytes: current == null ? null : num(Math.max(current - inactive, 0)),
    pids: num(pids),
  };
}

// 数据目录（会话、记忆、插件）的大小：累加普通文件的大小，不跟随符号链接；最多看 MAX_FILES 个文件
export const MAX_FILES = 200000;
export function directorySize(path, limit = MAX_FILES) {
  let bytes = 0, files = 0;
  const stack = [path];
  while (stack.length) {
    const dir = stack.pop();
    let entries;
    try { entries = readdirSync(dir, { withFileTypes: true }); } catch { continue; }
    for (const entry of entries) {
      const full = `${dir}/${entry.name}`;
      if (entry.isDirectory()) { stack.push(full); continue; }
      if (!entry.isFile()) continue;
      if (++files > limit) return { bytes: num(bytes), truncated: true };
      try { bytes += lstatSync(full).size; } catch { /* 刚被删掉的文件 */ }
    }
  }
  return { bytes: num(bytes), truncated: false };
}

// 服务器指纹：boot_id（同一个内核相同，重启后变）的 HMAC-SHA256，盐来自接入包
export function hostKey(bootId, salt) {
  const id = (bootId ?? "").trim();
  if (!salt || !/^[0-9a-f-]{36}$/.test(id)) return null;
  return createHmac("sha256", salt).update(id).digest("hex");
}

function disk(path) {
  try {
    const s = statfsSync(path);
    return { total: num(s.blocks * s.bsize), free: num(s.bavail * s.bsize) };
  } catch {
    return null;
  }
}

export function collect(now = new Date()) {
  const cpus = os.cpus();
  const stat = tryRead(`${PROC()}/stat`);
  const mem = meminfo(read(`${PROC()}/meminfo`));
  if (!mem) throw new Error("cannot read meminfo");
  const load = loadavg(read(`${PROC()}/loadavg`));
  const iface = defaultInterface(tryRead(`${PROC()}/net/route`) ?? "");
  const bytes = iface ? interfaceBytes(tryRead(`${PROC()}/net/dev`) ?? "", iface) : null;
  const uptime = Number((tryRead(`${PROC()}/uptime`) ?? "").trim().split(/\s+/)[0]);
  const times = stat ? cpuTimes(stat) : null;
  return {
    cpu: {
      cores: cpus.length || null,
      model: (cpus[0]?.model ?? "").slice(0, 120) || null,
      load: load ?? null,
      total_ticks: times ? num(times.total) : null,
      idle_ticks: times ? num(times.idle) : null,
    },
    memory: {
      total: num(mem.total),
      available: num(mem.available),
      swap_total: num(mem.swap_total),
      swap_free: num(mem.swap_free),
      limit: memoryLimit(tryRead(`${SYS()}/fs/cgroup/memory.max`), tryRead(`${SYS()}/fs/cgroup/memory/memory.limit_in_bytes`), mem.total),
    },
    disks: { data: disk(process.env.MYWS_DATA_PATH || DATA_PATH), root: disk(process.env.MYWS_ROOT_PATH || "/") },
    network: bytes ? { rx_bytes: num(bytes.rx), tx_bytes: num(bytes.tx) } : null,
    uptime_seconds: num(uptime),
    container: container(now),
    host_key: hostKey(tryRead(`${PROC()}/sys/kernel/random/boot_id`), process.env.MYWS_HOST_SALT),
  };
}

function container(now) {
  const usage = containerUsage(SYS());
  if (!usage) return null;
  // 数据目录大小每 10 分钟算一次（文件多时要遍历一阵）；其他分钟不报，工作台沿用上一次的值
  const size = now.getUTCMinutes() % 10 === 0 || process.env.MYWS_DATA_SIZE === "1"
    ? directorySize(process.env.MYWS_DATA_PATH || DATA_PATH)
    : null;
  return {
    ...usage,
    data_bytes: size ? size.bytes : null,
    data_truncated: size ? size.truncated : false,
  };
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
      res.on("end", () => {
        if (res.statusCode === 200) return resolve("ok");
        // 每个实例每分钟最多收 2 次：这一分钟已经报过（定时任务或“立即上报一次”），工作台的数据已是最新，不算失败
        if (res.statusCode === 429) return resolve("rate_limited");
        reject(new Error(`workspace returned ${res.statusCode}`));
      });
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
    req.end(payload);
  });
}

export async function main() {
  if (!process.env.MYWS_AGENT_API || !process.env.MYWS_INSTANCE_TOKEN) {
    throw new Error("MYWS_AGENT_API / MYWS_INSTANCE_TOKEN not set");
  }
  const result = await post("/v1/host-metrics", collect());
  if (result === "rate_limited") {
    process.stderr.write("host-report: already reported this minute (the workspace accepts 2 per minute), skipped\n");
  }
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    process.stderr.write(`host-report: ${error.message}\n`);
    process.exit(1);
  });
}
