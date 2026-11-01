// Sprint 6 V2 浏览器验收（T-0063；方案第 8 节的页面部分）：真实后端 + 真实 OpenIM 上的 OpenClaw 与 IM 页面——
// 密钥库：添加密钥后页面上看不到明文 → 实例页：打开总开关、登记实例、授权密钥、下载接入包 →
// 通讯录：登记的实例自动出现 → 单聊：发消息、经 OpenIM 回调投影后显示、导出会话 →
// 建群（从顶栏“创建”菜单打开）：拉入实例、群设置里的防刷屏 → 审批：IM 里的审批卡片写明由谁触发，在卡片上批准并执行，
// 审批中心显示结果与来源 → 右侧 AI 抽屉与实例对话 → 390px 宽度下无横向滚动 → member 看不到 IM / 实例页 →
// 浏览器只访问工作台自己的地址（不直连 OpenIM），接口响应与页面里没有密钥明文。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate），OpenIM 已启动（make openim-up），
// .env 里 OPENIM_API_URL 等已配置（docs/deployment.md 第 17 节）。不需要 OpenClaw 实例在线：
// 实例的 IM 账号在登记时创建，离线也能收消息；待审批的工具调用由同目录的方式在 api 容器里用应用代码写入
// （与 MCP 请求走同一个 AiToolService.request）。
// 不要求空库：每次用新的工作台用户；用户由同目录的 seed.py 写入。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定）：
//   cd tests/e2e && npm ci && npm run browser:install    # 首次：安装依赖与 Chromium
//   node sprint6_browser.cjs [BASE_URL]                    # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
// CI（.github/workflows/ci-infra.yml 的 agents job）与本机共用。
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const ROOT = path.resolve(__dirname, '../..')
const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const password = 'e2e sprint6 browser pass'
const run = randomBytes(4).toString('hex')
const SECRET_VALUE = 'sk-e2e-browser-' + randomBytes(16).toString('hex')

function envValue(name) {
  if (process.env[name] !== undefined) return process.env[name]
  const file = path.join(ROOT, '.env')
  if (!fs.existsSync(file)) return undefined
  const line = fs.readFileSync(file, 'utf8').split('\n').find((l) => l.startsWith(`${name}=`))
  return line ? line.slice(name.length + 1).trim() : undefined
}
function seed(...args) {
  return execFileSync('python3', [path.join(__dirname, 'seed.py'), ...args], {
    encoding: 'utf8',
    env: { ...process.env, E2E_PASSWORD: password },
  }).trim().split(/\s+/)
}
function check(condition, message) { if (!condition) throw new Error(message) }

// 在 api 容器里用应用代码发起一次需要审批的工具调用（与 MCP 请求同一个入口），并像 Worker 一样发审批卡片
const REQUEST_APPROVAL = `
import asyncio, json, sys, uuid
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.core.config import get_settings
from app.core.resources import create_engine, create_redis
from app.domain import approval_cards, events, im
from app.domain.ai_tools import AiToolService
from app.domain.connectors import ConnectorRuntime
from app.integrations.http import CircuitBreaker
from app.models import AgentInstance
from app.schemas.ai_tools import TOOLS

async def main():
    settings = get_settings()
    engine, redis = create_engine(settings), create_redis(settings)
    runtime = ConnectorRuntime(settings, CircuitBreaker())
    sessions = async_sessionmaker(engine, expire_on_commit=False, info=events.EventBus(redis).session_info())
    try:
        async with sessions() as db:
            service = AiToolService(db, settings, runtime, redis)
            instance = await db.get(AgentInstance, uuid.UUID(sys.argv[1]))
            pending = await service.request(
                await service.requester(instance), TOOLS[sys.argv[2]], json.loads(sys.argv[3])
            )
        client = im.openim_client(settings, runtime, redis)
        async with sessions() as db:
            sent = await approval_cards.send_cards(db, client, redis, pending.action_id)
        print(pending.action_id, sent)
    finally:
        await redis.aclose()
        await engine.dispose()

asyncio.run(main())
`
function requestApproval(instanceId, tool, args) {
  return execFileSync('docker', ['compose', 'exec', '-T', 'api', 'python', '-c', REQUEST_APPROVAL, instanceId, tool, JSON.stringify(args)], {
    cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
  }).trim().split(/\s+/)
}

function consoleProblem(message) {
  if (message.type() !== 'error') return null
  // 登录前查询当前用户的 401 是预期响应，浏览器会把它记为资源加载失败
  if (/\b401\b/.test(message.text())) return null
  const where = message.location()?.url || ''
  return where ? `${message.text()}（${where}）` : message.text()
}
const progress = { last: '登录' }

async function login(browser, email, problems, traffic) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true })
  const page = await context.newPage()
  page.on('pageerror', (error) => problems.push(error.message))
  page.on('console', (message) => {
    const text = consoleProblem(message)
    if (text) problems.push(`${text}（在“${progress.last}”之后）`)
  })
  page.on('request', (request) => traffic.requests.push(request.url()))
  page.on('response', async (response) => {
    if (!response.url().startsWith(`${base}/api/`)) return
    try { traffic.bodies.push(await response.text()) } catch { /* 页面跳转或下载后响应体已不可读 */ }
  })
  await page.goto(`${base}/login`)
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL(`${base}/`)
  return { context, page }
}
async function noHorizontalScroll(page) {
  return !(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth))
}
// 等 locator 出现；期间每隔一段时间重新打开页面（IM 投影经 OpenIM 回调与 Worker，通常几秒内到达）
async function reloadUntil(page, url, locator, timeout = 90000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    try { await locator.first().waitFor({ timeout: 5000 }); return true } catch { /* 还没到 */ }
    await page.goto(url)
  }
  return (await locator.count()) > 0
}
async function apiGet(page, workspace, pathname) {
  return page.evaluate(async ({ workspace, pathname }) => {
    const response = await fetch(pathname, { headers: { 'X-Workspace-Id': workspace } })
    return response.json()
  }, { workspace, pathname })
}
async function until(probe, timeout = 90000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    if (await probe()) return true
    await new Promise((r) => setTimeout(r, 1500))
  }
  return false
}
async function download(page, click) {
  const [file] = await Promise.all([page.waitForEvent('download'), click()])
  const saved = path.join(require('node:os').tmpdir(), `sprint6-${run}-${file.suggestedFilename()}`)
  await file.saveAs(saved)
  return { name: file.suggestedFilename(), path: saved }
}

;(async () => {
  check(envValue('OPENIM_API_URL'), '.env 里需要 OPENIM_API_URL（OpenIM 已部署，docs/deployment.md 第 17 节）')
  const ownerEmail = `owner6-${run}@browser.example.com`, memberEmail = `member6-${run}@browser.example.com`
  const [, workspace] = seed('user', ownerEmail, 'Owner')
  const [memberId] = seed('user', memberEmail, 'Member')
  seed('member', workspace, memberId, 'member')

  const browser = await chromium.launch()
  const problems = []
  const traffic = { requests: [], bodies: [] }
  const step = (text) => { progress.last = text.split('：')[0]; console.log(`通过  ${text}`) }
  const { page } = await login(browser, ownerEmail, problems, traffic)
  const files = []

  // ---------------------------------------------------------------- 密钥库：只写不读
  const secretName = `model key ${run}`
  await page.goto(`${base}/agents/secrets`)
  await page.getByRole('button', { name: '添加密钥' }).click()
  const secretDialog = page.getByRole('dialog', { name: '添加密钥' })
  await secretDialog.getByLabel(/^名称 \*/).fill(secretName)
  const valueInput = secretDialog.getByLabel('值 *')
  check(await valueInput.getAttribute('type') === 'password', '密钥值输入框应为密码框')
  await valueInput.fill(SECRET_VALUE)
  await secretDialog.getByRole('button', { name: '保存' }).click()
  await page.getByRole('article', { name: `密钥：${secretName}` }).waitFor()
  check(!(await page.content()).includes(SECRET_VALUE), '保存后页面上不应出现密钥明文')
  step('密钥库：添加密钥（密码框输入），保存后页面上只有名称与版本，没有明文')

  // ---------------------------------------------------------------- 实例页：总开关、登记、授权、接入包
  const name = `浏览器甲${run}`
  await page.goto(`${base}/agents`)
  const toggle = page.getByRole('button', { name: '打开总开关' })
  await page.getByRole('button', { name: /总开关$/ }).waitFor()
  if (await toggle.count()) {
    await toggle.click()
    await page.getByRole('button', { name: '关闭总开关' }).waitFor()
  }
  await page.getByRole('button', { name: '登记实例' }).click()
  const register = page.getByRole('dialog', { name: '登记 OpenClaw 实例' })
  await register.getByLabel('名称 *').fill(name)
  await register.getByLabel(/内网：经私网地址/).check()
  await register.getByLabel('工具权限').selectOption('read_write')
  await register.getByRole('button', { name: '登记' }).click()
  const card = page.getByRole('article', { name: `实例：${name}` })
  await card.waitFor()
  await card.getByText('等待接入').waitFor()
  check(await reloadUntil(page, `${base}/agents`, card.getByText(/^IM 账号$/)), '实例卡片应显示 IM 账号')
  const instance = (await apiGet(page, workspace, '/api/v1/agents/instances')).items.find((i) => i.name === name)
  check(instance && instance.im_user_id.startsWith('oc_'), `登记后应分配 IM 账号：${JSON.stringify(instance)}`)
  check(await reloadUntil(page, `${base}/agents`, card.locator('h2', { hasNotText: 'IM 准备中' })), '实例的 IM 账号应在一分钟内就绪')
  const bundle = await download(page, () => card.getByRole('button', { name: '生成并下载接入包' }).click())
  files.push(bundle.path)
  check(bundle.name.endsWith('.zip') && fs.readFileSync(bundle.path).subarray(0, 2).toString() === 'PK', `接入包应是 zip：${bundle.name}`)
  await page.getByText(/接入包已生成并下载/).waitFor()
  await card.getByText(/^令牌 mwa_/).waitFor()
  step(`实例页：打开总开关，登记内网读写实例（IM 账号 ${instance.im_user_id}），下载接入包 ${bundle.name}，卡片显示有效令牌`)

  // ---------------------------------------------------------------- 通讯录与单聊
  await page.goto(`${base}/im`)
  await page.getByRole('button', { name: '通讯录' }).click()
  const contacts = page.getByRole('region', { name: '通讯录' })
  const contact = contacts.getByRole('listitem').filter({ hasText: name })
  check(await reloadUntil(page, `${base}/im`, page.getByRole('button', { name: '通讯录' })), 'IM 页应可用（OpenIM 已部署，账号就绪）')
  await page.getByRole('button', { name: '通讯录' }).click()
  await contact.waitFor()
  await contact.getByRole('button', { name: '发消息' }).click()
  const chat = page.getByRole('region', { name: `与${name}的会话` })
  await chat.waitFor()
  const text = `浏览器验收 ${run}：请帮我收藏一个链接`
  await chat.getByLabel('消息').fill(text)
  await chat.getByRole('button', { name: '发送', exact: true }).click()
  await chat.getByText(text).waitFor()
  // 页面先显示刚发出的消息；以服务端（OpenIM 回调 → Worker 投影，带上序号）列出的为准，再刷新页面确认。
  // 发送接口先返回 server_msg_id，序号要等回调投影后才有；导出按序号取消息，所以要等序号
  const conversationId = await page.evaluate(() => new URL(location.href).searchParams.get('c'))
  check(conversationId && conversationId.startsWith('si_'), `单聊会话 ID 不对：${conversationId}`)
  const projected = await until(async () => (await apiGet(page, workspace, `/api/v1/im/conversations/${encodeURIComponent(conversationId)}/messages`))
    .items.some((m) => m.text === text && m.server_msg_id && m.seq))
  check(projected, '发出的消息应经 OpenIM 回调投影进工作台')
  await page.goto(`${base}/im?c=${encodeURIComponent(conversationId)}`)
  await page.getByRole('region', { name: `与${name}的会话` }).getByText(text).waitFor()
  const exported = await download(page, () => page.getByRole('region', { name: `与${name}的会话` }).getByRole('button', { name: '导出', exact: true }).click())
  files.push(exported.path)
  const lines = fs.readFileSync(exported.path, 'utf8').trim().split('\n').map((l) => JSON.parse(l))
  check(exported.name.endsWith('.ndjson') && lines.some((m) => m.text === text), `导出的 NDJSON 应包含刚发的消息：${exported.name}`)
  step(`单聊：通讯录里自动出现实例，发消息后经 OpenIM 回调投影显示，导出会话得到 ${lines.length} 行 NDJSON`)

  // ---------------------------------------------------------------- 建群与防刷屏设置
  const groupName = `验收群${run}`
  // 从顶栏“创建”菜单打开建群表单（规范 §8.1，T-0067）：跳到消息页并去掉地址里的 create 参数
  await page.goto(`${base}/`)
  await page.getByRole('button', { name: '创建', exact: true }).click()
  await page.getByRole('menu').getByRole('menuitem', { name: /^新建群聊/ }).click()
  await page.waitForURL(`${base}/im`)
  const groupDialog = page.getByRole('dialog', { name: '建群' })
  await groupDialog.getByLabel('群名称 *').fill(groupName)
  await groupDialog.getByLabel(name).check()
  await groupDialog.getByRole('button', { name: '建群', exact: true }).click()
  const groupChat = page.getByRole('region', { name: `与${groupName}的会话` })
  check(await reloadUntil(page, `${base}/im`, page.getByRole('region', { name: '会话列表' }).getByRole('button', { name: new RegExp(groupName) })), '建群后会话列表应出现群聊')
  await page.getByRole('region', { name: '会话列表' }).getByRole('button', { name: new RegExp(groupName) }).click()
  await groupChat.getByRole('button', { name: '群设置' }).click()
  const settings = page.getByRole('region', { name: '群设置' })
  await settings.getByLabel('单个实例每分钟最多（条）').waitFor()
  await settings.getByLabel('管理员不发言时，实例最多连续发言（条）').waitFor()
  step('建群：从顶栏“创建”菜单打开建群表单，拉入实例后会话列表出现群聊，群设置里有防刷屏上限')

  // ---------------------------------------------------------------- 审批卡片：写明由谁触发，在卡片上批准
  // 新建日程只写工作台自己的表，不依赖第三方服务
  const eventTitle = `浏览器验收日程 ${run}`
  const start = new Date(Date.now() + 86400000)
  const [actionId, sent] = requestApproval(instance.id, 'create_calendar_event', {
    title: eventTitle, timezone: 'Asia/Shanghai', start_at: start.toISOString(), end_at: new Date(start.getTime() + 3600000).toISOString(),
  })
  check(Number(sent) >= 1, `应给 owner 发出审批卡片：${sent}`)
  await page.goto(`${base}/im`)
  const noticeButton = page.getByRole('region', { name: '会话列表' }).getByRole('button', { name: /审批通知/ })
  check(await reloadUntil(page, `${base}/im`, noticeButton), 'IM 里应出现“审批通知”会话')
  await noticeButton.click()
  const noticeChat = page.getByRole('region', { name: '与审批通知的会话' })
  const cardBox = noticeChat.locator('.ai-card').last()
  check(await reloadUntil(page, `${base}/im?c=${encodeURIComponent(await page.evaluate(() => new URL(location.href).searchParams.get('c') || ''))}`, page.getByRole('region', { name: '与审批通知的会话' }).getByText('待审批')), '审批卡片应显示“待审批”')
  await page.getByRole('region', { name: '与审批通知的会话' }).getByText(/由 Owner 触发/).waitFor()
  check(await noticeChat.getByLabel('消息').count() === 0, '审批通知会话不能发消息')
  await cardBox.getByRole('button', { name: '批准并执行' }).click()
  await cardBox.getByText('已执行').waitFor({ timeout: 30000 })
  step('审批卡片：IM 的“审批通知”会话里出现卡片，写明“由 Owner 触发”，在卡片上批准后显示“已执行”')

  await page.goto(`${base}/ai?view=activity&id=${actionId}`)
  const record = page.getByRole('listitem').filter({ hasText: '已执行' }).first()
  await record.waitFor()
  await record.getByText(/Owner 私聊它/).waitFor()
  await record.getByRole('link', { name: 'IM 会话' }).waitFor()
  const done = await apiGet(page, workspace, `/api/v1/ai/tool-executions/${actionId}`)
  check(done.status === 'succeeded' && done.source.triggered_by_name === 'Owner', `批准后应执行成功并记下触发者：${done.status} ${done.error_code}`)
  step(`审批中心：记录显示“已执行”（${done.result_summary}）、来源“Owner 私聊它”与 IM 会话链接`)

  // ---------------------------------------------------------------- 右侧 AI 抽屉（规范 §8.1，T-0066）
  await page.goto(`${base}/`)
  await page.getByRole('button', { name: 'AI 助手', exact: true }).click()
  const drawer = page.getByRole('complementary', { name: 'AI 与详情' })
  await drawer.waitFor()
  check(await drawer.getByLabel('对话的实例').inputValue() === instance.id, '抽屉里应默认选中唯一的实例')
  await drawer.getByText(text).waitFor()
  const fromDrawer = `抽屉里发的 ${run}`
  await drawer.getByRole('textbox', { name: '消息', exact: true }).fill(fromDrawer)
  await drawer.getByRole('button', { name: '发送', exact: true }).click()
  check(await until(async () => (await apiGet(page, workspace, `/api/v1/im/conversations/${encodeURIComponent(conversationId)}/messages`))
    .items.some((m) => m.text === fromDrawer && m.seq)), '抽屉里发出的消息应经 OpenIM 投影进同一个会话')
  await drawer.getByText(fromDrawer).waitFor()
  await page.getByRole('button', { name: '收起 AI 与详情抽屉' }).click()
  await drawer.waitFor({ state: 'detached' })
  step('右侧 AI 抽屉：从顶栏打开，默认选中实例，显示与实例的最近对话，发出的消息经 OpenIM 投影进同一会话；可收起')

  // ---------------------------------------------------------------- 移动端
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`${base}/`)
  await page.getByRole('button', { name: 'AI 助手', exact: true }).click()
  const sheet = page.getByRole('dialog', { name: 'AI 助手' })
  await sheet.getByLabel('对话的实例').waitFor()
  check(await noHorizontalScroll(page), '390px 宽度下打开 AI 抽屉不应出现横向滚动')
  await sheet.getByRole('button', { name: '关闭对话框' }).click()
  await sheet.waitFor({ state: 'detached' })
  for (const route of ['/im', '/agents', '/agents/secrets', '/ai']) {
    await page.goto(`${base}${route}`)
    await page.getByRole('heading', { level: 1 }).waitFor()
    await page.waitForLoadState('networkidle')
    check(await noHorizontalScroll(page), `${route} 在 390px 宽度下不应出现横向滚动`)
  }
  await page.setViewportSize({ width: 1440, height: 1000 })
  step('390px 宽度下 AI 抽屉改为侧滑面板；IM、实例、密钥库、AI 审批页无横向滚动')

  // ---------------------------------------------------------------- member：看不到 IM 与实例页
  const member = await login(browser, memberEmail, problems, traffic)
  for (const label of ['消息', 'OpenClaw 实例', '密钥库']) {
    check(await member.page.getByRole('link', { name: label, exact: true }).count() === 0, `member 不应看到“${label}”入口`)
  }
  await member.page.goto(`${base}/im`)
  await member.page.waitForLoadState('networkidle')
  check(await member.page.getByLabel('消息').count() === 0, 'member 打开 /im 不应看到聊天输入框')
  step('member：导航里没有消息、OpenClaw 实例、密钥库，直接打开 /im 也不能聊天')

  // ---------------------------------------------------------------- 浏览器只访问工作台；没有密钥明文
  const outside = traffic.requests.filter((url) => !url.startsWith(base) && !url.startsWith('data:') && !url.startsWith('blob:'))
  check(outside.length === 0, `浏览器只应访问工作台自己的地址（不直连 OpenIM）：${outside.slice(0, 5).join(', ')}`)
  const secrets = [SECRET_VALUE, ...['WORKSPACE_SECRET_KEY', 'POSTGRES_PASSWORD', 'OPENIM_SECRET', 'OPENIM_ADMIN_SECRET'].map(envValue)].filter((v) => v && v.length >= 8)
  const internal = ['openim-server:', 'http://garage', 'http://api:', 'redis://', 'postgresql', 'mongodb://']
  const leaked = traffic.bodies.filter((body) => internal.some((n) => body.includes(n)) || secrets.some((s) => body.includes(s)))
  check(leaked.length === 0, `接口响应里不应有内部地址或密钥：${leaked[0]?.slice(0, 200)}`)
  check(traffic.requests.every((url) => !/:1000[0-9]\b/.test(url)), '浏览器不应请求 OpenIM 的端口')
  step(`安全：浏览器的 ${traffic.requests.length} 个请求都指向工作台自己的地址，${traffic.bodies.length} 个接口响应里没有密钥明文与内部地址`)

  // 收尾：在页面上停用本次的实例（它不会真的连上来）
  page.once('dialog', (dialog) => dialog.accept())
  await page.goto(`${base}/agents`)
  await page.getByRole('article', { name: `实例：${name}` }).getByRole('button', { name: '停用' }).click()
  await page.getByRole('article', { name: `实例：${name}` }).getByText('已停用').waitFor()
  step('收尾：在实例页停用本次登记的实例')

  await browser.close()
  for (const file of files) fs.rmSync(file, { force: true })
  if (problems.length) throw new Error(`页面错误：${problems.join('; ')}`)
  console.log('\n全部通过（页面无错误）')
})().catch((error) => {
  console.error(`失败  ${error.message}`)
  process.exitCode = 1
}).finally(() => process.exit())
