// Sprint 1 浏览器验收（T-0013）：首次安装 → 登录 → 修改设置 → 撤销会话 → 登出。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate），数据库里还没有任何用户。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定为 Playwright 1.63.0）：
//   cd tests/e2e && npm ci && npm run browser:install    # 首次：安装依赖与 Chromium
//   node sprint1_browser.cjs [BASE_URL]                    # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
//   - 安装凭据取环境变量 SETUP_TOKEN，未设置时读取仓库根目录的 .env。
//   - BASE_URL 的来源必须是 PUBLIC_BASE_URL 或在 CORS_ALLOWED_ORIGINS 里，否则写请求会被来源校验拒绝。
// CI（.github/workflows/ci-infra.yml）在清空数据卷后运行；接口级验收见 tests/e2e/sprint1_acceptance.py。
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const owner = { email: 'owner@browser.example.com', password: 'browser owner pass 1', name: 'Browser Owner' }

function setupToken() {
  if (process.env.SETUP_TOKEN) return process.env.SETUP_TOKEN
  const env = path.join(__dirname, '..', '..', '.env')
  const line = fs.existsSync(env) && fs.readFileSync(env, 'utf8').split('\n').find((l) => l.startsWith('SETUP_TOKEN='))
  if (!line) throw new Error('缺少安装凭据：请设置 SETUP_TOKEN 或先运行 make env')
  return line.slice('SETUP_TOKEN='.length).trim()
}

async function login(page) {
  await page.getByLabel('邮箱').fill(owner.email)
  await page.getByLabel('密码').fill(owner.password)
  await page.getByRole('button', { name: '登录' }).click()
}

;(async () => {
  const browser = await chromium.launch()
  const laptop = await (await browser.newContext()).newPage()
  const problems = []
  laptop.on('pageerror', (error) => problems.push(error.message))
  laptop.on('console', (message) => {
    // 登录前 / 登出后查询当前用户返回 401 是预期行为，浏览器会把它记为资源加载失败
    if (message.type() === 'error' && !message.text().includes('401')) problems.push(message.text())
  })
  const step = (text) => console.log(`通过  ${text}`)

  await laptop.goto(`${base}/`)
  await laptop.waitForURL(`${base}/setup`)
  step('空库时打开首页 → 首次安装页')
  await laptop.getByLabel('安装凭据').fill(setupToken())
  await laptop.getByLabel('显示名').fill(owner.name)
  await laptop.getByLabel('工作空间名称').fill('浏览器验收空间')
  await laptop.getByLabel('邮箱').fill(owner.email)
  await laptop.getByLabel('密码').fill(owner.password)
  await laptop.getByRole('button', { name: '创建工作空间' }).click()
  await laptop.waitForURL(`${base}/`)
  await laptop.getByText(owner.name).first().waitFor()
  step('首次安装完成，进入首页，顶栏显示管理员')

  await laptop.getByRole('button', { name: '登出' }).click()
  await laptop.waitForURL(/\/login/)
  await laptop.goto(`${base}/settings`)
  await laptop.waitForURL(/\/login\?redirect=/)
  await login(laptop)
  await laptop.waitForURL(`${base}/settings`)
  step('登出后访问 /settings 被引导登录，登录后回到 /settings')

  await laptop.getByLabel('显示名').fill('浏览器验收管理员')
  await laptop.getByRole('button', { name: '保存个人资料' }).click()
  await laptop.getByText('个人资料已保存').waitFor()
  await laptop.getByLabel('工作空间名称').fill('改名后的空间')
  await laptop.getByRole('button', { name: '保存工作空间' }).click()
  await laptop.getByText('工作空间已保存').waitFor()
  step('修改显示名与工作空间名称')

  const phone = await (await browser.newContext()).newPage()
  await phone.goto(`${base}/login`)
  await login(phone)
  await phone.waitForURL(`${base}/`)
  await laptop.goto(`${base}/settings/security`)
  // exact：修改密码表单的说明里也有“其他会话”四个字，要等的是会话列表里的那一条
  await laptop.getByText('其他会话', { exact: true }).waitFor()
  await laptop.getByRole('button', { name: '撤销会话', exact: true }).click()
  await laptop.getByRole('dialog').getByRole('button', { name: '确认撤销' }).click()
  await laptop.getByText('会话已撤销').waitFor()
  const remaining = await laptop.locator('.record-list li strong').allTextContents()
  if (remaining.join() !== '当前会话') throw new Error(`撤销后会话列表应只剩当前会话：${remaining}`)
  await phone.goto(`${base}/settings`)
  await phone.waitForURL(/\/login/)
  step('在另一台设备登录后撤销它的会话，该设备被引导重新登录')

  await laptop.goto(`${base}/settings/audit`)
  await laptop.locator('.record-list li').first().waitFor()
  const actions = await laptop.locator('.record-list li strong').allTextContents()
  for (const action of ['auth.session_revoked', 'workspace.updated', 'user.updated', 'setup.completed']) {
    if (!actions.includes(action)) throw new Error(`审计日志缺少 ${action}：${actions}`)
  }
  step(`审计日志显示 ${actions.length} 条记录（含撤销会话、修改设置、首次安装）`)

  await laptop.getByRole('button', { name: '登出' }).click()
  await laptop.waitForURL(/\/login/)
  await laptop.goto(`${base}/settings/audit`)
  await laptop.waitForURL(/\/login\?redirect=/)
  step('登出后访问受保护页面被引导登录')

  await browser.close()
  if (problems.length) throw new Error(`页面错误：${problems.join('; ')}`)
  console.log('\n全部通过（页面无错误）')
})().catch((error) => {
  console.error(`失败  ${error.message}`)
  process.exit(1)
})
