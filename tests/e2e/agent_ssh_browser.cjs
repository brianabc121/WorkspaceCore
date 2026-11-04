// OpenClaw SSH 自动部署的浏览器验收（T-0090）：真实后端 + 真实 sshd + 真实 OpenClaw——
// 实例页登记外网实例 → “通过 SSH 自动部署”填写服务器 → 页面显示的指纹与 sshd 的主机公钥一致，勾选核对后才能确认 →
// 输入一次登录密码开始部署（输入框随即清空）→ 页面跟着任务显示每一步，直到“部署完成，实例已连上工作台”，卡片显示最近在线 →
// 运维（T-0114）：查看状态、立即上报一次、重启 OpenClaw（先输一次自己的登录密码）→ 服务器状态（T-0113）显示当前值与趋势 →
// 卸载（同时删除接入包目录）→ 吊销专用密钥 → 删除服务器 → 390px 宽度下无横向滚动 →
// 密码只出现在开始部署的那一个请求里：不在页面、接口响应、浏览器存储里；浏览器只访问工作台自己的地址。
//
// 前提与 agent_ssh_acceptance.py 相同（CI 的 agents job 在它之后运行，复用同一个 sshd 与账号）：
//   SSH_TARGET_HOST、SSH_TARGET_PORT、SSH_TARGET_USER、SSH_TARGET_PASSWORD、SSH_HOST_KEY_PUB（sshd 的主机公钥文件）；
//   Compose 全栈已启动并迁移，外网接入入口已配置。用户由同目录的 seed.py 写入（每次新的用户）；
//   不用 Compose 的本机环境可以先自己建好 owner，用 SSH_BROWSER_EMAIL / SSH_BROWSER_PASSWORD 传入。
// 本机的代理拦截 npm 的 TLS 时插件一步会失败：设 SSH_BROWSER_ALLOW_PLUGIN_FAILURE=1，部署停在插件一步也继续验收卸载与吊销。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定）：
//   node agent_ssh_browser.cjs [BASE_URL]    # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const path = require('node:path')
const { chromium } = require('playwright')

const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const run = randomBytes(4).toString('hex')
const INSTALL_TIMEOUT = 25 * 60 * 1000

function env(name) {
  const value = process.env[name]
  if (!value) throw new Error(`缺少环境变量 ${name}`)
  return value
}
function check(condition, message) { if (!condition) throw new Error(message) }
function step(text) { console.log(`通过  ${text}`) }

async function main() {
  const host = env('SSH_TARGET_HOST'), port = env('SSH_TARGET_PORT'), user = env('SSH_TARGET_USER')
  const sshPassword = env('SSH_TARGET_PASSWORD')
  const expected = execFileSync('ssh-keygen', ['-lf', env('SSH_HOST_KEY_PUB')], { encoding: 'utf8' }).split(/\s+/)[1]
  let email = process.env.SSH_BROWSER_EMAIL, password = process.env.SSH_BROWSER_PASSWORD
  if (!email) {
    email = `ssh-ui-${run}@browser.example.com`; password = 'e2e ssh browser pass'
    execFileSync('python3', [path.join(__dirname, 'seed.py'), 'user', email, 'SSH 网页部署'], { encoding: 'utf8', env: { ...process.env, E2E_PASSWORD: password } })
  }

  const browser = await chromium.launch()
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  const problems = [], requests = [], bodies = [], posts = []
  page.on('pageerror', (error) => problems.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error' && !/\b(401|409|503)\b/.test(message.text())) problems.push(message.text()) })
  page.on('request', (request) => { requests.push(request.url()); if (request.method() !== 'GET') posts.push(request.postData() ?? '') })
  page.on('response', async (response) => {
    if (!response.url().startsWith(`${base}/api/`)) return
    try { bodies.push(await response.text()) } catch { /* 页面跳转后响应体已不可读 */ }
  })
  await page.goto(`${base}/login`)
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL(`${base}/`)

  // ---------------------------------------------------------------- 登记外网实例
  const name = `ssh-ui-${run}`
  await page.goto(`${base}/agents`)
  await page.getByRole('button', { name: '登记实例' }).click()
  const dialog = page.getByRole('dialog', { name: '登记 OpenClaw 实例' })
  await dialog.getByLabel('名称 *').fill(name)
  await dialog.getByRole('radio', { name: /外网/ }).check()
  await dialog.getByRole('button', { name: '登记' }).click()
  const card = page.getByRole('article', { name: `实例：${name}` })
  const ssh = card.getByRole('region', { name: 'SSH 自动部署' })
  await ssh.waitFor()
  step('实例页登记外网实例，卡片上有“通过 SSH 自动部署”')

  // ---------------------------------------------------------------- 填写服务器，核对指纹
  const form = ssh.getByRole('form', { name: '目标服务器' })
  await form.getByLabel('服务器 IP 或域名 *').fill(host)
  await form.getByLabel('SSH 端口 *').fill(port)
  await form.getByLabel('SSH 账号 *').fill(user)
  await form.getByRole('button', { name: '读取主机公钥' }).click()
  const verify = ssh.getByRole('group', { name: '核对主机公钥' })
  await verify.waitFor({ timeout: 60000 })
  const shown = (await verify.getByTestId('ssh-fingerprint').textContent()).trim()
  check(shown === expected, `页面显示的指纹 ${shown} 应与 sshd 的主机公钥 ${expected} 一致`)
  check(await verify.getByText('ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub').isVisible(), '应给出在服务器上核对指纹的命令')
  const confirm = verify.getByRole('button', { name: '确认指纹' })
  check(await confirm.isDisabled(), '勾选“已核对”之前不能确认指纹')
  step(`读取主机公钥（不登录）：页面显示的 ${shown} 与 sshd 的一致，并给出核对命令`)

  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(300)
  check(!(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)), '390px 宽度下核对指纹时不应出现横向滚动')
  await page.setViewportSize({ width: 1440, height: 1000 })
  step('390px 宽度下指纹与核对命令自动换行，无横向滚动')

  await verify.getByLabel(/我已在服务器上核对/).check()
  await confirm.click()
  const deploy = ssh.getByRole('form', { name: '部署' })
  await deploy.waitFor()
  step('勾选核对后确认指纹')

  // ---------------------------------------------------------------- 输入一次密码，跟着进度到上线
  const field = deploy.getByLabel('登录密码 *')
  check(await field.getAttribute('type') === 'password', '密码输入框应是 password 类型')
  check(await deploy.getByRole('button', { name: '开始部署' }).isDisabled(), '没填密码时不能开始第一次部署')
  await field.fill(sshPassword)
  await deploy.getByRole('button', { name: '开始部署' }).click()
  const progress = ssh.getByRole('region', { name: '部署进度' })
  await progress.waitFor({ timeout: 30000 })
  check(await deploy.count() === 0 || await field.inputValue() === '', '提交后密码输入框应清空')
  step('输入一次密码开始部署，页面显示进度')

  const deadline = Date.now() + INSTALL_TIMEOUT
  let last = ''
  while (Date.now() < deadline) {
    if (await progress.getByText(/^部署：(成功|失败|已中断)$/).count()) break
    const current = await progress.locator('li[data-status="running"]').first().textContent({ timeout: 1000 }).catch(() => '')
    if (current && current !== last) { console.log(`  … ${current.trim()}`); last = current }
    await page.waitForTimeout(3000)
  }
  const statuses = await progress.locator('li').evaluateAll((items) => items.map((li) => `${li.textContent.trim()}=${li.dataset.status}`))
  const succeeded = await progress.getByText('部署完成，实例已连上工作台。').count() > 0
  if (!succeeded) {
    const reason = await progress.getByRole('alert').textContent().catch(() => '')
    const tolerated = process.env.SSH_BROWSER_ALLOW_PLUGIN_FAILURE === '1' && /plugin_failed/.test(reason)
    console.error(statuses.join('\n'))
    check(tolerated, `部署没有成功：${reason}`)
    check(await progress.locator('details pre').count() === 1, '失败时应能展开脱敏后的命令输出')
    step(`部署停在插件一步（本机代理拦截 npm，SSH_BROWSER_ALLOW_PLUGIN_FAILURE=1）：页面显示失败原因与脱敏日志：${reason.trim()}`)
  } else {
    check(statuses.every((s) => /=(done|skipped)$/.test(s)), `每一步都应完成或跳过：${statuses.join('; ')}`)
    const seen = card.locator('div', { has: page.getByText('最近在线', { exact: true }) }).locator('dd')
    const online = await (async () => {
      const end = Date.now() + 180000
      for (let i = 1; Date.now() < end; i++) {
        if (!/从未连接/.test(await seen.first().textContent())) return true
        await page.waitForTimeout(3000)
        if (i % 10 === 0) await page.reload()
      }
      return false
    })()
    check(online, '部署完成后实例卡片的“最近在线”应有时间')
    step(`页面跟着任务显示 ${statuses.length} 步直到“部署完成，实例已连上工作台”，实例卡片显示最近在线`)

    // ---------------------------------------------------------------- 运维动作（T-0114）
    const ops = card.getByRole('region', { name: '运维' })
    const finish = async (label) => {
      const result = ops.getByRole('article', { name: `运维结果：${label}` })
      await result.getByText(/^(成功|失败|已中断)$/).waitFor({ timeout: 240000 })
      return result
    }
    await ops.getByRole('button', { name: '查看状态' }).click()
    let result = await finish('查看状态')
    let output = await result.getByLabel(/输出/).textContent()
    check(await result.getByText('成功', { exact: true }).count() === 1 && /SERVICE/.test(output) && /openclaw/.test(output), `查看状态应成功并显示 docker compose ps：${output.slice(-400)}`)
    check(!/mwa_(?!\*\*\*)[A-Za-z0-9]/.test(output), '输出里不应有未脱敏的实例凭证')
    step('运维：查看状态，页面显示 docker compose ps、docker stats、df 与 uptime 的输出')
    await ops.getByRole('button', { name: '立即上报一次' }).click()
    result = await finish('立即上报一次')
    check(await result.getByText('成功', { exact: true }).count() === 1, '立即上报一次应成功')
    await ops.getByRole('button', { name: '重启 OpenClaw' }).click()
    const confirmForm = ops.getByRole('form', { name: '确认：重启 OpenClaw' })
    const secret = confirmForm.getByLabel('你的登录密码')
    check(await secret.getAttribute('type') === 'password', '确认密码的输入框应是 password 类型')
    check(await confirmForm.getByRole('button', { name: '确认执行' }).isDisabled(), '没填密码时不能确认')
    await secret.fill(password)
    await confirmForm.getByRole('button', { name: '确认执行' }).click()
    result = await finish('重启 OpenClaw')
    check(await result.getByText('成功', { exact: true }).count() === 1, `重启 OpenClaw 应成功：${await result.textContent()}`)
    step('运维：立即上报一次；重启 OpenClaw 先输一次自己的登录密码，执行成功')

    // ---------------------------------------------------------------- 服务器状态（T-0113）
    const hostStatus = card.getByRole('region', { name: '服务器状态' })
    await hostStatus.getByRole('button', { name: '查看服务器状态' }).click()
    const cpu = hostStatus.getByRole('region', { name: 'CPU' })
    await cpu.waitFor({ timeout: 60000 })
    const memory = await hostStatus.getByRole('region', { name: '内存' }).textContent()
    check(/核/.test(await cpu.textContent()) && /GB|MB/.test(memory), `服务器状态应显示 CPU 核心数与内存：${memory}`)
    check(await hostStatus.getByText('CPU 使用率', { exact: true }).count() === 1 && await hostStatus.getByText('负载（1 分钟）', { exact: true }).count() === 1, '应有 CPU 使用率与负载两张趋势图')
    await hostStatus.getByRole('button', { name: '最近 7 天' }).click()
    check(await hostStatus.getByRole('button', { name: '最近 7 天' }).getAttribute('aria-pressed') === 'true', '切换到最近 7 天')
    await page.setViewportSize({ width: 390, height: 844 })
    await page.waitForTimeout(300)
    check(!(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)), '390px 宽度下服务器状态与运维面板不应出现横向滚动')
    await page.setViewportSize({ width: 1440, height: 1000 })
    step('服务器状态：上报后显示 CPU、内存、两个分区与趋势图，可切换 7 天；390px 宽度无横向滚动')
  }

  // ---------------------------------------------------------------- 卸载、吊销、删除服务器
  const manage = ssh.getByRole('group', { name: '卸载与吊销' })
  await manage.getByLabel(/卸载时同时删除接入包目录/).check()
  page.once('dialog', (d) => d.accept())
  await manage.getByRole('button', { name: '卸载' }).click()
  const removal = ssh.getByRole('region', { name: '卸载进度' })
  await removal.getByText(/^卸载：(成功|失败)$/).waitFor({ timeout: 10 * 60 * 1000 })
  check(await removal.getByText('卸载完成。').count() === 1, `卸载应成功：${await removal.getByRole('alert').textContent().catch(() => '')}`)
  step('卸载（同时删除接入包目录）：页面显示每一步直到卸载完成')

  page.once('dialog', (d) => d.accept())
  await ssh.getByRole('group', { name: '卸载与吊销' }).getByRole('button', { name: '吊销专用密钥' }).click()
  await ssh.getByText(/专用密钥已吊销/).waitFor({ timeout: 60000 })
  check(await ssh.getByRole('group', { name: '卸载与吊销' }).count() === 0, '吊销后不再显示卸载与吊销')
  page.once('dialog', (d) => d.accept())
  await ssh.getByRole('form', { name: '部署' }).getByRole('button', { name: '删除服务器' }).click()
  await ssh.getByRole('form', { name: '目标服务器' }).waitFor()
  step('吊销专用密钥（服务器上的公钥与工作台的私钥都删除），再删除目标服务器，回到填写表单')

  // ---------------------------------------------------------------- 密码与流量
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }))
  check(!storage.includes(sshPassword), '浏览器存储里不应有登录密码')
  check(!(await page.content()).includes(sshPassword), '页面里不应有登录密码')
  check(bodies.every((b) => !b.includes(sshPassword)), '接口响应里不应有登录密码')
  check(posts.filter((b) => b.includes(sshPassword)).length === 1, '登录密码应只出现在开始部署的那一个请求里')
  check(posts.filter((b) => b.includes(password)).length <= 2, '工作台登录密码只应出现在登录与确认重启的请求里')
  const outside = requests.filter((url) => !url.startsWith(base) && !url.startsWith('data:') && !url.startsWith('blob:'))
  check(outside.length === 0, `浏览器只应访问工作台自己的地址：${outside.slice(0, 5).join(', ')}`)
  step(`安全：密码只随一个请求发出，不在页面、${bodies.length} 个接口响应与浏览器存储里；${requests.length} 个请求都指向工作台`)

  page.once('dialog', (d) => d.accept())
  await card.getByRole('button', { name: '停用' }).click()
  await card.getByText('已停用').waitFor()
  step('收尾：停用本次登记的实例')
  await browser.close()
  if (problems.length) throw new Error(`页面错误：${problems.join('; ')}`)
  console.log('\n全部通过（页面无错误）')
}

main().catch((error) => {
  console.error(`失败  ${error.message}`)
  process.exitCode = 1
}).finally(() => process.exit())
