// 邮件页浏览器验收（T-0170 / T-0171，docs/v2/pages-redesign 规范第 7、9 节）：真实后端 + 模拟 Google——
// 管理员连接两个 Gmail 邮箱（两个邮箱里放了同一个 message_id）→ 第一次打开邮件页不选邮箱就同时看到两个邮箱的邮件，
// 每封标明所属邮箱 → 两封同 ID 的邮件各自打开，正文不串号 → 回复只经所属邮箱发出（模拟 Google 收到的发信带 In-Reply-To）→
// 点一个邮箱只看它的邮件，`?account=` 深链打开同一范围 → 没有归档 / 删除 / 转发等 Gmail 写操作按钮 → 手机宽度不横向滚动。
//
// 前提：Compose 全栈已启动并迁移；api / worker 的 GOOGLE_OAUTH_TOKEN_URL、GOOGLE_OAUTH_REVOKE_URL、GMAIL_API_URL 指向
// http://fake-gmail:8080，INTEGRATION_ALLOWED_HOSTS 含 fake-gmail（与 ci-infra.yml agents job 的设置相同）。
// 本脚本自己启动模拟 Google（tests/e2e/fake_gmail_server.py，跑在 api 镜像里，加入 integrations 网络、别名 fake-gmail），结束时删除。
// 用法：node mail_browser.cjs [BASE_URL]；API_IMAGE 默认 my-workspace-api:local；SHOTS_DIR 设了就把两种范围的截图存进去。
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const path = require('node:path')
const { chromium } = require('playwright')

const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || 8080}`).replace(/\/$/, '')
const root = path.resolve(__dirname, '../..')
const password = 'e2e mail browser pass'
const run = randomBytes(4).toString('hex')
const ownerEmail = `mail-owner-${run}@browser.example.com`
const WORK = 'work.e2e@gmail.com', HOME = 'home.e2e@gmail.com'
const FAKE_PORT = Number(process.env.FAKE_GMAIL_PORT || 18093)
const fakeName = `e2e-mail-browser-gmail-${run}`
// 模拟服务认的假值（与 agents_acceptance.py 场景 23 相同），不是真实凭证
const CLIENT_ID = '1234567890-fake.apps.googleusercontent.com'
const CLIENT_SECRET = 'GOCSPX-fake-client-secret-000000'

function seed(...args) {
  return execFileSync('python3', [path.join(__dirname, 'seed.py'), ...args], { encoding: 'utf8', env: { ...process.env, E2E_PASSWORD: password } }).trim().split(/\s+/)
}
const docker = (...args) => execFileSync('docker', args, { encoding: 'utf8' }).trim()
function check(condition, message) { if (!condition) throw new Error(message) }
const step = (text) => console.log(`通过  ${text}`)
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function fake(method, pathname, body) {
  const response = await fetch(`http://127.0.0.1:${FAKE_PORT}${pathname}`, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  return response.json()
}
async function startFakeGoogle() {
  // 取本项目 api 容器所在的 integrations 网络（同一台机器上可能有别的 Compose 项目）
  const api = execFileSync('docker', ['compose', '-f', path.join(root, 'compose.yaml'), 'ps', '-q', 'api'], { encoding: 'utf8', cwd: root }).trim()
  check(api, '找不到运行中的 api 容器')
  const network = docker('inspect', '-f', '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}', api).split(/\s+/).filter((n) => n.endsWith('_integrations'))
  check(network.length === 1, `api 容器应在一个 integrations 网络里：${network.join(', ')}`)
  docker('run', '-d', '--rm', '--name', fakeName, '-p', `127.0.0.1:${FAKE_PORT}:8080`,
    '-v', `${root}/services/api/tests:/fake/tests:ro`, '-v', `${root}/tests/e2e/fake_gmail_server.py:/fake/server.py:ro`,
    '--entrypoint', 'python', process.env.API_IMAGE || 'my-workspace-api:local', '/fake/server.py')
  docker('network', 'connect', '--alias', 'fake-gmail', network[0], fakeName)
  for (let i = 0; i < 30; i += 1) {
    try { await fake('GET', '/_fake/stats'); return } catch { await sleep(1000) }
  }
  throw new Error('模拟 Google 没有就绪')
}

async function api(page, workspace, method, pathname, body) {
  return page.evaluate(async ({ method, pathname, body, workspace }) => {
    const csrf = document.cookie.split('; ').find((item) => item.startsWith('ws_csrf='))?.slice(8)
    const headers = { 'X-Workspace-Id': workspace, ...(csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {}) }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    const response = await fetch(pathname, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
    return { status: response.status, body: response.status === 204 ? null : await response.json() }
  }, { method, pathname, body, workspace })
}
function watch(page, problems) {
  page.on('pageerror', (error) => problems.push(error.message))
  page.on('response', (response) => {
    const pathname = new URL(response.url()).pathname
    if (response.status() >= 500 && !(response.status() === 503 && pathname.startsWith('/api/v1/im/'))) problems.push(`${response.status()} ${response.request().method()} ${pathname}`)
  })
  page.on('console', (message) => { if (message.type() === 'error' && !/\b(401|503)\b/.test(message.text())) problems.push(message.text()) })
}
const noHorizontalScroll = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)

;(async () => {
  await startFakeGoogle()
  const problems = []
  const browser = await chromium.launch()
  try {
    const [, workspace] = seed('user', ownerEmail, 'Mia')
    const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, locale: 'zh-CN' })
    const page = await context.newPage()
    watch(page, problems)
    await page.goto(`${base}/login`)
    await page.getByLabel('邮箱').fill(ownerEmail)
    await page.getByLabel('密码').fill(password)
    await page.getByRole('button', { name: '登录' }).click()
    await page.waitForURL(`${base}/`)

    // ---------------------------------------------------------------- 连接两个邮箱，放进同一个 message_id
    const settings = await api(page, workspace, 'PUT', '/api/v1/mail/settings', { client_id: CLIENT_ID, client_secret: CLIENT_SECRET })
    check(settings.status === 200, `保存 OAuth 客户端失败：${settings.status}`)
    const accounts = {}
    for (const email of [WORK, HOME]) {
      const connect = await api(page, workspace, 'POST', '/api/v1/mail/connect', { login_hint: email })
      check(connect.status === 200, `发起连接失败：${connect.status} ${JSON.stringify(connect.body).slice(0, 200)}`)
      const consent = await fake('POST', '/_fake/consent', { authorize_url: connect.body.authorize_url, email })
      await page.goto(`${base}/api/v1/mail/oauth/callback?${new URLSearchParams({ code: consent.code, state: consent.state })}`)
    }
    for (const item of (await api(page, workspace, 'GET', '/api/v1/mail/accounts')).body.items) accounts[item.email] = item
    check(accounts[WORK]?.status === 'active' && accounts[HOME]?.status === 'active', `两个邮箱都应已连接：${JSON.stringify(Object.keys(accounts))}`)
    const now = Date.now()
    await fake('POST', '/_fake/message', { email: WORK, sender: 'Alice <alice@example.com>', to: WORK, subject: `合同确认 ${run}`, text: '工作邮箱的正文：合同第三条已确认。', message_id: 'same-id-0001', thread_id: 'same-thread-01', when: now - 60_000 })
    await fake('POST', '/_fake/message', { email: HOME, sender: 'Bob <bob@example.com>', to: HOME, subject: `周末安排 ${run}`, text: '个人邮箱的正文：周六上午见。', message_id: 'same-id-0001', thread_id: 'same-thread-01', when: now - 30_000 })
    step('管理员经 Google 授权（模拟）连接两个邮箱，两个邮箱里各有一封 message_id 相同的邮件')

    // ---------------------------------------------------------------- 默认全部邮箱
    await page.goto(`${base}/mail`)
    const list = page.getByRole('region', { name: '邮件列表' })
    const workRow = list.getByRole('button', { name: new RegExp(`合同确认 ${run}`) })
    const homeRow = list.getByRole('button', { name: new RegExp(`周末安排 ${run}`) })
    await workRow.waitFor()
    await homeRow.waitFor()
    check(!new URL(page.url()).searchParams.has('account'), '第一次打开不应选中某个邮箱')
    const [workText, homeText] = [await workRow.textContent(), await homeRow.textContent()]
    // 没有设显示名时，列表行的邮箱标签用地址 @ 前面那段
    check(workText.includes(`所属邮箱：${WORK.split('@')[0]}`) && homeText.includes(`所属邮箱：${HOME.split('@')[0]}`), `每封邮件应标明所属邮箱：${workText} | ${homeText}`)
    const nav = page.getByRole('navigation', { name: '邮箱与分类' })
    check(await nav.getByRole('button', { name: /全部邮箱/ }).getAttribute('aria-pressed') === 'true', '默认应是“全部邮箱”')
    for (const name of ['归档', '删除', '转发', '回复全部', '标记未读']) check(await page.getByRole('button', { name, exact: true }).count() === 0, `不应有“${name}”按钮`)
    if (process.env.SHOTS_DIR) await page.screenshot({ path: path.join(process.env.SHOTS_DIR, 'mail-all-1600.png') })
    step('第一次打开邮件页：不选邮箱就同时看到两个邮箱的邮件，每封标明所属邮箱；没有 Gmail 写操作按钮')

    // ---------------------------------------------------------------- 同 ID 的两封各自打开
    const reader = page.getByRole('region', { name: '阅读' })
    await workRow.click()
    const workArticle = reader.getByRole('article', { name: `邮件：合同确认 ${run}` })
    await workArticle.getByText('工作邮箱的正文：合同第三条已确认。').waitFor()
    await homeRow.click()
    const homeArticle = reader.getByRole('article', { name: `邮件：周末安排 ${run}` })
    await homeArticle.getByText('个人邮箱的正文：周六上午见。').waitFor()
    check(!(await reader.textContent()).includes('合同第三条'), '打开另一个邮箱的同 ID 邮件后不应还显示前一封的正文')
    step('两个邮箱里 message_id 相同的邮件各自打开，正文不串号')

    // ---------------------------------------------------------------- 回复只经所属邮箱发出
    page.on('dialog', (dialog) => dialog.accept())
    await homeArticle.getByRole('button', { name: '回复' }).click()
    const form = page.getByRole('form', { name: '写邮件' })
    check(await form.getByLabel('发信邮箱').inputValue() === accounts[HOME].id, '回复应默认用这封邮件所属的邮箱')
    await form.getByLabel('正文（纯文字）').fill(`收到，周六见 ${run}`)
    await form.getByRole('button', { name: '发送' }).click()
    await page.getByText(`已发出：Re: 周末安排 ${run}`).waitFor()
    const sentHome = await fake('GET', `/_fake/sent?${new URLSearchParams({ email: HOME })}`)
    const sentWork = await fake('GET', `/_fake/sent?${new URLSearchParams({ email: WORK })}`)
    check(sentHome.length === 1 && sentHome[0].body.includes(`收到，周六见 ${run}`) && sentHome[0].in_reply_to === '<same-id-0001@mail.example.com>', `回复应经个人邮箱发出并带 In-Reply-To：${JSON.stringify(sentHome).slice(0, 300)}`)
    check(sentWork.length === 0, '工作邮箱不应发出任何邮件')
    step('回复默认用所属邮箱，只经这个邮箱发出，模拟 Google 收到的回复带原邮件的 In-Reply-To')

    // ---------------------------------------------------------------- 单邮箱
    await nav.getByRole('button', { name: new RegExp(WORK) }).click()
    await page.waitForURL((url) => url.searchParams.get('account') === accounts[WORK].id)
    await workRow.waitFor()
    await homeRow.waitFor({ state: 'detached' })
    check(await list.getByRole('searchbox').getAttribute('placeholder') === `搜索 ${WORK} 的邮件`, '单邮箱时搜索框应写明范围')
    if (process.env.SHOTS_DIR) await page.screenshot({ path: path.join(process.env.SHOTS_DIR, 'mail-single-1600.png') })
    await page.goto(`${base}/mail?account=${accounts[HOME].id}`)
    await homeRow.waitFor()
    check(await workRow.count() === 0, '深链打开个人邮箱时不应显示工作邮箱的邮件')
    check(await nav.getByRole('button', { name: new RegExp(HOME) }).getAttribute('aria-pressed') === 'true', '深链应选中个人邮箱')
    step('点一个邮箱只看它的邮件（URL 带 account），深链打开同一范围')

    // ---------------------------------------------------------------- 手机
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`${base}/mail`)
    await homeRow.waitFor()
    check(await noHorizontalScroll(page), '手机宽度不应横向滚动')
    await homeRow.click()
    await page.getByRole('article', { name: `邮件：周末安排 ${run}` }).waitFor()
    check(await noHorizontalScroll(page), '手机上打开邮件后不应横向滚动')
    if (process.env.SHOTS_DIR) await page.screenshot({ path: path.join(process.env.SHOTS_DIR, 'mail-reader-390.png') })
    step('390px 宽度：列表与打开的邮件都不横向滚动')
  } finally {
    await browser.close()
    try { docker('rm', '-f', fakeName) } catch { /* 已退出 */ }
  }
  if (problems.length) throw new Error(`页面错误：${problems.join('; ')}`)
  console.log('\n邮件页浏览器验收通过（页面无错误）')
})().catch((error) => {
  console.error(`失败  ${error.message}`)
  process.exit(1)
})
