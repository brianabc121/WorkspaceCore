# 回合中追加消息与图片输入的核验记录（T-0236，docs/v2/LLM_BRIDGE_QUEUE_PLAN.md）

> 规则（`docs/AGENTS.md` 第 8 节）：第三方的接口与行为必须在锁定版本上核验，不按名称猜；核验不了的写明，并留好退路。
> 2026-11-14 在本会话的环境里做。没有任何平台的账号，所以只能核对接口定义和命令行参数，**不能实测运行时行为**。

## 1. Codex 0.169.0（`@openai/codex@0.169.0`）

`codex app-server generate-json-schema` 生成的定义：

| 项 | 结果 |
|---|---|
| `turn/steer` | 存在。参数 `threadId`、`expectedTurnId`、`input[]` 必填，`clientUserMessageId` 可选；应答 `{turnId}` |
| `input` 的项类型 | `text{text}`、`image`、`localImage{path}`、`audio`、`localAudio`、`skill`、`mention` |
| 会话条目类型 | 有 `imageGeneration`、`imageView` |

没有核验：`turn/steer` 在回合进行中的实际效果、`expectedTurnId` 不匹配时的错误形式、`imageGeneration` 条目里图片的存放方式。

## 2. Claude Code（锁定 2.1.323；本机能看帮助的是 2.1.325）与 CodeBuddy Code 2.184.0

`--help` 的原文：

- `--input-format <format>`：只和 `--print` 一起用，`text`（默认）或 `stream-json`（realtime streaming input）。
- `--replay-user-messages`：把从标准输入读到的用户消息在标准输出上回显作为确认（要求输入输出都是 `stream-json`）。

两个程序的这两个参数说明一致。

没有核验：回合进行中写入一条 user message 时，是并进当前回合、还是排到当前回合之后；user message 里 image 内容块的接受情况。
本会话的环境里有一个已登录的 Claude Code，按约定不用它做测试。

## 3. 因此的实现约定

- 插入消息先尝试回合中追加，并要求得到确认（Codex：`turn/steer` 的应答；Claude / CodeBuddy：回显）。规定时间内没有确认，
  或平台返回错误，就降级成“中断当前回合，把新消息和‘接着刚才的做’合并成一个新回合”——消息不会丢。
- 官方程序把追加的消息当成“当前回合之后的下一个回合”处理时（进程在第一个 `result` 之后继续输出），管理器照实把它记成紧接着的一个回合。
- 真实账号验收时补上第 1、2 节“没有核验”的各项，结果写回本文件。

来源：<https://github.com/openai/codex/tree/main/codex-rs/app-server>、<https://code.claude.com/docs/en/cli-reference>。
