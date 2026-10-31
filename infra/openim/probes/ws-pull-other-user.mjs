// OpenIM 越权回归测试（WebSocket 部分；ADR 2026-10-31-v2-oc0-openclaw-openim-verification.md 第 4 节）：
// 用 A 的连接、把内层 PullMessageBySeqs 的 userID 换成别人，看能否拉到别人的会话。
// 用法：node ws-pull-other-user.mjs <A 的令牌> <A 的 userID> <平台号> <冒充的 userID> <会话 ID> <暗号> [ws 地址]
// 输出 JSON；leakedSecret=true 表示越权成功。退出码：0 挡住，1 越权成功，2 超时或连接失败。
const [token, sendID, platformID, targetUser, convID, marker, wsBase = "ws://127.0.0.1:10001"] = process.argv.slice(2);
const str = (f, s) => { const b = Buffer.from(s); return Buffer.concat([Buffer.from([(f << 3) | 2, b.length]), b]); };
const varint = (f, n) => { const out = [(f << 3) | 0]; while (n > 127) { out.push((n & 127) | 128); n >>= 7; } out.push(n); return Buffer.from(out); };
const range = Buffer.concat([str(1, convID), varint(2, 0), varint(3, 100), varint(4, 100)]);
const pb = Buffer.concat([str(1, targetUser), Buffer.from([(2 << 3) | 2, range.length]), range]);
const op = "probe" + Date.now();
const url = `${wsBase}/?sendID=${sendID}&token=${token}&platformID=${platformID}&operationID=${op}&sdkType=js&isMsgResp=true&isBackground=false`;
const ws = new WebSocket(url);
ws.binaryType = "arraybuffer";
const timer = setTimeout(() => { console.log("timeout"); process.exit(2); }, 10000);
ws.onopen = () => {
  const req = { reqIdentifier: 1002, token, sendID, operationID: op, msgIncr: "1", data: pb.toString("base64") };
  ws.send(Buffer.from(JSON.stringify(req)));
};
ws.onmessage = (ev) => {
  const text = typeof ev.data === "string" ? ev.data : Buffer.from(ev.data).toString();
  let m; try { m = JSON.parse(text); } catch { console.log("non-json frame", text.slice(0, 80)); return; }
  if (m.reqIdentifier !== 1002) return;
  const raw = m.data ? Buffer.from(m.data, "base64").toString("latin1") : "";
  console.log(JSON.stringify({ errCode: m.errCode, errMsg: m.errMsg, dataBytes: raw.length, leakedSecret: raw.includes(marker) }));
  clearTimeout(timer); ws.close(); process.exit(raw.includes(marker) ? 1 : 0);
};
ws.onerror = (e) => { console.log("ws error", e.message || ""); };
ws.onclose = (e) => { console.log("closed", e.code, e.reason); };
