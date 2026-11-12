#!/usr/bin/env node
// openclaw-channel 0.3.3 的小补丁（T-0120，My Workspace docs/v2/AGENT_MESSAGING_PLAN.md 第 5.2 节、第 11 节 D6）。
//
// 插件把自动回复发出去失败时（工作台的 before 回调拒收，OpenIM 把 5001 ~ 5009 原样作为 errCode 返回，
// OC-0 核验第 5 项），原版只记一行日志 `deliver failed`，智能体并不知道。补丁在同一处再调用
// OpenClaw 插件运行时的 `api.runtime.system.enqueueSystemEvent(text, { agentId, sessionKey })`
// （OpenClaw 2026.9.36 docs/plugins/sdk-runtime/state-and-system.md），把“哪条回复没发出、为什么、怎么办”
// 放回**同一个会话**，智能体下一轮就看得到。补丁只加这张原因表和一次调用，不改别的逻辑。
//
// 第二处（T-0221）：插件调 `runtime.channel.routing.resolveAgentRoute` 时没有传对方（peer）。OpenClaw 2026.9.36
// 的 resolveAgentRoute 不看传入的 sessionKey，只用 peer 拼会话键，没有 peer 就返回主会话 `agent:main:main`，
// 于是一个实例在 OpenIM 的所有群聊、私聊（和定时心跳）挤在同一个会话里，上下文互相带入。补丁补上
// `peer: { kind: "group" | "direct", id }`：群的会话是 `agent:main:openim:group:<群 ID>`，私聊按接入包的
// `session.dmScope: per-channel-peer` 是 `agent:main:openim:direct:<发送者>`（都转小写；核验记录 6.1 的补充）。
//
// 用法（在锁定的 OpenClaw 镜像里以 node 用户运行，插件装好之后、OpenClaw 启动之前；重复运行没有副作用）：
//   node /opt/myworkspace/bin/channel-patch.mjs [插件的 dist/inbound.js]
// 只给锁定版本打：先核对 inbound.js 的 sha256 与 0.3.3 发布包里的原文件一致（发布包本身的 sha256
// 已在安装时核对），打完再核对结果的 sha256；已经打过就只核对、不再改。只打了第一处的旧补丁（T-0120 的结果，
// sha256 与 PATCHED_V1_SHA256 一致）就补上第二处，结果与从原文件打的完全相同。
// 退出码：0 成功、已升级或已打过，7 文件不是锁定版本的原文件（升级插件时要重新审查并更新这里），
// 8 结果与预期不符（没有写入）。只用 Node 标准库。
import { createHash } from "node:crypto";
import { readFileSync, renameSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

export const DEFAULT_TARGET = "/home/node/.openclaw/extensions/openclaw-channel/dist/inbound.js";
// @openim/openclaw-channel@0.3.3 发布包（sha256 3d10dca0…7643）里 dist/inbound.js 的 sha256
export const ORIGINAL_SHA256 = "5beb827deeb5cae9293fc7f363b97ba6a57bb548baf410039c544d4fa00a6cfc";
// 打完补丁后的 sha256（改了下面的补丁内容就要更新；agents 端到端验收在真实文件上核对）
export const PATCHED_SHA256 = "5fad9e48264a4680336ca6818936649ca3cfc63592322e509b7d0c4ec1cc2194";
// 只打了第一处的旧补丁（T-0120）的结果：遇到它就补上第二处
export const PATCHED_V1_SHA256 = "36e2670f72a66ff95ea672e5456e61d0f3101384d26e12dabda489f07c9ce67d";
export const MARKER = "myworkspace-patch:T-0120";
export const ROUTE_MARKER = "myworkspace-patch:T-0221";

// 原文件里给收到的消息定路由的调用（0.3.3 dist/inbound.js 第 308 ~ 313 行的后半段，必须恰好出现一次）
export const ROUTE_ANCHOR = [
  '        channel: "openim",',
  "        accountId: client.config.accountId,",
  '    }) ?? { agentId: "main", sessionKey: baseSessionKey };',
].join("\n");

const ROUTE_REPLACEMENT = [
  '        channel: "openim",',
  "        accountId: client.config.accountId,",
  `        peer: { kind: group ? "group" : "direct", id: String(group ? msg.groupID : msg.sendID) }, // ${ROUTE_MARKER}`,
  '    }) ?? { agentId: "main", sessionKey: baseSessionKey };',
].join("\n");

// 原文件里 deliver 的 catch 块（0.3.3 dist/inbound.js，必须恰好出现一次）
export const ANCHOR = [
  "                    catch (e) {",
  "                        api.logger?.error?.(`[openim] deliver failed: ${formatSdkError(e)}`);",
  "                    }",
].join("\n");

const REPLACEMENT = [
  "                    catch (e) {",
  "                        api.logger?.error?.(`[openim] deliver failed: ${formatSdkError(e)}`);",
  `                        mywsReplyRejected(api, route, sessionKey, group, e); // ${MARKER}`,
  "                    }",
].join("\n");

// 拒收码与原因：与工作台 app/domain/agent_outbox.py 的 REJECT_REASONS 一致（测试核对），
// 后半句是对“自动回复被拒收”的建议。5002 的文字回复由工作台在限额恢复后补发（方案第 5.3 节）。
export const REASONS = {
  5001: ["群里智能体连续发言已达上限，自动接力已暂停", "等管理员在群里发言后再说，不要换个说法重发；急事用 im_send 私聊管理员"],
  5002: ["你在这个群里每分钟的条数已达上限", "工作台会在限额恢复后补发这条回复（最多等 2 分钟），不要重复发同样的内容；结果用 im_outbox 查"],
  5003: ["智能体之间不能私聊", "在你们共同所在的群里 @ 它，或者用项目的 Issue"],
  5004: ["实例已停用，或智能体总开关已关闭", "现在发不出消息，请等管理员处理"],
  5005: ["你不在这个群里，或对方不是本工作空间的管理员", "用 im_list_targets 查看能发给谁"],
  5006: ["这个群关闭了接力，不能 @ 其他智能体", "去掉对其他智能体的 @ 后用 im_send 再发"],
  5007: ["智能体只能经工作台发附件", "用 im_send 的 attachments 发图片、文件、视频"],
  5009: ["工作台暂时判断不了", "稍后用 im_send 再发一次"],
};

const HELPER = `

// ---- ${MARKER}（My Workspace：自动回复被工作台拒收时告诉智能体，docs/v2/AGENT_MESSAGING_PLAN.md 5.2）----
const MYWS_REASONS = ${JSON.stringify(REASONS)};
function mywsReplyRejected(api, route, sessionKey, group, error) {
    try {
        const enqueue = api.runtime?.system?.enqueueSystemEvent;
        const code = Number(error?.errCode);
        const known = MYWS_REASONS[code];
        if (typeof enqueue !== "function" || !known)
            return;
        const where = group ? "群聊" : "私聊";
        const text = \`[工作台] 你刚才在这个\${where}里的回复没有发出（\${code}）：\${known[0]}。\${known[1]}。\`;
        Promise.resolve(enqueue(text, { agentId: route?.agentId || "main", sessionKey })).catch((err) => {
            api.logger?.warn?.(\`[openim] myworkspace notice failed: \${String(err)}\`);
        });
    }
    catch (err) {
        api.logger?.warn?.(\`[openim] myworkspace notice failed: \${String(err)}\`);
    }
}
`;

export const sha256 = (text) => createHash("sha256").update(text, "utf8").digest("hex");

// 纯文本变换：替换 catch 块、在文件末尾加上原因表和函数（函数声明在模块里会提升）
function replaceOnce(source, anchor, replacement, name) {
  const count = source.split(anchor).length - 1;
  if (count !== 1) throw new Error(`${name} found ${count} times`);
  return source.replace(anchor, replacement);
}

// 第二处：给 resolveAgentRoute 补上 peer（T-0221）
export function routeTransform(source) {
  return replaceOnce(source, ROUTE_ANCHOR, ROUTE_REPLACEMENT, "route anchor");
}

export function transform(source) {
  return routeTransform(replaceOnce(source, ANCHOR, REPLACEMENT, "anchor")) + HELPER;
}

function write(path, patched) {
  const temp = `${path}.myws-${process.pid}`;
  writeFileSync(temp, patched, { encoding: "utf8", mode: 0o644 });
  renameSync(temp, path);
}

export function apply(path = DEFAULT_TARGET) {
  const source = readFileSync(path, "utf8");
  const current = sha256(source);
  if (current === PATCHED_SHA256) return { status: "plugin_patch_already_applied", code: 0 };
  if (current === PATCHED_V1_SHA256) {
    // 旧补丁（只有第一处）：补上第二处，结果必须与从原文件打的一致
    const upgraded = routeTransform(source);
    const result = sha256(upgraded);
    if (result !== PATCHED_SHA256) return { status: "plugin_patch_result_mismatch", code: 8, sha256: result };
    write(path, upgraded);
    return { status: "plugin_patch_upgraded", code: 0 };
  }
  if (source.includes(MARKER) || source.includes(ROUTE_MARKER)) {
    return { status: "patched_file_changed", code: 8, sha256: current };
  }
  if (current !== ORIGINAL_SHA256) return { status: "plugin_patch_source_mismatch", code: 7, sha256: current };
  const patched = transform(source);
  const result = sha256(patched);
  if (result !== PATCHED_SHA256) return { status: "plugin_patch_result_mismatch", code: 8, sha256: result };
  write(path, patched);
  return { status: "plugin_patch_applied", code: 0 };
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  const outcome = apply(process.argv[2] || DEFAULT_TARGET);
  const line = outcome.sha256 ? `${outcome.status} ${outcome.sha256}` : outcome.status;
  (outcome.code === 0 ? console.log : console.error)(line);
  process.exit(outcome.code);
}
