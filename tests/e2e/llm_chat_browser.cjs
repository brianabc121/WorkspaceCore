// 大模型聚合聊天浏览器验收（T-0231、T-0232，docs/v2/LLM_CHAT_PLAN.md）：
// 真实后端 + 真实前端 + 一个验收用的 MCP 管理器——它里面的 Claude 和 WorkBuddy 程序换成了不访问外网的假程序
// （services/llm-bridge/tests/fakes/fake_cli.py：参数和事件的形状与真实程序一致，回答是把收到的话原样带回来；
// 收到 hang 时会一直不回答，用来验收“停止”）。别的都是真的：会话桥、工作台接口、页面。
//
// 没有管理器时的说明 → 新建会话并发第一条 → 回答、过程、标题 → Enter 再发一条 → 停止 → 消息队列（排队、置顶、修改、取消）→ 改标题 → 搜索 →
// 刷新后还在 → 别的管理员建的会话要确认一次才能接着发 → member 用不了 → 归档后不能再发 →
// 管理器停用后只能看 → 多模型同问（同一个问题同时发给两个模型、并排看、继续问、回到单会话、别的管理员要确认、归档）→
// 审计里没有消息内容 → 手机宽度两屏（同问一次看一栏）。
//
// 不验证的：各平台的真实登录和真实回答（需要账号）；智能体建的会话（compose job 没有接入入口，
// 由 services/api/tests/test_module_llm_bridge_chat.py 和前端测试覆盖）。
//
// 前提：Compose 全栈已启动并迁移；验收用的管理器已启动（CI：叠加 tests/e2e/llm-bridge-fake.compose.yaml）。
// 用法：LLM_BRIDGE_FAKE_TOKEN=<它的管理令牌> [LLM_BRIDGE_FAKE_URL=http://llm-bridge-fake:8787] node llm_chat_browser.cjs [BASE_URL]
// 不要求空库：每次新建工作台用户。结束时（成败都）删掉添加的管理器、关掉模块。
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const path = require('node:path')
const { chromium } = require('playwright')

const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const bridgeUrl = process.env.LLM_BRIDGE_FAKE_URL || 'http://llm-bridge-fake:8787'
const bridgeToken = process.env.LLM_BRIDGE_FAKE_TOKEN || ''
const password = 'e2e llm chat browser pass'
const run = randomBytes(4).toString('hex')
const ownerEmail = `lc-owner-${run}@browser.example.com`
const adminEmail = `lc-admin-${run}@browser.example.com`
const memberEmail = `lc-member-${run}@browser.example.com`
const ADMIN_NAME = `聊天管理员 ${run}`
const MANAGER = `验收用 ${run}`
const FIRST = `你好，验收 ${run}`
const TITLE = `验收会话 ${run}`
const QUESTION = `同问 ${run}`
// 假的 API 密钥：假程序只看“有没有”，不会发给任何平台
const FAKE_KEY = `e2e-not-a-real-key-${randomBytes(12).toString('hex')}`

async function until(test, timeout = 30000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    if (await test()) return true
    await new Promise((resolve) => setTimeout(resolve, 300))
  }
  return false
}

function seed(...args) {
  return execFileSync('python3', [path.join(__dirname, 'seed.py'), ...args], {
    encoding: 'utf8',
    env: { ...process.env, E2E_PASSWORD: password },
  }).trim().split(/\s+/)
}
function check(condition, message) { if (!condition) throw new Error(message) }
const step = (text) => console.log(`通过  ${text}`)

async function api(page, workspace, method, pathname, body) {
  return page.evaluate(async ({ method, pathname, body, workspace }) => {
    const csrf = document.cookie.split('; ').find((item) => item.startsWith('ws_csrf='))?.slice(8)
    const headers = { 'X-Workspace-Id': workspace, ...(csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {}) }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    const response = await fetch(pathname, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
    const text = await response.text()
    let json = null
    try { json = text ? JSON.parse(text) : null } catch { /* 不是 JSON */ }
    return { status: response.status, body: json }
  }, { method, pathname, body, workspace })
}

function watch(page, problems, who) {
  page.on('pageerror', (error) => problems.push(`${who} 页面错误：${error.message}`))
  page.on('response', (response) => {
    if (response.status() >= 500) problems.push(`${who} ${response.status()} ${response.request().method()} ${new URL(response.url()).pathname}`)
  })
  page.on('console', (message) => {
    // 4xx 在浏览器里会记成“资源加载失败”；这些响应由下面的断言逐个检查
    if (message.type() === 'error' && !/Failed to load resource/i.test(message.text())) problems.push(`${who} 控制台错误：${message.text()}`)
  })
}
async function login(browser, email, problems, who, viewport = { width: 1440, height: 1000 }) {
  const context = await browser.newContext({ viewport, locale: 'zh-CN' })
  const page = await context.newPage()
  watch(page, problems, who)
  await page.goto(`${base}/login`)
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL(`${base}/`)
  return { context, page }
}
// seed.py 给每个用户都建了自己的空间：别的用户打开页面后要切到 owner 的工作空间
async function openAs(page, workspace, target) {
  await page.goto(`${base}${target}`)
  const select = page.getByLabel('当前工作空间')
  await select.waitFor()
  if ((await select.inputValue()) !== workspace) await select.selectOption(workspace)
}
async function noHorizontalScroll(page) {
  return !(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth))
}
const side = (page) => page.getByRole('complementary', { name: '会话列表' })
const thread = (page) => page.getByRole('list', { name: '消息' })
const composer = (page) => page.getByRole('textbox', { name: '消息内容' })
// 消息列表里直接的那几条（回答里“过程”的每一步也是列表项，不算）
const countMessages = (page) => thread(page).locator(':scope > li').count()
const requestId = () => `e2e-${randomBytes(8).toString('hex')}`

async function main() {
  check(bridgeToken.length >= 32, '请设置 LLM_BRIDGE_FAKE_TOKEN（验收用 MCP 管理器的管理令牌）')
  const [ownerId, workspace] = seed('user', ownerEmail, '大模型聊天验收')
  const [adminId] = seed('user', adminEmail, ADMIN_NAME)
  const [memberId] = seed('user', memberEmail, '大模型聊天成员')
  seed('member', workspace, adminId, 'admin')
  seed('member', workspace, memberId, 'member')
  check(ownerId && workspace, '没有建出测试用户')

  const browser = await chromium.launch()
  const problems = []
  let ownerPage = null
  try {
    const { page } = await login(browser, ownerEmail, problems, 'owner')
    ownerPage = page
    const enable = await api(page, workspace, 'PATCH', '/api/v1/modules/llm_bridge', { enabled: true })
    check(enable.status === 200, `没能启用模块：${JSON.stringify(enable)}`)

    // ---------------------------------------------------------------- 没有管理器时
    await page.goto(`${base}/llm_bridge`)
    await page.getByRole('heading', { name: '大模型聊天', level: 1 }).waitFor()
    await page.getByText('还没有 MCP 管理器。').waitFor()
    await page.getByRole('link', { name: '去添加 MCP 管理器' }).click()
    await page.getByRole('heading', { name: '大模型客户端管理', level: 1 }).waitFor()
    check(new URL(page.url()).searchParams.get('view') === 'manage', '“去添加”应打开管理视图')
    step('默认打开聊天；没有管理器时给出说明，并能去“管理器与账号”添加')

    // ---------------------------------------------------------------- 添加验收用的管理器，Claude 用（假的）API 密钥登录
    const created = await api(page, workspace, 'POST', '/api/v1/llm_bridge/bridges', { name: MANAGER, base_url: bridgeUrl, token: bridgeToken, platforms: ['claude', 'workbuddy'] })
    check(created.status === 201, `没能添加验收用的管理器：${JSON.stringify(created)}`)
    const bridgeId = created.body.id
    const chat = `/api/v1/llm_bridge/bridges/${bridgeId}/chat`
    const signedIn = await api(page, workspace, 'POST', `/api/v1/llm_bridge/bridges/${bridgeId}/accounts/claude/login`, { method: 'api_key', api_key: FAKE_KEY })
    check(signedIn.status === 200, `Claude 登录失败：${JSON.stringify(signedIn)}`)
    const state = await api(page, workspace, 'GET', `/api/v1/llm_bridge/bridges/${bridgeId}/status`)
    check(state.body?.accounts?.find((a) => a.slot === 'claude')?.state === 'logged_in', `Claude 应显示已登录：${JSON.stringify(state.body?.accounts)}`)

    await page.getByRole('navigation', { name: '大模型客户端的视图' }).getByRole('link', { name: '聊天', exact: true }).click()
    await side(page).waitFor()
    check((await side(page).getByLabel('MCP 管理器').locator('option:checked').innerText()) === MANAGER, '左栏应选中验收用的管理器')
    const filters = side(page).getByRole('group', { name: '按平台筛选' })
    await filters.getByRole('button', { name: 'Claude', exact: true }).waitFor()
    check(/未登录/.test(await filters.getByRole('button', { name: /WorkBuddy/ }).innerText()), '没登录的平台要在筛选里标出来')
    check((await filters.getByRole('button').count()) === 3, '筛选里只列这个管理器控制的平台（全部 / Claude / WorkBuddy）')
    step('添加管理器并登录后，聊天页列出它控制的平台，没登录的标出来')

    // ---------------------------------------------------------------- 新建会话并发第一条
    await side(page).getByRole('button', { name: '新建会话' }).click()
    const form = page.getByRole('form', { name: '新建会话' })
    const platforms = form.getByRole('group', { name: '平台' })
    await platforms.getByRole('radio', { name: /Claude/ }).waitFor()
    check(await platforms.getByRole('radio', { name: /Claude/ }).isChecked(), '默认选中已登录的平台')
    check(await platforms.getByRole('radio', { name: /WorkBuddy/ }).isDisabled(), '没登录的平台不能选')
    check(await form.getByRole('group', { name: '用法' }).getByRole('radio', { name: /对话/ }).isChecked(), '默认用法是对话')
    await form.getByLabel('第一条消息').fill(FIRST)
    await form.getByRole('button', { name: '新建并发送' }).click()
    await page.getByRole('heading', { level: 2, name: FIRST }).waitFor({ timeout: 60000 })
    const firstId = new URL(page.url()).searchParams.get('s')
    check(/^bs_[0-9a-f]{20}$/.test(firstId || ''), `地址里应带上新会话：${page.url()}`)
    await thread(page).getByText(`:: ${FIRST}`).waitFor({ timeout: 60000 })
    check((await countMessages(page)) === 2, '应有一问一答两条消息')
    const reply = thread(page).locator(':scope > li').nth(1)
    check((await reply.locator('.lc-msg-head strong').innerText()) === 'Claude', '回答上面写的是平台名')
    await reply.getByText('过程（2 步）').waitFor()
    // 会话桥不把自己的管理令牌交给官方程序：假程序把看到的环境变量带在回答里
    check(/leak=None/.test(await reply.innerText()), '官方程序不应拿到会话桥的管理令牌')
    await side(page).getByRole('button', { name: new RegExp(FIRST) }).waitFor()
    step('新建会话并发第一条：回答出现，带过程；标题取自第一条消息；地址里带着会话')

    // ---------------------------------------------------------------- Enter 再发一条
    await composer(page).fill('第二问')
    await composer(page).press('Shift+Enter')
    check((await countMessages(page)) === 2, 'Shift + Enter 是换行，不发送')
    await composer(page).fill('第二问')
    await composer(page).press('Enter')
    await thread(page).getByText(':: 第二问').waitFor({ timeout: 60000 })
    check((await countMessages(page)) === 4, '第二条发出后应有四条消息')
    check((await composer(page).inputValue()) === '', '发出后输入框清空')
    step('Enter 发送、Shift + Enter 换行；第二条的回答出现')

    // ---------------------------------------------------------------- 停止
    await composer(page).fill('hang')
    await composer(page).press('Enter')
    const stop = page.getByRole('button', { name: '停止', exact: true })
    await stop.waitFor({ timeout: 30000 })
    await thread(page).getByText('正在回答…').waitFor({ timeout: 30000 })
    await stop.click()
    await thread(page).getByText('已停止').waitFor({ timeout: 60000 })
    await stop.waitFor({ state: 'detached' })
    step('回答到一半可以停止，这一回合显示“已停止”')

    // ---------------------------------------------------------------- 消息队列（T-0236）
    await composer(page).fill('hang')
    await composer(page).press('Enter')
    await thread(page).getByText('正在回答…').waitFor({ timeout: 30000 })
    for (const text of ['排队甲', '排队乙']) {
      await composer(page).fill(text)
      await composer(page).press('Enter')
      await page.waitForFunction(() => document.querySelector('textarea[aria-label="消息内容"]')?.value === '', null, { timeout: 30000 })
    }
    const queue = page.getByRole('region', { name: '消息队列' })
    await queue.getByRole('button', { name: /队列里有 2 条消息/ }).waitFor({ timeout: 30000 })
    const queued = () => queue.getByRole('listitem').allTextContents()
    check((await queued()).map((t) => (t.includes('排队甲') ? '甲' : '乙')).join('') === '甲乙', `队列应按发送顺序：${await queued()}`)
    check((await thread(page).getByText(':: 排队甲').count()) === 0, '前面的还没做完：排队的消息不应已经有回答（没有打断正在跑的）')
    await queue.getByRole('button', { name: '把第 2 条调到最前面' }).click()
    check(await until(async () => (await queued())[0].includes('排队乙')), `调到最前面后第一条应是“排队乙”：${await queued()}`)
    await queue.getByRole('button', { name: '修改第 1 条' }).click()
    const edit = queue.getByRole('form', { name: '修改排队中的第 1 条' })
    await edit.getByRole('textbox', { name: '消息内容' }).fill('排队乙（改过）')
    await edit.getByRole('button', { name: '保存' }).click()
    check(await until(async () => (await queued())[0].includes('排队乙（改过）')), '修改后队列里应是新的内容')
    await queue.getByRole('button', { name: '取消第 2 条' }).click()
    check(await until(async () => (await queued()).length === 1), '取消后队列里只剩一条')
    // 停掉正在跑的那一条：队列里剩下的那条接着被处理
    await page.getByRole('button', { name: '停止', exact: true }).click()
    await thread(page).getByText(':: 排队乙（改过）').waitFor({ timeout: 60000 })
    check((await thread(page).getByText(':: 排队甲').count()) === 0, '取消的那条不应被处理')
    check(await until(async () => (await queue.count()) === 0), '队列空了之后面板应消失')
    step('消息队列：会话在跑时再发的消息进队列、不打断；能调到最前面、修改、取消；前面的结束后按顺序处理')

    // ---------------------------------------------------------------- 改标题、搜索、刷新
    await page.getByRole('button', { name: '改标题' }).click()
    const rename = page.getByRole('form', { name: '改会话标题' })
    await rename.getByRole('textbox', { name: '会话标题' }).fill(TITLE)
    await rename.getByRole('button', { name: '保存' }).click()
    await page.getByRole('heading', { level: 2, name: TITLE }).waitFor()
    await side(page).getByRole('button', { name: new RegExp(TITLE) }).waitFor()
    const search = side(page).getByRole('searchbox', { name: '按标题搜索会话' })
    await search.fill(`找不到的标题 ${run}`)
    await side(page).getByText('没有标题里带这几个字的会话。').waitFor()
    await search.fill(run)
    await side(page).getByRole('button', { name: new RegExp(TITLE) }).waitFor()
    await search.fill('')
    await page.reload()
    await page.getByRole('heading', { level: 2, name: TITLE }).waitFor()
    await thread(page).getByText(':: 第二问').waitFor()
    // 三问三答，加上队列那一步的：停掉的一条、取消的一条、处理完的一条
    check((await countMessages(page)) === 12, `刷新后六问六答都还在：${await countMessages(page)}`)
    const info = page.getByRole('complementary', { name: '会话信息' })
    await info.getByText('回合（6）').waitFor()
    await info.getByText(MANAGER).waitFor()
    step('改标题后列表跟着变；按标题搜索；刷新后从地址回到同一个会话，右栏有会话信息和回合')

    // ---------------------------------------------------------------- 别的管理员建的会话：接着发要确认一次
    const admin = await login(browser, adminEmail, problems, 'admin')
    await openAs(admin.page, workspace, '/llm_bridge')
    await side(admin.page).waitFor()
    const adminText = `管理员的问题 ${run}`
    const theirs = await api(admin.page, workspace, 'POST', `${chat}/sessions`, { platform: 'claude', mode: 'chat', text: adminText, request_id: requestId() })
    check(theirs.status === 201 && theirs.body.session.owner_kind === 'me', `admin 应能新建会话：${JSON.stringify(theirs)}`)
    const theirId = theirs.body.session.session_id
    // admin 在页面上也能聊：打开自己的会话，输入框可用
    await side(admin.page).getByRole('button', { name: new RegExp(adminText) }).click()
    await thread(admin.page).getByText(`:: ${adminText}`).waitFor({ timeout: 60000 })
    await composer(admin.page).waitFor()
    await admin.context.close()

    const refused = await api(page, workspace, 'POST', `${chat}/sessions/${theirId}/messages`, { text: '不确认就发', request_id: requestId() })
    check(refused.status === 409 && refused.body?.error?.code === 'TAKEOVER_REQUIRED', `不确认就往别人的会话里发应被拒绝：${JSON.stringify(refused)}`)
    await page.goto(`${base}/llm_bridge?m=${bridgeId}&s=${theirId}`)
    await thread(page).getByText(`:: ${adminText}`).waitFor()
    await page.getByRole('note').filter({ hasText: `这是「${ADMIN_NAME}」建的会话` }).waitFor()
    check(/别的管理员|聊天管理员/.test(await side(page).getByRole('button', { name: new RegExp(adminText) }).innerText()), '列表里写明是谁建的')
    await composer(page).fill('我来补充一句')
    page.once('dialog', (dialog) => dialog.dismiss())
    await composer(page).press('Enter')
    await page.waitForTimeout(500)
    check((await countMessages(page)) === 2 && (await composer(page).inputValue()) === '我来补充一句', '取消确认时不发送，草稿还在')
    page.once('dialog', (dialog) => dialog.accept())
    await composer(page).press('Enter')
    await thread(page).getByText(':: 我来补充一句').waitFor({ timeout: 60000 })
    // 同一个会话里确认过一次，之后不再问（再弹确认框的话，没人接会卡住，这一步会超时）
    await composer(page).fill('再补一句')
    await composer(page).press('Enter')
    await thread(page).getByText(':: 再补一句').waitFor({ timeout: 60000 })
    step('别的管理员建的会话：能看；接着发消息前确认一次（接口不带确认直接拒绝），之后不再问；admin 也能聊')

    // ---------------------------------------------------------------- member 用不了
    const member = await login(browser, memberEmail, problems, 'member')
    await openAs(member.page, workspace, '/')
    const memberRead = await api(member.page, workspace, 'GET', `${chat}/sessions`)
    const memberWrite = await api(member.page, workspace, 'POST', `${chat}/sessions/${firstId}/messages`, { text: 'x', request_id: requestId(), takeover: true })
    check(memberRead.status === 403 && memberWrite.status === 403, `member 应被拒绝：${memberRead.status} / ${memberWrite.status}`)
    check((await member.page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '大模型客户端', exact: true }).count()) === 0, 'member 的侧栏不应有入口')
    await member.context.close()
    step('member 看不到入口，接口也拒绝')

    // ---------------------------------------------------------------- 归档
    await page.goto(`${base}/llm_bridge?m=${bridgeId}&s=${firstId}`)
    await page.getByRole('heading', { level: 2, name: TITLE }).waitFor()
    page.once('dialog', (dialog) => dialog.accept())
    await page.getByRole('button', { name: '归档会话' }).click()
    await page.getByText('这个会话已归档，不能再发送。').waitFor()
    check((await composer(page).count()) === 0, '归档后没有输入框')
    await side(page).getByRole('button', { name: new RegExp(TITLE) }).waitFor({ state: 'detached' })
    await side(page).getByLabel('看已归档的').check()
    await side(page).getByRole('button', { name: new RegExp(TITLE) }).waitFor()
    await side(page).getByLabel('看已归档的').uncheck()
    const archivedSend = await api(page, workspace, 'POST', `${chat}/sessions/${firstId}/messages`, { text: 'x', request_id: requestId() })
    check(archivedSend.status === 409, `归档后接口也应拒绝发送：${JSON.stringify(archivedSend)}`)
    step('归档后不能再发（页面和接口都拒绝），在“看已归档的”里能找到')

    // ---------------------------------------------------------------- 管理器停用：只能看
    const off = await api(page, workspace, 'PATCH', `/api/v1/llm_bridge/bridges/${bridgeId}`, { enabled: false })
    check(off.status === 200, `没能停用管理器：${JSON.stringify(off)}`)
    await page.goto(`${base}/llm_bridge?m=${bridgeId}&s=${theirId}`)
    await thread(page).getByText(`:: ${adminText}`).waitFor()
    await page.getByRole('note').filter({ hasText: '这个 MCP 管理器已停用：可以查看，不能发送。' }).waitFor()
    check(await side(page).getByRole('button', { name: '新建会话' }).isDisabled(), '停用后不能新建会话')
    const offSend = await api(page, workspace, 'POST', `${chat}/sessions/${theirId}/messages`, { text: 'x', request_id: requestId(), takeover: true })
    const offCreate = await api(page, workspace, 'POST', `${chat}/sessions`, { platform: 'claude', mode: 'chat' })
    check(offSend.status === 409 && offSend.body?.error?.code === 'BRIDGE_DISABLED' && offCreate.status === 409, `停用后接口应拒绝发送和新建：${offSend.status} / ${offCreate.status}`)
    const on = await api(page, workspace, 'PATCH', `/api/v1/llm_bridge/bridges/${bridgeId}`, { enabled: true })
    check(on.status === 200, `没能重新启用管理器：${JSON.stringify(on)}`)
    step('管理器停用后会话还能看，页面和接口都不能新建、不能发送')

    // ---------------------------------------------------------------- 多模型同问（T-0232）
    const buddyIn = await api(page, workspace, 'POST', `/api/v1/llm_bridge/bridges/${bridgeId}/accounts/workbuddy/login`, { method: 'api_key', api_key: FAKE_KEY })
    check(buddyIn.status === 200, `WorkBuddy 登录失败：${JSON.stringify(buddyIn)}`)
    const groupsApi = '/api/v1/llm_bridge/chat/groups'
    const tooFew = await api(page, workspace, 'POST', groupsApi, { text: 'x', request_id: requestId(), targets: [{ bridge_id: bridgeId, platform: 'claude', mode: 'chat' }] })
    check(tooFew.status === 422, `只有一个目标应被拒绝：${tooFew.status}`)
    await page.goto(`${base}/llm_bridge?m=${bridgeId}`)
    await side(page).getByRole('button', { name: '多模型同问' }).click()
    const compare = page.getByRole('form', { name: '多模型同问' })
    // 当前管理器上已登录、能对话的平台先加进来
    const chosen = compare.getByRole('list', { name: '发给（2 个）' })
    await chosen.waitFor({ timeout: 60000 })
    check((await chosen.getByRole('listitem').allInnerTexts()).join('|') === `Claude · 默认模型 · ${MANAGER}|WorkBuddy · 默认模型 · ${MANAGER}`, `默认应选中已登录的两个平台：${await chosen.innerText()}`)
    await compare.getByLabel('问题').fill(QUESTION)
    await compare.getByRole('button', { name: '发给 2 个模型' }).click()
    await page.getByRole('heading', { level: 2, name: QUESTION }).waitFor({ timeout: 60000 })
    const groupId = new URL(page.url()).searchParams.get('g')
    check(/^[0-9a-f-]{36}$/.test(groupId || ''), `地址里应带上这一组：${page.url()}`)
    const claudeColumn = page.getByRole('region', { name: `Claude（${MANAGER}）` })
    const buddyColumn = page.getByRole('region', { name: `WorkBuddy（${MANAGER}）` })
    await claudeColumn.getByText(`:: ${QUESTION}`).waitFor({ timeout: 60000 })
    await buddyColumn.getByText(`:: ${QUESTION}`).waitFor({ timeout: 60000 })
    const entry = side(page).getByRole('region', { name: '同问' }).getByRole('button', { name: new RegExp(QUESTION) })
    check(/同问 2 个模型 · 我/.test(await entry.innerText()), '列表的“同问”里应有这一组')
    // 继续问：两个模型都收到
    const ask = page.getByRole('form', { name: '向这一组继续发' })
    await ask.getByRole('textbox', { name: '问题内容' }).fill('再问一句')
    await ask.getByRole('textbox', { name: '问题内容' }).press('Enter')
    await claudeColumn.getByText(':: 再问一句').waitFor({ timeout: 60000 })
    await buddyColumn.getByText(':: 再问一句').waitFor({ timeout: 60000 })
    // 每个模型就是一个普通会话：可以回到单会话里单独继续
    await claudeColumn.getByRole('link', { name: '在这个会话里继续' }).click()
    await page.getByRole('heading', { level: 2, name: QUESTION }).waitFor()
    check(/^bs_/.test(new URL(page.url()).searchParams.get('s') || ''), '应回到单会话')
    await composer(page).fill('单独继续')
    await composer(page).press('Enter')
    await thread(page).getByText(':: 单独继续').waitFor({ timeout: 60000 })
    // 别的管理员能看到这一组；不确认不能接着发
    const second = await login(browser, adminEmail, problems, 'admin')
    await openAs(second.page, workspace, '/llm_bridge')
    const theirGroups = await api(second.page, workspace, 'GET', groupsApi)
    check(theirGroups.body?.groups?.find((g) => g.id === groupId)?.owner_kind === 'user', `admin 应看到 owner 建的这一组：${JSON.stringify(theirGroups.body)}`)
    const noTakeover = await api(second.page, workspace, 'POST', `${groupsApi}/${groupId}/messages`, { text: 'x', request_id: requestId() })
    check(noTakeover.status === 409 && noTakeover.body?.error?.code === 'TAKEOVER_REQUIRED', `别人建的一组不确认应被拒绝：${JSON.stringify(noTakeover)}`)
    await second.context.close()
    // 归档这一组：不再并排显示，每个模型的会话还在
    await page.goto(`${base}/llm_bridge?m=${bridgeId}&g=${groupId}`)
    await claudeColumn.getByText(':: 单独继续').waitFor({ timeout: 60000 })
    page.once('dialog', (dialog) => dialog.accept())
    await page.getByRole('button', { name: '归档这一组' }).click()
    await page.getByText('这一组同问已归档，不能再一起发').waitFor()
    await side(page).getByRole('region', { name: '同问' }).waitFor({ state: 'detached' })
    await side(page).getByRole('button', { name: new RegExp(QUESTION) }).first().waitFor()
    const archivedGroupSend = await api(page, workspace, 'POST', `${groupsApi}/${groupId}/messages`, { text: 'x', request_id: requestId() })
    check(archivedGroupSend.status === 409, `归档后的一组接口也应拒绝：${archivedGroupSend.status}`)
    step('多模型同问：默认选中已登录的平台；同一个问题两个模型并排回答；继续问、回到单会话；别的管理员要确认；归档后会话还在')

    // ---------------------------------------------------------------- 审计：记了动作，没有消息内容
    const audit = await api(page, workspace, 'GET', '/api/v1/audit-logs?limit=100&action_prefix=llm_bridge.chat_')
    check(audit.status === 200, `读不到审计：${JSON.stringify(audit)}`)
    const actions = new Set(audit.body.items.map((item) => item.action))
    for (const action of ['chat_session_created', 'chat_message_sent', 'chat_takeover_sent', 'chat_turn_cancelled', 'chat_session_renamed', 'chat_session_archived', 'chat_group_created', 'chat_group_sent', 'chat_group_archived']) {
      check(actions.has(`llm_bridge.${action}`), `审计里应有 llm_bridge.${action}：${[...actions].join('、')}`)
    }
    const auditText = JSON.stringify(audit.body.items)
    check(!auditText.includes(FIRST) && !auditText.includes('我来补充一句') && !auditText.includes(QUESTION) && !auditText.includes(FAKE_KEY), '审计里不应有消息内容和密钥')
    step('审计记下了新建、发送、接着别人的会话发、停止、改标题、归档和同问的新建、继续、归档，不含消息内容')

    // ---------------------------------------------------------------- 手机宽度：列表和对话分成两屏
    const phone = await login(browser, ownerEmail, problems, 'owner（手机）', { width: 390, height: 844 })
    await phone.page.goto(`${base}/llm_bridge?m=${bridgeId}`)
    await side(phone.page).getByRole('button', { name: new RegExp(adminText) }).waitFor()
    check((await composer(phone.page).count()) === 0, '手机上先只显示会话列表')
    check(await noHorizontalScroll(phone.page), '手机宽度下会话列表不应横向滚动')
    await side(phone.page).getByRole('button', { name: new RegExp(adminText) }).click()
    await thread(phone.page).getByText(`:: ${adminText}`).waitFor()
    await composer(phone.page).waitFor()
    check((await side(phone.page).count()) === 0, '进入对话后不再显示列表')
    check(await noHorizontalScroll(phone.page), '手机宽度下对话不应横向滚动')
    await phone.page.getByRole('button', { name: '会话信息' }).click()
    await phone.page.getByRole('complementary', { name: '会话信息' }).getByText(MANAGER).waitFor()
    await phone.page.getByRole('button', { name: '收起会话信息' }).click()
    await phone.page.getByRole('button', { name: '返回会话列表' }).click()
    await side(phone.page).waitFor()
    await side(phone.page).getByRole('button', { name: '新建会话' }).click()
    await phone.page.getByRole('form', { name: '新建会话' }).getByLabel('第一条消息').waitFor()
    check(await noHorizontalScroll(phone.page), '手机宽度下新建会话不应横向滚动')
    // 同问在手机上一次看一栏，用标签切换
    await phone.page.goto(`${base}/llm_bridge?m=${bridgeId}&g=${groupId}`)
    const tabs = phone.page.getByRole('tablist', { name: '这一组里的模型' })
    await tabs.waitFor()
    await phone.page.getByRole('region', { name: `Claude（${MANAGER}）` }).getByText(`:: ${QUESTION}`).waitFor({ timeout: 60000 })
    check(!(await phone.page.getByRole('region', { name: `WorkBuddy（${MANAGER}）` }).isVisible()), '手机上一次只看一栏')
    await tabs.getByRole('tab', { name: new RegExp('WorkBuddy') }).click()
    await phone.page.getByRole('region', { name: `WorkBuddy（${MANAGER}）` }).getByText(`:: ${QUESTION}`).waitFor({ timeout: 60000 })
    check(await noHorizontalScroll(phone.page), '手机宽度下同问不应横向滚动')
    await phone.context.close()
    step('手机宽度（390）：列表和对话分成两屏，能返回，会话信息从右上角打开；同问一次看一栏；都不横向滚动')

    check(problems.length === 0, `检查过程中出现错误：\n  ${problems.join('\n  ')}`)
    console.log('大模型聚合聊天浏览器验收全部通过')
  } finally {
    // 成败都收尾：删掉这次添加的管理器、关掉模块（别的验收脚本按“模块默认停用”检查）
    if (ownerPage) {
      try {
        const left = await api(ownerPage, workspace, 'GET', '/api/v1/llm_bridge/bridges')
        for (const bridge of left.body?.bridges ?? []) {
          await api(ownerPage, workspace, 'POST', `/api/v1/llm_bridge/bridges/${bridge.id}/accounts/claude/logout`)
          await api(ownerPage, workspace, 'POST', `/api/v1/llm_bridge/bridges/${bridge.id}/accounts/workbuddy/logout`)
          await api(ownerPage, workspace, 'DELETE', `/api/v1/llm_bridge/bridges/${bridge.id}`)
        }
        await api(ownerPage, workspace, 'PATCH', '/api/v1/modules/llm_bridge', { enabled: false })
      } catch (error) { console.error(`收尾失败：${error.message}`) }
    }
    await browser.close()
  }
}

main().catch((error) => { console.error(`失败  ${error.message}`); process.exit(1) })
