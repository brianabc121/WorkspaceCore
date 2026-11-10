// 插拔式模块与侧栏布局浏览器验收（T-0159 ~ T-0161，docs/modules/README.md）：真实后端 + 真实前端——
// 应用中心停用新闻早报 → 侧栏没有“早报”、打开 /news 显示“功能未启用”、早报接口 404 MODULE_DISABLED、
// member 也看不到 → 再启用恢复；侧栏与页面：新建分组、把“任务”挪进去、加外部链接与嵌入页并保存 →
// 侧栏按新布局显示、外部链接新窗口打开、嵌入页是带沙箱的 iframe 且 CSP 放行了 EMBED_ORIGINS →
// member 看得到新布局但不能改；手机宽度不横向滚动。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate）。.env 里 EMBED_ORIGINS 设了
//   https://example.com 时验证嵌入页（CI 会设置）；没设置时只验证设置页给出的部署提示。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定）：
//   cd tests/e2e && npm ci && npm run browser:install    # 首次：安装依赖与 Chromium
//   node modules_browser.cjs [BASE_URL]                    # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
// CI（.github/workflows/ci-infra.yml 的 compose job）与本机共用。不要求空库：每次新建工作台用户。
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const path = require('node:path')
const { chromium } = require('playwright')

const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const password = 'e2e modules browser pass'
const run = randomBytes(4).toString('hex')
const ownerEmail = `mod-owner-${run}@browser.example.com`
const memberEmail = `mod-member-${run}@browser.example.com`
const EMBED = 'https://example.com'

function seed(...args) {
  return execFileSync('python3', [path.join(__dirname, 'seed.py'), ...args], {
    encoding: 'utf8',
    env: { ...process.env, E2E_PASSWORD: password },
  }).trim().split(/\s+/)
}
function check(condition, message) { if (!condition) throw new Error(message) }

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
    if (response.status() >= 500) problems.push(`${response.status()} ${response.request().method()} ${new URL(response.url()).pathname}`)
  })
  page.on('console', (message) => {
    // 停用模块后页面上的早报请求会得到 404（预期内）；嵌入页的外站内容可能因网络加载失败
    if (message.type() === 'error' && !/Failed to load resource|example\.com|frame/i.test(message.text())) problems.push(message.text())
  })
}
async function login(browser, email, problems, viewport = { width: 1440, height: 1000 }) {
  const context = await browser.newContext({ viewport, locale: 'zh-CN' })
  const page = await context.newPage()
  watch(page, problems)
  await page.goto(`${base}/login`)
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL(`${base}/`)
  return { context, page }
}
const sidebar = (page) => page.getByRole('navigation', { name: '主导航' })
// seed.py 给每个用户都建了自己的空间：member 打开页面后要切到 owner 的工作空间（整页刷新后会回到第一个）
async function openAs(page, workspace, target) {
  await page.goto(`${base}${target}`)
  const select = page.getByLabel('当前工作空间')
  await select.waitFor()
  if ((await select.inputValue()) !== workspace) await select.selectOption(workspace)
}
async function noHorizontalScroll(page) {
  return !(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth))
}
async function groupContainer(page, title) {
  const button = sidebar(page).getByRole('button', { name: title, exact: true })
  await button.waitFor()
  if ((await button.getAttribute('aria-expanded')) === 'false') await button.click()
  return page.locator(`#${await button.getAttribute('aria-controls')}`)
}

async function main() {
  const [ownerId, workspace] = seed('user', ownerEmail, '模块验收')
  const [memberId] = seed('user', memberEmail, '模块成员')
  seed('member', workspace, memberId, 'member')
  check(ownerId && workspace, '没有建出测试用户')

  const browser = await chromium.launch()
  const problems = []
  try {
    const { page } = await login(browser, ownerEmail, problems)
    const member = await login(browser, memberEmail, problems)

    // ---------------------------------------------------------------- 应用中心：停用 / 启用新闻早报
    await sidebar(page).getByRole('link', { name: '早报', exact: true }).waitFor()
    await page.goto(`${base}/settings/modules`)
    await page.getByRole('heading', { name: '应用中心' }).waitFor()
    // 应用中心改版（T-0211）：模块是卡片（article），停用要在对话框里确认
    const card = page.getByRole('article', { name: '模块：新闻早报' })
    const toggle = card.getByRole('switch', { name: '启用新闻早报' })
    check((await toggle.getAttribute('aria-checked')) === 'true', '新闻早报默认应启用')
    await toggle.click()
    await page.getByRole('dialog', { name: '停用应用' }).getByRole('button', { name: '停用应用' }).click()
    await page.waitForFunction(() => document.querySelector('[role="switch"][aria-label="启用新闻早报"]')?.getAttribute('aria-checked') === 'false')
    await sidebar(page).getByRole('link', { name: '早报', exact: true }).waitFor({ state: 'detached' })

    await page.goto(`${base}/news`)
    await page.getByText('功能未启用').first().waitFor()
    await page.getByRole('link', { name: '前往应用中心' }).waitFor()
    const disabled = await api(page, workspace, 'GET', '/api/v1/news/today')
    check(disabled.status === 404 && disabled.body.error.code === 'MODULE_DISABLED', `早报接口应是 MODULE_DISABLED：${JSON.stringify(disabled)}`)

    await openAs(member.page, workspace, '/')
    await sidebar(member.page).getByRole('link', { name: '任务', exact: true }).waitFor()
    await sidebar(member.page).getByRole('link', { name: '早报', exact: true }).waitFor({ state: 'detached' })
    check(await sidebar(member.page).getByRole('link', { name: '早报', exact: true }).count() === 0, 'member 侧栏不应再有早报')
    await sidebar(member.page).getByRole('link', { name: '应用中心' }).waitFor({ state: 'detached' })
    const denied = await api(member.page, workspace, 'PATCH', '/api/v1/modules/news', { enabled: true })
    check(denied.status === 403, `member 不能启用模块：${denied.status}`)

    await page.goto(`${base}/settings/modules`)
    await toggle.click()
    await page.waitForFunction(() => document.querySelector('[role="switch"][aria-label="启用新闻早报"]')?.getAttribute('aria-checked') === 'true')
    await sidebar(page).getByRole('link', { name: '早报', exact: true }).waitFor()
    const back = await api(page, workspace, 'GET', '/api/v1/news/status')
    check(back.status === 200, `再启用后早报接口应恢复：${back.status}`)

    // ---------------------------------------------------------------- 侧栏与页面
    const layout = await api(page, workspace, 'GET', '/api/v1/navigation')
    const embedAllowed = layout.body.embed_origins.includes(EMBED)
    await page.goto(`${base}/settings/navigation`)
    await page.getByRole('heading', { name: '侧栏与页面', level: 1 }).waitFor()
    await page.getByLabel('新分组名称').fill('研究资料')
    await page.getByRole('button', { name: '添加分组' }).click()
    await page.getByRole('listitem', { name: '分组：研究资料' }).waitFor()
    await page.getByLabel('任务所在分组').selectOption({ label: '研究资料' })

    await page.getByRole('button', { name: '添加页面' }).click()
    let dialog = page.getByRole('dialog', { name: '添加页面' })
    await dialog.getByLabel('名称').fill('官方文档')
    await dialog.getByLabel('类型').selectOption('link')
    await dialog.getByLabel('分组').selectOption({ label: '研究资料' })
    await dialog.getByLabel('链接地址').fill('https://docs.example.com/guide')
    await dialog.getByRole('button', { name: '加入页面' }).click()
    await page.getByRole('listitem', { name: '页面：官方文档' }).waitFor()

    await page.getByRole('button', { name: '添加页面' }).click()
    dialog = page.getByRole('dialog', { name: '添加页面' })
    await dialog.getByLabel('名称').fill('外部看板')
    await dialog.getByLabel('类型').selectOption('embed')
    if (embedAllowed) {
      await dialog.getByRole('list', { name: '允许嵌入的来源' }).getByText(EMBED).waitFor()
      await dialog.getByLabel('嵌入地址').fill(`${EMBED}/board`)
      await dialog.getByRole('button', { name: '加入页面' }).click()
      await page.getByRole('listitem', { name: '页面：外部看板' }).waitFor()
    } else {
      await dialog.getByText('EMBED_ORIGINS').first().waitFor()
      await dialog.getByRole('button', { name: '取消' }).click()
    }

    await page.getByRole('button', { name: '保存侧栏布局' }).click()
    await page.getByText('侧栏布局已保存。').waitFor()

    const research = await groupContainer(page, '研究资料')
    await research.getByRole('link', { name: '任务', exact: true }).waitFor()
    const docs = research.getByRole('link', { name: /^官方文档\s*（在新窗口打开）$/ })
    check((await docs.getAttribute('target')) === '_blank', '外部链接应在新窗口打开')
    check(/noopener/.test(await docs.getAttribute('rel')), '外部链接应带 rel=noopener')

    if (embedAllowed) {
      const response = await page.goto(`${base}/`)
      const csp = response.headers()['content-security-policy'] || ''
      check(csp.includes(`frame-src 'self' ${EMBED}`), `CSP 应放行嵌入来源：${csp}`)
      await (await groupContainer(page, '工作空间')).waitFor()
      await sidebar(page).getByRole('link', { name: '外部看板', exact: true }).click()
      await page.waitForURL(/\/p\/[a-z0-9-]+$/)
      await page.getByRole('heading', { name: '外部看板', level: 1 }).waitFor()
      const frame = page.locator('iframe[title="外部看板"]')
      check((await frame.getAttribute('src')) === `${EMBED}/board`, '嵌入地址不对')
      check((await frame.getAttribute('sandbox')) === 'allow-scripts allow-same-origin allow-forms allow-popups', '嵌入页应带 sandbox')
      check((await frame.getAttribute('referrerpolicy')) === 'no-referrer', '嵌入页不应带来源信息')
    }

    // 版本冲突：用旧 version 保存返回 409
    const stale = await api(page, workspace, 'PUT', '/api/v1/navigation', { ...layout.body, version: 0, embed_origins: undefined, can_manage: undefined })
    check(stale.status === 409 || stale.status === 422, `旧版本保存应被拒绝：${stale.status}`)

    // member：看得到新布局，不能改
    await openAs(member.page, workspace, '/')
    const memberResearch = await groupContainer(member.page, '研究资料')
    await memberResearch.getByRole('link', { name: /^官方文档\s*（在新窗口打开）$/ }).waitFor()
    check(await sidebar(member.page).getByRole('link', { name: '侧栏与页面' }).count() === 0, 'member 不应看到侧栏设置入口')
    const put = await api(member.page, workspace, 'PUT', '/api/v1/navigation', { version: 1, sections: [], pages: [], placements: {}, section_order: [] })
    check(put.status === 403, `member 不能改侧栏：${put.status}`)

    // 手机宽度
    const phone = await login(browser, ownerEmail, problems, { width: 390, height: 844 })
    for (const target of ['/settings/navigation', '/settings/modules']) {
      await phone.page.goto(`${base}${target}`)
      await phone.page.getByRole('heading', { level: 1 }).first().waitFor()
      check(await noHorizontalScroll(phone.page), `${target} 在手机宽度横向滚动`)
    }

    check(problems.length === 0, `页面有错误：\n${problems.join('\n')}`)
    console.log('模块与侧栏浏览器验收通过')
  } finally {
    await browser.close()
  }
}

main().catch((error) => { console.error(error); process.exit(1) })
