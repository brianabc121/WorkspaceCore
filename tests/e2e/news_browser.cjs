// 新闻早报浏览器验收（T-0155，docs/v2/NEWS_PLAN.md 第 12 节）：真实后端 + 真实 TrendRadar 6.11.0 / MCP 4.1.1——
// 设置页（启用、推送时间、自建 NewsNow、RSS 源、排除词、ntfy 渠道，密钥只显示“已设置”）→ 测试推送经 MCP 到达 →
// TrendRadar 推来一份分两批的早报（经内部接收地址）→ 早报页各标签（今天 / 往期 / 热榜 / RSS / 搜索 / 收藏）、
// 收藏与“不感兴趣”（owner 顺手加排除词，配置文件随之改变）、早报正文的安全渲染 → 首页“今日早报”面板 →
// 手机宽度 → viewer 只读（看得到早报，没有收藏与设置）。
//
// 前提：Compose 全栈已启动并迁移，TrendRadar 已启动（make up && make migrate && make trendradar-up），
//   .env 里 TRENDRADAR_MCP_URL=http://trendradar-mcp:3333/mcp、NEWS_ALLOWED_HOSTS=fake-news；
//   TrendRadar 的输出卷里已经有数据（CI 里先跑 news_acceptance.py，它最后会关掉自己的早报，一个部署只能有一个工作空间启用）。
// 外部依赖换成 tests/e2e/fake_news_server.py（在 news 网络里叫 fake-news），本脚本自己启动、结束时删掉。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定）：
//   cd tests/e2e && npm ci && npm run browser:install    # 首次：安装依赖与 Chromium
//   node news_browser.cjs [BASE_URL]                       # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
// CI（.github/workflows/ci-infra.yml 的 news job）与本机共用。
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const ROOT = path.resolve(__dirname, '..', '..')
const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const password = 'e2e news browser pass'
const run = randomBytes(4).toString('hex')
const ownerEmail = `news-owner-${run}@browser.example.com`
const viewerEmail = `news-viewer-${run}@browser.example.com`
const FAKE = 'ws-e2e-fake-news'
const NTFY_TOPIC = `browser-${run}`
const CONFIG_DIR = '/var/lib/workspace/trendradar'

function seed(...args) {
  return execFileSync('python3', [path.join(__dirname, 'seed.py'), ...args], {
    encoding: 'utf8',
    env: { ...process.env, E2E_PASSWORD: password },
  }).trim().split(/\s+/)
}
function check(condition, message) { if (!condition) throw new Error(message) }
const docker = (...args) => execFileSync('docker', args, { cwd: ROOT, encoding: 'utf8' }).trim()
function project() {
  if (process.env.COMPOSE_PROJECT_NAME) return process.env.COMPOSE_PROJECT_NAME
  try {
    const line = fs.readFileSync(path.join(ROOT, '.env'), 'utf8').split('\n').find((l) => l.startsWith('COMPOSE_PROJECT_NAME='))
    if (line && line.slice(21).trim()) return line.slice(21).trim()
  } catch { /* 没有 .env 用默认项目名 */ }
  return 'my-workspace'
}
const apiExec = (...args) => docker('compose', 'exec', '-T', 'api', ...args)

// 模拟的 NewsNow / RSS / ntfy（与 news_acceptance.py 同一个 fake_news_server.py），跑在 api 镜像里
function startFake() {
  try { docker('rm', '-f', FAKE) } catch { /* 没有旧的 */ }
  docker('run', '-d', '--name', FAKE, '--network', `${project()}_news`, '--network-alias', 'fake-news',
    '--entrypoint', 'python', '-v', `${ROOT}/tests/e2e/fake_news_server.py:/srv/fake_news_server.py:ro`,
    'my-workspace-api:local', '/srv/fake_news_server.py', '8080')
  for (let i = 0; i < 30; i += 1) {
    try { fakeGet('/healthz'); return } catch { execFileSync('sleep', ['1']) }
  }
  throw new Error('模拟服务没有就绪')
}
function fakeGet(pathname) {
  const script = "import sys, urllib.request\nprint(urllib.request.urlopen('http://127.0.0.1:8080' + sys.argv[1], timeout=5).read().decode())"
  return JSON.parse(docker('exec', FAKE, 'python', '-c', script, pathname))
}

// TrendRadar 推早报的方式：通用 Webhook POST {"title","content"} 到工作台的内部接收地址（地址里的令牌在它的配置文件里）。
// 这里在 api 容器里按同样的格式推两批（企业微信式的批次头），验证拼接与通知；不经 Caddy。
const INGEST_SCRIPT = String.raw`
import json, re, sys, urllib.request
config = open(sys.argv[1] + "/config.yaml").read()
token = re.search(r"/internal/news/ingest/([A-Za-z0-9_-]+)", config).group(1)
for part in json.loads(sys.argv[2]):
    body = json.dumps({"title": sys.argv[3], "content": part}).encode()
    request = urllib.request.Request("http://127.0.0.1:8000/internal/news/ingest/" + token, body, {"Content-Type": "application/json"})
    print(urllib.request.urlopen(request, timeout=10).read().decode())
`
function pushBriefing(title, parts) {
  return apiExec('python', '-c', INGEST_SCRIPT, CONFIG_DIR, JSON.stringify(parts), title).split('\n').map((line) => JSON.parse(line).result)
}

// 页面里直接调用接口（带会话 Cookie、CSRF 与工作空间），用于准备数据与核对
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
  page.on('console', (message) => {
    // 登录前查询当前用户的 401、viewer 被拒的 403 是预期响应，浏览器会把它们记为资源加载失败
    if (message.type() === 'error' && !/status of (401|403)\b/.test(message.text())) problems.push(message.text())
  })
  // 早报正文里故意放了 onerror / javascript: 链接：弹出对话框就说明执行了脚本
  page.on('dialog', (dialog) => { problems.push(`页面弹出了对话框：${dialog.message()}`); void dialog.dismiss() })
}
async function login(browser, email, problems, viewport = { width: 1440, height: 1000 }) {
  const context = await browser.newContext({ viewport, locale: 'zh-CN', timezoneId: 'Asia/Shanghai' })
  const page = await context.newPage()
  watch(page, problems)
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
// 推送时间选在三小时后的半点：验收期间 TrendRadar 按新配置每分钟运行，但不会在这个时间段里推送
function quietSlot() {
  const local = new Date(Date.now() + 8 * 3600000 + 3 * 3600000)
  return `${String(local.getUTCHours()).padStart(2, '0')}:${local.getUTCMinutes() >= 30 ? '30' : '00'}`
}
const tab = (page, name) => page.getByRole('tablist', { name: '早报' }).getByRole('tab', { name, exact: true })
const panel = (page) => page.getByRole('tabpanel')
const item = (scope, title) => scope.getByRole('listitem', { name: title, exact: true })

;(async () => {
  startFake()
  const [, workspace, dashboard] = seed('user', ownerEmail, 'NewsOwner')
  const [viewerId] = seed('user', viewerEmail, 'NewsViewer')
  seed('member', workspace, viewerId, 'viewer')
  const browser = await chromium.launch()
  const problems = []
  const step = (text) => console.log(`通过  ${text}`)
  const { context, page } = await login(browser, ownerEmail, problems)

  // ---------------------------------------------------------------- 设置 → 新闻早报（owner）
  await page.goto(`${base}/settings/news`)
  const status = page.getByRole('region', { name: '状态' })
  await status.getByText('早报没有启用', { exact: true }).waitFor()
  const form = page.getByRole('form', { name: '新闻早报设置' })
  await form.getByRole('checkbox', { name: '启用新闻早报' }).check()
  const slot = quietSlot()
  await form.getByLabel('推送时间 1').selectOption(slot)
  await form.getByLabel('自建 NewsNow 地址（可不填，留空用公共接口）').fill('http://fake-news:8080/api/s')
  await form.getByRole('button', { name: '添加 RSS 源' }).click()
  const feed = form.getByRole('group', { name: 'RSS 源 1' })
  await feed.getByLabel('ID').fill('tech')
  await feed.getByLabel('名称').fill('科技 RSS')
  await feed.getByLabel('地址').fill('http://fake-news:8080/rss/tech.xml')
  check(await form.getByRole('group', { name: '兴趣主题 1' }).getByLabel('名称').inputValue() === 'AI 与大模型', '新工作空间应带默认的兴趣主题')
  await form.getByLabel(/^全局排除词/).fill('八卦\n绯闻')
  await form.locator('details.news-channel summary', { hasText: 'ntfy' }).click()
  const ntfy = form.getByRole('group', { name: '推送渠道：ntfy' })
  await ntfy.getByRole('checkbox', { name: '启用ntfy' }).check()
  await ntfy.getByLabel('服务器地址（可不填）').fill('http://fake-news:8080')
  await ntfy.getByLabel('主题（topic）').fill(NTFY_TOPIC)
  await page.getByRole('button', { name: '保存设置' }).click()
  await page.getByRole('status').filter({ hasText: '设置已保存：TrendRadar 下一次运行时生效。' }).waitFor()
  await ntfy.getByText('已设置', { exact: true }).waitFor()
  await status.getByRole('button', { name: '刷新状态' }).click()
  await status.getByText('在线', { exact: true }).waitFor({ timeout: 30000 })
  check(!(await page.content()).includes(NTFY_TOPIC), '页面上不应出现 ntfy 的主题（密钥只显示“已设置”）')
  const words = apiExec('cat', `${CONFIG_DIR}/frequency_words.txt`)
  const config = apiExec('cat', `${CONFIG_DIR}/config.yaml`)
  check(words.includes('[AI 与大模型]') && words.includes('八卦'), `兴趣主题与排除词应写进 frequency_words.txt：${words}`)
  check(config.includes('http://fake-news:8080/rss/tech.xml') && config.includes('http://fake-news:8080/api/s'), '自建 NewsNow 与 RSS 源应写进 config.yaml')
  step(`设置页：启用、推送时间 ${slot}、自建 NewsNow、RSS 源、排除词、ntfy；保存后写进 TrendRadar 的配置文件，密钥只显示“已设置”，状态显示 TrendRadar 在线`)

  await page.getByRole('button', { name: '发一条测试推送' }).click()
  await page.getByRole('list', { name: '测试推送结果' }).getByText('ntfy：成功').waitFor({ timeout: 30000 })
  const captured = fakeGet('/captured').items.filter((c) => c.path === `/${NTFY_TOPIC}`)
  check(captured.length === 1 && captured[0].body.includes('测试推送'), `ntfy 应收到一条测试推送：${JSON.stringify(captured).slice(0, 300)}`)
  step('测试推送：经 TrendRadar MCP 发到 ntfy，页面显示每个渠道的结果')

  // ---------------------------------------------------------------- TrendRadar 推来一份早报（两批）
  const briefingTitle = '每日热点汇总'
  const results = pushBriefing(briefingTitle, [
    '**[第 1/2 批次]**\n\n## AI 与大模型\n\n1. [OpenAI 发布新一代推理模型](https://www.zhihu.com/item/1) 知乎 第 1 名\n2. [DeepSeek 开源新模型引发讨论](https://www.zhihu.com/item/3)',
    '**[第 2/2 批次]**\n\n## AI 分析\n\nAI 与大模型是今天的主线。<img src="x" onerror="alert(1)">\n\n[危险链接](javascript:alert(1))',
  ])
  check(results.join(',') === 'ok,ok', `两批都应收下：${results}`)
  const notes = await api(page, workspace, 'GET', '/api/v1/notifications')
  const note = notes.body.items.find((n) => n.title === '今日早报已送达')
  check(note, 'owner 应收到“今日早报已送达”')
  const latestId = (await api(page, workspace, 'GET', '/api/v1/news/briefings?limit=1')).body.items[0].id
  const latest = (await api(page, workspace, 'GET', `/api/v1/news/briefings/${latestId}`)).body
  check(latest.status === 'complete' && latest.parts === 2 && latest.total_parts === 2, `早报应拼成完整的一份：${JSON.stringify(latest).slice(0, 300)}`)
  step('TrendRadar 推来的两批拼成一份完整早报，owner 收到通知')

  // ---------------------------------------------------------------- 早报页：今天
  await page.goto(`${base}/news`)
  const article = page.getByRole('article', { name: /^早报：/ })
  await article.getByRole('link', { name: 'OpenAI 发布新一代推理模型' }).waitFor()
  check(await article.getByText('AI 与大模型是今天的主线。').count() === 1, '早报正文应显示 AI 分析')
  check(await article.locator('img[onerror], script').count() === 0, '早报正文里的 HTML 不应原样渲染')
  check(await article.locator('a[href^="javascript"]').count() === 0, '早报正文不应有 javascript: 链接')
  const topic = page.getByRole('region', { name: '主题：AI 与大模型' })
  const openai = item(topic, 'OpenAI 发布新一代推理模型')
  await openai.waitFor({ timeout: 60000 })
  check((await openai.getByRole('link').getAttribute('href')).startsWith('https://www.zhihu.com/'), '条目标题应链接到原文')
  check(await topic.getByText('娱乐明星八卦新闻').count() === 0, '排除词应生效（今天各主题里没有八卦）')
  step('早报页“今天”：完整早报（正文安全渲染）+ 各主题今天的条目（经 MCP，排除词生效）')

  await openai.getByRole('button', { name: '收藏' }).click()
  await openai.getByRole('button', { name: '已收藏' }).waitFor()
  const deepseek = item(topic, 'DeepSeek 开源新模型引发讨论')
  await deepseek.getByRole('button', { name: '不感兴趣' }).click()
  const dismissForm = deepseek.getByRole('form', { name: '标为不感兴趣' })
  await dismissForm.getByLabel('同时把一个词加入全局排除（可不填）').fill('DeepSeek')
  await dismissForm.getByRole('button', { name: '标为不感兴趣' }).click()
  await deepseek.waitFor({ state: 'detached' })
  const settings = await api(page, workspace, 'GET', '/api/v1/news/settings')
  check(settings.body.exclusions.includes('DeepSeek'), `排除词应加上 DeepSeek：${settings.body.exclusions}`)
  check(apiExec('cat', `${CONFIG_DIR}/frequency_words.txt`).includes('DeepSeek'), '新的排除词应写进配置文件')
  step('收藏；owner 标“不感兴趣”时顺手加排除词：条目从今天消失，配置文件随之改变')

  // ---------------------------------------------------------------- 热榜 / RSS / 搜索 / 收藏 / 往期
  await tab(page, '热榜').click()
  await page.waitForURL(/tab=hot/)
  await panel(page).getByLabel('平台').selectOption({ label: '知乎' })
  const hot = panel(page).getByRole('list', { name: '热榜条目' })
  await item(hot, 'OpenAI 发布新一代推理模型').waitFor()
  await page.waitForLoadState('networkidle')
  const hrefs = await hot.getByRole('link').evaluateAll((links) => links.map((a) => a.getAttribute('href')))
  check(hrefs.length > 0 && hrefs.every((href) => href.startsWith('https://www.zhihu.com/')), `只看知乎时应只有知乎的条目：${hrefs}`)
  step('热榜：按平台筛选（知乎）')

  await tab(page, 'RSS').click()
  await panel(page).getByRole('link', { name: 'Claude 发布新的编程能力' }).waitFor()
  step('RSS：订阅源的最新条目')

  await tab(page, '搜索').click()
  const search = panel(page).getByRole('search', { name: '搜索新闻' })
  await search.getByLabel('关键词').fill('芯片')
  await search.getByRole('button', { name: '搜索' }).click()
  await panel(page).getByRole('list', { name: '搜索结果' }).getByRole('link', { name: '英伟达财报超预期，AI 芯片需求强劲' }).waitFor()
  step('搜索：按关键词搜到热榜条目')

  await tab(page, '收藏').click()
  const favorites = panel(page).getByRole('list', { name: '收藏的条目' })
  await item(favorites, 'OpenAI 发布新一代推理模型').getByRole('button', { name: '取消收藏' }).click()
  await panel(page).getByText(/还没有收藏/).waitFor()
  step('收藏：列出我收藏的条目，可以取消收藏')

  await tab(page, '往期').click()
  await panel(page).getByRole('list', { name: '往期早报' }).getByRole('button', { name: new RegExp(briefingTitle) }).click()
  await page.waitForURL(new RegExp(`briefing=${latest.id}`))
  await page.getByRole('article', { name: /^早报：/ }).getByText('AI 与大模型是今天的主线。').waitFor()
  await page.goto(base + note.link)
  await page.getByRole('article', { name: /^早报：/ }).getByRole('link', { name: 'OpenAI 发布新一代推理模型' }).waitFor()
  check(await tab(page, '往期').getAttribute('aria-selected') === 'true', '通知里的链接应直接打开往期里的那一份')
  step('往期：列表点开看全文；通知里的链接直接打开那一份早报')

  // ---------------------------------------------------------------- 首页“今日早报”面板
  const widget = await api(page, workspace, 'POST', `/api/v1/dashboards/${dashboard}/widgets`, {
    widget_type: 'news_briefing', config: { lines: 4 },
    layout: { desktop: { x: 0, y: 20, w: 6, h: 4 }, mobile: { y: 30, h: 4 } },
  })
  check(widget.status === 201, `添加“今日早报”组件失败：${widget.status} ${JSON.stringify(widget.body).slice(0, 200)}`)
  await page.goto(`${base}/`)
  const card = page.getByRole('article', { name: '今日早报' })
  await card.getByRole('link', { name: new RegExp(briefingTitle) }).waitFor()
  await card.getByRole('list', { name: '早报开头' }).getByText(/OpenAI 发布新一代推理模型/).waitFor()
  await card.getByRole('link', { name: '查看早报 →' }).click()
  await page.waitForURL(`${base}/news`)
  step('首页“今日早报”面板：标题与正文开头几行（纯文字），点开进早报页')

  // ---------------------------------------------------------------- 手机宽度
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'zh-CN', timezoneId: 'Asia/Shanghai', storageState: await context.storageState() })
  const phone = await mobile.newPage()
  watch(phone, problems)
  for (const url of ['/news', '/news?tab=hot', '/news?tab=search', `/news?briefing=${latest.id}`, '/settings/news']) {
    await phone.goto(base + url)
    await phone.waitForLoadState('networkidle')
    await phone.getByRole('heading', { level: 1 }).waitFor()
    check(await noHorizontalScroll(phone), `手机宽度下 ${url} 不应横向滚动`)
  }
  await mobile.close()
  step('手机宽度（390）：早报页各标签与设置页不横向滚动')

  // ---------------------------------------------------------------- viewer 只读
  const viewer = await login(browser, viewerEmail, problems)
  await viewer.page.getByLabel('当前工作空间').selectOption({ label: 'NewsOwner 的空间' })
  await viewer.page.goto(`${base}/news`)
  const viewerArticle = viewer.page.getByRole('article', { name: /^早报：/ })
  await viewerArticle.getByRole('link', { name: 'OpenAI 发布新一代推理模型' }).waitFor()
  await viewer.page.getByRole('region', { name: '主题：AI 与大模型' }).getByRole('listitem').first().waitFor({ timeout: 60000 })
  check(await viewer.page.getByRole('button', { name: '收藏' }).count() === 0, 'viewer 不应看到“收藏”')
  check(await viewer.page.getByRole('button', { name: '不感兴趣' }).count() === 0, 'viewer 不应看到“不感兴趣”')
  const refused = await api(viewer.page, workspace, 'POST', '/api/v1/news/feedback', {
    kind: 'favorite', source_type: 'hotlist', source_id: 'zhihu', source_name: '知乎', title: 'x', url: 'https://www.zhihu.com/item/9',
  })
  check(refused.status === 403, `viewer 收藏应被后端拒绝（403），实际 ${refused.status}`)
  check((await api(viewer.page, workspace, 'GET', '/api/v1/news/settings')).status === 403, 'viewer 读设置应被后端拒绝')
  await viewer.page.goto(`${base}/settings/news`)
  await viewer.page.waitForLoadState('networkidle')
  check(await viewer.page.getByRole('form', { name: '新闻早报设置' }).count() === 0, 'viewer 不应看到早报设置')
  step('viewer：能看早报、热榜等；看不到收藏、不感兴趣与设置，后端同样拒绝')

  check(problems.length === 0, `页面错误：${problems.join(' | ')}`)
  await browser.close()
  try { docker('rm', '-f', FAKE) } catch { /* 已经删了 */ }
  console.log('新闻早报浏览器验收通过')
})().catch((error) => { console.error(error); process.exit(1) })
