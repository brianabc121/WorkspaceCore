#!/usr/bin/env node
// 应用工作台给这个实例设置的主模型（T-0205，My Workspace docs/decisions/2026-11-09-model-connections.md）。
// 宿主机 crontab 每分钟在 OpenClaw 容器里运行一次：
//   * * * * * cd <接入包目录> && docker compose exec -T openclaw node /opt/myworkspace/bin/model-config.mjs
// 1. 取自己的配置 GET /agent-api/v1/model-config（实例凭证；不含 Key）。没有绑定（409）时什么也不做；
// 2. apply_key 或 fingerprint 与上次应用的不同：只改受管的 provider（键以 managed_prefix 开头）与主模型，
//    用 OpenClaw 自己的 `config patch` 一次校验写入（先 --dry-run --allow-exec 校验 schema 与 Key 能否取到），
//    网关热加载 models / agents.defaults.model（不用重启，核验记录第 4 节），再 `secrets reload` 换上新 Key；
//    其他配置（MCP、IM 渠道、工具限制、用户自己的 provider）一概不动；
// 3. 把结果（applied / failed + 固定原因）报回 POST /agent-api/v1/model-config/applied；回报失败下次重报。
// 配置里只有 exec SecretRef（mc_<连接 ID>），Key 由取密钥程序在需要时向工作台取。只用 Node 标准库。
import { execFile } from "node:child_process";
import { readFileSync, renameSync, writeFileSync } from "node:fs";
import http from "node:http";
import https from "node:https";
import { pathToFileURL } from "node:url";

const CLI_TIMEOUT_MS = 90000;
const HTTP_TIMEOUT_MS = 10000;
const RELOAD_SETTLE_MS = 3000;
// 旧接入包模板里的 openai 段（只有一个指向 openai_api_key 的 exec SecretRef）：换成受管 provider 时一起移除
const LEGACY_REF = "openai_api_key";

const stateDir = () => process.env.OPENCLAW_STATE_DIR || "/home/node/.openclaw";
const configPath = () => process.env.OPENCLAW_CONFIG_PATH || `${stateDir()}/openclaw.json`;
const statePath = () => `${stateDir()}/myworkspace-model-state.json`;

function isManagedKey(key, prefix) {
  return typeof key === "string" && key.startsWith(prefix) && /^[a-z0-9]+$/.test(key);
}

function isLegacyTemplate(provider) {
  if (!provider || typeof provider !== "object") return false;
  const keys = Object.keys(provider);
  const ref = provider.apiKey;
  return keys.length === 1 && keys[0] === "apiKey" && ref && typeof ref === "object"
    && ref.source === "exec" && ref.provider === "myworkspace" && ref.id === LEGACY_REF;
}

function primaryOf(model) {
  if (typeof model === "string") return model;
  if (model && typeof model === "object" && typeof model.primary === "string") return model.primary;
  return null;
}

// 由当前配置与工作台给的目标算出 `config patch` 的补丁（对象递归合并、null 删除）。纯函数，便于测试。
export function buildPatch(current, target) {
  const prefix = target.managed_prefix;
  const providers = current?.models?.providers ?? {};
  const patchProviders = {};
  for (const [key, value] of Object.entries(providers)) {
    if (isManagedKey(key, prefix) && key !== target.provider_key) patchProviders[key] = null;
    else if (target.state === "active" && key === "openai" && isLegacyTemplate(value)) patchProviders[key] = null;
  }
  const patch = {};
  const model = current?.agents?.defaults?.model;
  const primary = primaryOf(model);
  if (target.state === "active") {
    patchProviders[target.provider_key] = {
      baseUrl: target.base_url,
      api: target.api,
      apiKey: { source: "exec", provider: "myworkspace", id: target.secret_ref_id },
      models: [{ id: target.model_id, name: target.model_name || target.model_id }],
    };
    if (primary !== target.primary) patch.agents = { defaults: { model: { primary: target.primary } } };
  } else if (primary) {
    const providerOfPrimary = primary.split("/", 1)[0].toLowerCase();
    if (isManagedKey(providerOfPrimary, prefix)) {
      patch.agents = { defaults: { model: typeof model === "string" ? null : { primary: null } } };
    }
  }
  if (Object.keys(patchProviders).length) patch.models = { providers: patchProviders };
  return patch;
}

export function failure(error) {
  return String(error?.message ?? error ?? "error").replace(/\s+/g, " ").trim().slice(0, 200) || "error";
}

function cli(args, input) {
  const cwd = process.env.MYWS_OPENCLAW_DIR || "/app";
  return new Promise((resolve) => {
    const child = execFile(process.execPath, ["openclaw.mjs", ...args],
      { cwd, timeout: CLI_TIMEOUT_MS, maxBuffer: 4 * 1024 * 1024 },
      (error, stdout) => resolve({ ok: !error, code: error?.code ?? 0, stdout: String(stdout || "") }));
    if (input !== undefined) child.stdin.end(input);
  });
}

function lastJson(text) {
  const start = text.indexOf("{");
  if (start < 0) return null;
  try { return JSON.parse(text.slice(start)); } catch { return null; }
}

function request(method, pathname, body) {
  const base = process.env.MYWS_AGENT_API, token = process.env.MYWS_INSTANCE_TOKEN;
  if (!base || !token) return Promise.reject(new Error("MYWS_AGENT_API / MYWS_INSTANCE_TOKEN not set"));
  return new Promise((resolve, reject) => {
    const url = new URL(base.replace(/\/$/, "") + pathname);
    const payload = body === undefined ? undefined : JSON.stringify(body);
    const headers = { authorization: `Bearer ${token}`, accept: "application/json" };
    if (payload !== undefined) {
      headers["content-type"] = "application/json";
      headers["content-length"] = Buffer.byteLength(payload);
    }
    const opts = { method, timeout: HTTP_TIMEOUT_MS, headers };
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
        let data = null;
        try { data = JSON.parse(Buffer.concat(parts).toString("utf8") || "null"); } catch { data = null; }
        resolve({ status: res.statusCode, data });
      });
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
    req.end(payload);
  });
}

function readState() {
  try { return JSON.parse(readFileSync(statePath(), "utf8")); } catch { return {}; }
}

function writeState(state) {
  const path = statePath(), tmp = `${path}.tmp`;
  writeFileSync(tmp, JSON.stringify(state), { mode: 0o600 });
  renameSync(tmp, path);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function apply(target, { run = cli, read = () => readFileSync(configPath(), "utf8"), wait = sleep } = {}) {
  let current;
  try { current = JSON.parse(read()); } catch { return { result: "manual_required", reason: "config_unreadable" }; }
  const patch = buildPatch(current, target);
  if (Object.keys(patch).length) {
    const text = JSON.stringify(patch);
    // 先校验：schema 与 exec SecretRef 能否取到（会调用取密钥程序向工作台取一次 Key）
    const dry = await run(["config", "patch", "--stdin", "--dry-run", "--allow-exec", "--json"], text);
    const checked = lastJson(dry.stdout);
    if (!dry.ok || !checked?.ok) {
      if (checked?.checks?.schema === false) return { result: "failed", reason: "invalid_config" };
      if (checked?.checks?.resolvability === false) return { result: "failed", reason: "credential_unavailable" };
      return { result: "failed", reason: "validate_failed" };
    }
    const written = await run(["config", "patch", "--stdin"], text);
    if (!written.ok) return { result: "failed", reason: "patch_failed" };
    await wait(RELOAD_SETTLE_MS); // 网关监视配置文件、热加载 models / agents.defaults.model
  }
  // 同一个 SecretRef id 换了值（Key 更换）只有 secrets reload 才会重新取（核验记录第 4 节）
  const reload = await run(["secrets", "reload", "--json"]);
  const reloaded = lastJson(reload.stdout);
  if (!reload.ok || reloaded?.ok === false) return { result: "failed", reason: "reload_failed" };
  return { result: "applied", reason: null };
}

export async function main() {
  const got = await request("GET", "/v1/model-config");
  // 没有绑定（no_binding）或目标连接不可用（connection_unavailable，例如被停用）：保持现有配置
  if (got.status === 409) return got.data?.error?.details?.reason ?? "no_model_binding";
  if (got.status !== 200 || !got.data) throw new Error(`workspace returned ${got.status}`);
  const target = got.data;
  const local = readState();
  const changed = local.apply_key !== target.apply_key || local.fingerprint !== target.fingerprint;
  const waiting = target.job_state === "queued" || target.job_state === "running";
  if (!changed && !waiting) return "unchanged";
  let outcome = { result: local.result, reason: local.reason ?? null };
  if (changed || !outcome.result) {
    outcome = await apply(target);
    writeState({ apply_key: target.apply_key, fingerprint: target.fingerprint, ...outcome });
  }
  const body = {
    binding_version: target.binding_version,
    desired_revision: target.desired_revision,
    result: outcome.result,
    observed_at: new Date().toISOString(),
    ...(outcome.reason ? { reason: outcome.reason } : {}),
  };
  const sent = await request("POST", "/v1/model-config/applied", body);
  if (sent.status === 409) return "stale";
  if (sent.status !== 200) throw new Error(`report returned ${sent.status}`);
  return `${outcome.result}${outcome.reason ? `:${outcome.reason}` : ""}`;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  main().then(
    (status) => { process.stdout.write(`${status}\n`); },
    (error) => { process.stderr.write(`model-config: ${failure(error)}\n`); process.exitCode = 1; },
  );
}
