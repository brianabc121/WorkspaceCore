// 页面上一键部署 MCP 管理器的浏览器验收（T-0234，docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md 第 7 节）：
// 真实后端 + 真实 sshd + 真实 Docker——
// 服务器页面的“MCP 管理器”区块（空的，有部署入口）→ 点“在这台服务器上部署 MCP 管理器”到管理页，表单已选好这台服务器 →
// 填名字、选平台、“开始部署” → 页面显示每一步直到部署完成 → 管理器卡片写着部署位置 →
// 回到服务器页面，区块里有它（在线），“管理”链接回到管理页 → 重启 → 卸载（连数据卷）→ 区块又空了 → 390px 宽度无横向滚动。
// 登录密码、管理令牌不出现在页面和接口响应里。
//
// 两种用法：
// - CI 的 agents job（在 mcp_manager_deploy_acceptance.py 之后，那时服务器已从工作台移除）：
//   SSH_TARGET_HOST / SSH_TARGET_PORT / SSH_TARGET_USER / SSH_TARGET_PASSWORD 都给了时，脚本先经接口把它添加成智能体服务器、
//   确认指纹、初始化（这一段不是本验收的对象，页面上的操作由 agent_ssh_browser.cjs 验收），结束时移除。用户由 seed.py 写入。
// - 本机：自己建好 owner 和服务器，用 MCP_BROWSER_EMAIL / MCP_BROWSER_PASSWORD、MCP_TARGET_KIND（agent_server / runner_host）、
//   MCP_TARGET_ID 传入。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定）：
//   node mcp_manager_browser.cjs [BASE_URL]    # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const path = require('node:path')
const { chromium } = require('playwright')

const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const run = randomBytes(4).toString('hex')
const TIMEOUT = 25 * 60 * 1000

function check(condition, message) { if (!condition) throw new Error(message) }
function step(text) { console.log(`通过  ${text}`) }
async function until(test, timeout) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    if (await test()) return true
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  return false
}
async function noHorizontalScroll(page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)
}

async function main() {
  const sshHost = process.env.SSH_TARGET_HOST
  let email = process.env.MCP_BROWSER_EMAIL, password = process.env.MCP_BROWSER_PASSWORD
  if (!email) {
    email = `mcp-ui-${run}@browser.example.com`; password = 'e2e mcp browser pass'
    execFileSync('python3', [path.join(__dirname, 'seed.py'), 'user', email, 'MCP 网页部署'], { encoding: 'utf8', env: { ...process.env, E2E_PASSWORD: password } })
  }
  const browser = await chromium.launch()
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  const problems = [], bodies = []
  page.on('pageerror', (error) => problems.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error' && !/\b(401|404|409|503)\b/.test(message.text())) problems.push(message.text()) })
  page.on('response', async (response) => {
    if (!response.url().startsWith(`${base}/api/`)) return
    try { bodies.push(await response.text()) } catch { /* 页面跳转后响应体已不可读 */ }
  })
  await page.goto(`${base}/login`)
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL(`${base}/`)

  // 经接口做准备工作（带着页面的登录状态）
  const me = await (await context.request.get(`${base}/api/v1/me`)).json()
  const workspace = me.workspaces[0].id
  const csrf = (await context.cookies()).find((c) => c.name === 'ws_csrf')?.value ?? ''
  const headers = { 'X-Workspace-Id': workspace, 'X-CSRF-Token': decodeURIComponent(csrf), Origin: base }
  const call = async (method, url, data) => {
    const response = await context.request.fetch(`${base}${url}`, { method, headers, data })
    return { status: response.status(), body: await response.json().catch(() => null) }
  }
  const enabled = await call('PATCH', '/api/v1/modules/llm_bridge', { enabled: true })
  check(enabled.status === 200, `启用模块失败：${enabled.status}`)

  let kind = process.env.MCP_TARGET_KIND, targetId = process.env.MCP_TARGET_ID
  const serverPath = () => `/api/v1/agents/servers/${targetId}`
  if (sshHost) {
    kind = 'agent_server'
    const created = await call('POST', '/api/v1/agents/servers', {
      host: sshHost, port: Number(process.env.SSH_TARGET_PORT), username: process.env.SSH_TARGET_USER, login_password: process.env.SSH_TARGET_PASSWORD,
    })
    check(created.status === 201, `添加服务器失败：${created.status} ${JSON.stringify(created.body)}`)
    const server = created.body.server
    targetId = server.id
    const confirmed = await call('POST', `${serverPath()}/confirm`, { version: server.version, fingerprint: server.ssh.host_key_fingerprint })
    check(confirmed.status === 200, `确认指纹失败：${confirmed.status}`)
    const version = (await call('GET', serverPath())).body.server.version
    check((await call('POST', `${serverPath()}/prepare`, { version })).status === 202, '开始初始化失败')
    check(await until(async () => (await call('GET', serverPath())).body.job?.status === 'succeeded', 15 * 60 * 1000), '初始化没有成功')
  }
  check(kind && targetId, '缺少 MCP_TARGET_KIND / MCP_TARGET_ID（或 SSH_TARGET_*）')
  const serverPage = kind === 'agent_server' ? `${base}/agent-servers?server=${targetId}` : `${base}/runner?tab=settings&host_id=${targetId}`
  const name = `网页 ${run}`

  try {
    // ---------------------------------------------------------------- 服务器页面的入口（空）
    await page.goto(serverPage)
    const panel = page.getByRole('region', { name: 'MCP 管理器' })
    await panel.waitFor({ timeout: 30000 })
    await panel.getByText('这台服务器上还没有工作台部署的 MCP 管理器。').waitFor({ timeout: 30000 })
    check(/不受本页面的重启、清理、回收和卸载影响/.test(await panel.textContent()), '区块应说明这些容器不受本页面的管理程序影响')
    check(await panel.getByRole('button', { name: /重启|卸载|删除|停止/ }).count() === 0, '服务器页面的区块里不应有操作管理器的按钮')
    step('服务器页面有“MCP 管理器”区块：还没有管理器，说明它们不受本页面影响，没有操作按钮')

    // ---------------------------------------------------------------- 从入口去部署
    await panel.getByRole('link', { name: '在这台服务器上部署 MCP 管理器' }).click()
    await page.waitForURL(/\/llm_bridge\?/)
    const form = page.getByRole('form', { name: '一键部署 MCP 管理器' })
    await form.waitFor({ timeout: 30000 })
    const kindName = kind === 'agent_server' ? '智能体服务器' : '容器服务器'
    check(await form.getByRole('radio', { name: kindName }).isChecked(), `表单应已选好服务器类型“${kindName}”`)
    check(await form.getByLabel('部署到哪台服务器').inputValue() === targetId, '表单应已选好这台服务器')
    await form.getByLabel('名字').fill(name)
    for (const platform of ['ChatGPT', 'Codex']) {
      const box = form.getByRole('checkbox', { name: platform, exact: true })
      if (await box.isChecked()) await box.uncheck()
    }
    await form.getByRole('button', { name: '开始部署' }).click()
    const progress = page.getByRole('region', { name: `部署「${name}」的进度` })
    await progress.waitFor({ timeout: 30000 })
    check(await until(async () => !/进行中/.test(await progress.locator('strong').first().textContent()), TIMEOUT), '部署没有在限定时间内结束')
    const done = await progress.textContent()
    check(/部署完成，已经登记到工作台/.test(done), `部署应成功：${done.slice(0, 600)}`)
    for (const label of ['连接', '检查', '上传', '构建', '启动', '登记']) check(done.includes(label), `进度里应有“${label}”一步：${done.slice(0, 400)}`)
    step('从服务器页面的入口到管理页：表单已选好这台服务器；填名字、选平台、开始部署，页面显示每一步直到部署完成')

    // ---------------------------------------------------------------- 管理器卡片
    await page.goto(`${base}/llm_bridge?view=manage`)
    const card = page.getByRole('article', { name: `MCP 管理器：${name}` })
    await card.waitFor({ timeout: 30000 })
    check(await until(async () => new RegExp(`部署在${kindName}「`).test(await card.textContent()), 30000), `卡片应写明部署在哪台${kindName}：${await card.textContent()}`)
    check(await card.getByRole('button', { name: `重新部署${name}` }).count() === 1 && await card.getByRole('button', { name: `重启${name}` }).count() === 1
      && await card.getByRole('button', { name: `卸载${name}` }).count() === 1 && await card.getByRole('button', { name: `删除${name}` }).count() === 0,
    '工作台部署的管理器应有 重新部署 / 重启 / 卸载，没有普通的“删除”')
    step('管理器卡片写明部署位置和网络方式，有重新部署、重启、卸载')

    // ---------------------------------------------------------------- 服务器页面上看得到它
    await page.goto(serverPage)
    const row = panel.getByRole('row', { name: new RegExp(name) })
    await row.waitFor({ timeout: 60000 })
    check(await until(async () => /在线/.test(await row.textContent()), 60000), `服务器页面上它应在线：${await row.textContent()}`)
    check(await row.getByRole('link', { name: `打开聊天：${name}` }).count() === 1, '应有“打开聊天”')
    await page.setViewportSize({ width: 390, height: 844 })
    check(await noHorizontalScroll(page), '390px 宽度下服务器页面不应出现横向滚动')
    await page.setViewportSize({ width: 1440, height: 1000 })
    await row.getByRole('link', { name: `管理：${name}` }).click()
    await page.waitForURL(/\/llm_bridge\?.*view=manage/)
    await card.waitFor({ timeout: 30000 })
    step('服务器页面的区块里有它（在线、网络方式、部署时间），“管理”回到管理页；390px 无横向滚动')

    // ---------------------------------------------------------------- 重启、卸载
    const operate = async (action, title, expected, prepare) => {
      await card.getByRole('button', { name: `${action}${name}` }).click()
      const dialog = page.getByRole('form', { name: `${title}${name}` })
      await dialog.waitFor({ timeout: 30000 })
      if (prepare) await prepare(dialog)
      await dialog.locator('button[type=submit]').click()
      const region = page.getByRole('region', { name: `${action}「${name}」的进度` })
      await region.waitFor({ timeout: 30000 })
      check(await until(async () => !/进行中/.test(await region.locator('strong').first().textContent()), TIMEOUT), `${action}没有在限定时间内结束`)
      check(new RegExp(expected).test(await region.textContent()), `${action}应成功：${(await region.textContent()).slice(0, 600)}`)
    }
    await operate('重启', '重启', '重启完成')
    step('重启：页面显示每一步直到工作台重新连上它')
    await operate('卸载', '卸载', '卸载完成', async (dialog) => { await dialog.getByLabel(/同时删除数据卷/).check() })
    check(await until(async () => await card.count() === 0, 30000), '卸载后管理器卡片应消失')
    await page.goto(serverPage)
    await panel.getByText('这台服务器上还没有工作台部署的 MCP 管理器。').waitFor({ timeout: 30000 })
    step('卸载（同时删除数据卷）：页面显示每一步直到卸载完成，卡片消失，服务器页面的区块又空了')

    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`${base}/llm_bridge?view=manage`)
    await page.getByRole('heading', { name: '大模型客户端管理' }).waitFor({ timeout: 30000 })
    check(await noHorizontalScroll(page), '390px 宽度下管理页不应出现横向滚动')
    step('管理页在 390px 宽度下无横向滚动')

    const secret = process.env.SSH_TARGET_PASSWORD
    const html = await page.content()
    check(!bodies.some((body) => /BRIDGE_ADMIN_TOKEN=[0-9a-f]{16}/.test(body) || /PRIVATE KEY/.test(body)), '接口响应里不应有管理令牌或私钥')
    if (secret) check(!html.includes(secret) && !bodies.some((body) => body.includes(secret)), '服务器的登录密码不应出现在页面和接口响应里')
    check(problems.length === 0, `页面报错：${problems.join(' | ')}`)
    step('管理令牌、私钥、服务器登录密码不在页面和接口响应里；页面没有报错')
  } finally {
    if (sshHost && targetId) {
      const version = (await call('GET', serverPath())).body?.server?.version
      if (version) await call('DELETE', `${serverPath()}?version=${version}`)
    }
    await browser.close()
  }
  console.log('\n全部通过')
}

main().catch((error) => { console.error(`失败  ${error.message}`); process.exit(1) })
