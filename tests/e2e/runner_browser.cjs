// 容器服务器页面（原“运行服务器”，地址仍是 /runner）的浏览器验收（T-0141；页面 T-0140，改版 T-0186 / T-0187、验收 T-0188，SSH 部署 T-0136）：
// 真实后端 + 真实 sshd + 真实 Docker / gVisor——
// 没有服务器时引导去“服务器设置” → 添加容器服务器（外网入口）→ “通过 SSH 部署”填写服务器 → 页面显示的指纹与 sshd 的主机公钥一致，
// 勾选核对后才能确认 → 输入一次登录密码开始部署（输入框随即清空）→ 页面跟着任务显示每一步，直到“部署完成，容器服务器已连上工作台” →
// 容器总览的主机条显示在线、gVisor 可用、四个统计卡 → 旧链接 ?tab=environments 换到容器总览、详情直链遇到不存在的容器如实提示 →
// 智能体用量、环境模板、执行记录、服务器设置几个标签都能打开，地址里记着标签 → 390px 宽度下无横向滚动 →
// 卸载（同时删除接入包目录，sudo 再填一次密码）→ 吊销专用密钥 → 删除 SSH 地址 → 移除容器服务器 →
// 密码只出现在开始部署与卸载的两个请求里：不在页面、接口响应、浏览器存储里；浏览器只访问工作台自己的地址。
//
// 前提与 agent_ssh_browser.cjs 相同（CI 的 agents job 在它之后运行，复用同一个 sshd 与账号，外网接入入口已自动配置）：
//   SSH_TARGET_HOST、SSH_TARGET_PORT、SSH_TARGET_USER、SSH_TARGET_PASSWORD、SSH_HOST_KEY_PUB（sshd 的主机公钥文件）；
//   这台机器上 Docker 已认得 runsc（CI 里提前装好 gVisor）。用户由同目录的 seed.py 写入（每次新的用户）；
//   不用 Compose 的本机环境可以先自己建好 owner，用 RUNNER_BROWSER_EMAIL / RUNNER_BROWSER_PASSWORD 传入。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定）：
//   node runner_browser.cjs [BASE_URL]    # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const path = require('node:path')
const { chromium } = require('playwright')

const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const run = randomBytes(4).toString('hex')
const INSTALL_TIMEOUT = 15 * 60 * 1000

function env(name) {
  const value = process.env[name]
  if (!value) throw new Error(`缺少环境变量 ${name}`)
  return value
}
function check(condition, message) { if (!condition) throw new Error(message) }
function step(text) { console.log(`通过  ${text}`) }
// 页面是单页应用：跳转、刷新后要等它渲染出来再判断，不能只看“此刻”的状态
async function appears(locator, message, timeout = 30000) {
  await locator.waitFor({ timeout }).catch(() => { throw new Error(message) })
}

async function noHorizontalScroll(page, what) {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(300)
  check(!(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)), `390px 宽度下${what}不应出现横向滚动`)
  await page.setViewportSize({ width: 1440, height: 1000 })
}

async function waitJob(region, kind, timeout) {
  const deadline = Date.now() + timeout
  let last = ''
  while (Date.now() < deadline) {
    if (await region.getByText(new RegExp(`^${kind}：(成功|失败|已中断)$`)).count()) break
    const current = await region.locator('li[data-status="running"]').first().textContent({ timeout: 1000 }).catch(() => '')
    if (current && current !== last) { console.log(`  … ${current.trim()}`); last = current }
    await region.page().waitForTimeout(3000)
  }
  return region.locator('li').evaluateAll((items) => items.map((li) => `${li.textContent.trim()}=${li.dataset.status}`))
}

async function main() {
  const host = env('SSH_TARGET_HOST'), port = env('SSH_TARGET_PORT'), user = env('SSH_TARGET_USER')
  const sshPassword = env('SSH_TARGET_PASSWORD')
  const expected = execFileSync('ssh-keygen', ['-lf', env('SSH_HOST_KEY_PUB')], { encoding: 'utf8' }).split(/\s+/)[1]
  let email = process.env.RUNNER_BROWSER_EMAIL, password = process.env.RUNNER_BROWSER_PASSWORD
  if (!email) {
    email = `runner-ui-${run}@browser.example.com`; password = 'e2e runner browser pass'
    execFileSync('python3', [path.join(__dirname, 'seed.py'), 'user', email, '运行服务器网页验收'], { encoding: 'utf8', env: { ...process.env, E2E_PASSWORD: password } })
  }

  const browser = await chromium.launch()
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  const problems = [], requests = [], bodies = [], posts = []
  page.on('pageerror', (error) => problems.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error' && !/\b(401|404|409|503)\b/.test(message.text())) problems.push(message.text()) })
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

  // ---------------------------------------------------------------- 没有容器服务器 → 服务器设置里添加（外网入口）
  await page.goto(`${base}/runner`)
  await appears(page.getByRole('heading', { level: 1, name: '容器服务器' }), '页面标题应是“容器服务器”（地址仍是 /runner）')
  const empty = page.getByRole('region', { name: '还没有容器服务器' })
  await empty.waitFor()
  await empty.getByRole('button', { name: '去添加容器服务器' }).click()
  await page.waitForURL(/\/runner\?tab=settings$/)
  await appears(page.getByRole('tab', { name: '服务器设置', selected: true }), '“去添加容器服务器”应切到服务器设置标签')
  const add = page.getByRole('form', { name: '添加容器服务器' })
  await add.getByLabel('名称').fill(`runner-ui-${run}`)
  await add.getByLabel('连工作台的入口').selectOption('public')
  await add.getByRole('button', { name: '添加' }).click()
  const ssh = page.getByRole('region', { name: '通过 SSH 部署' })
  await ssh.waitFor()
  step('容器服务器页没有服务器时引导到“服务器设置”（地址里记着 ?tab=settings），添加容器服务器（外网入口），出现“通过 SSH 部署”')

  // ---------------------------------------------------------------- 填写服务器，核对指纹
  const form = ssh.getByRole('form', { name: '容器服务器的 SSH 地址' })
  await form.getByLabel('服务器 IP 或域名 *').fill(host)
  await form.getByLabel('SSH 端口 *').fill(port)
  await form.getByLabel('SSH 账号 *').fill(user)
  await form.getByRole('button', { name: '读取主机公钥' }).click()
  const verify = ssh.getByRole('group', { name: '核对主机公钥' })
  await verify.waitFor({ timeout: 60000 })
  const shown = (await verify.getByTestId('runner-ssh-fingerprint').textContent()).trim()
  check(shown === expected, `页面显示的指纹 ${shown} 应与 sshd 的主机公钥 ${expected} 一致`)
  check(await verify.getByText('ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub').isVisible(), '应给出在服务器上核对指纹的命令')
  const confirm = verify.getByRole('button', { name: '确认指纹' })
  check(await confirm.isDisabled(), '勾选“已核对”之前不能确认指纹')
  await noHorizontalScroll(page, '核对指纹时')
  await verify.getByLabel(/我已在服务器上核对/).check()
  await confirm.click()
  const deploy = ssh.getByRole('form', { name: '部署容器服务器' })
  await deploy.waitFor()
  step(`读取主机公钥（不登录）：页面显示的 ${shown} 与 sshd 的一致；勾选核对后确认指纹；390px 宽度无横向滚动`)

  // ---------------------------------------------------------------- 输入一次密码，跟着进度到上线
  const field = deploy.getByLabel('登录密码 *')
  check(await field.getAttribute('type') === 'password', '密码输入框应是 password 类型')
  check(await deploy.getByRole('button', { name: '开始部署' }).isDisabled(), '没填密码时不能开始第一次部署')
  await field.fill(sshPassword)
  await deploy.getByRole('button', { name: '开始部署' }).click()
  const progress = ssh.getByRole('region', { name: '部署进度' })
  await progress.waitFor({ timeout: 30000 })
  check(await deploy.count() === 0 || await field.inputValue() === '', '提交后密码输入框应清空')
  const statuses = await waitJob(progress, '部署', INSTALL_TIMEOUT)
  if (!(await progress.getByText('部署完成，容器服务器已连上工作台。').count())) {
    console.error(statuses.join('\n'))
    console.error(await progress.locator('details pre').textContent().catch(() => ''))
    throw new Error(`部署没有成功：${await progress.getByRole('alert').textContent().catch(() => '')}`)
  }
  check(statuses.every((s) => /=(done|skipped)$/.test(s)), `每一步都应完成或跳过：${statuses.join('; ')}`)
  step(`输入一次密码开始部署；页面跟着任务显示 ${statuses.length} 步（连接、核对、专用密钥、上传接入包、安装脚本、上线）直到“部署完成，容器服务器已连上工作台”`)

  // ---------------------------------------------------------------- 容器总览与各标签（T-0186 / T-0187）
  await page.getByRole('tab', { name: '容器总览' }).click()
  await page.waitForURL(`${base}/runner`)
  const strip = page.getByRole('region', { name: '容器服务器主机' })
  await strip.getByText('在线', { exact: true }).waitFor({ timeout: 60000 })
  const facts = await strip.textContent()
  check(/gVisor 可用/.test(facts), `主机条应显示 gVisor 可用：${facts.slice(0, 300)}`)
  check(/最近联系/.test(facts), `主机条应显示最近联系时间：${facts.slice(0, 300)}`)
  const stats = page.getByRole('region', { name: '容器统计' })
  await stats.getByText('容器环境总数').waitFor({ timeout: 30000 })
  const cards = await stats.textContent()
  check(/容器环境总数/.test(cards) && /需关注/.test(cards), `应显示四个统计卡：${cards.slice(0, 200)}`)
  await page.getByRole('region', { name: '容器列表' }).getByText('还没有容器环境').waitFor({ timeout: 30000 })
  await noHorizontalScroll(page, '容器总览')
  step('容器总览：主机条显示在线、gVisor 可用与最近联系；四个统计卡；还没有容器时如实说明；390px 宽度无横向滚动')

  // 旧链接 ?tab=environments（原来的“环境”标签、旧通知）换到容器总览，其余参数保留
  await page.goto(`${base}/runner?tab=environments&state=running`)
  await page.waitForURL(`${base}/runner?state=running`)
  await appears(page.getByRole('tab', { name: '容器总览', selected: true }), '旧链接应打开容器总览')
  // 详情直链：不存在（或已无权访问）的容器显示提示，不留旧内容
  await page.goto(`${base}/runner?environment_id=00000000-0000-4000-8000-000000000000`)
  await page.getByText('这个容器不存在，或你已无权访问。').waitFor({ timeout: 30000 })
  step('旧链接 ?tab=environments 换到容器总览并保留筛选；详情直链遇到不存在的容器显示“不存在或已无权访问”')

  for (const [tab, key, probe] of [['智能体用量', 'instances', '智能体用量'], ['环境模板', 'templates', null], ['执行记录', 'runs', '筛选运行记录']]) {
    await page.getByRole('tab', { name: tab }).click()
    await page.waitForURL(new RegExp(`\\?tab=${key}$`))
    if (probe) await page.getByRole('region', { name: probe }).waitFor()
    else await page.getByRole('tabpanel', { name: tab }).waitFor()
    await page.getByText('正在加载').first().waitFor({ state: 'detached', timeout: 30000 }).catch(() => {})
    check(!(await page.getByRole('alert').filter({ hasText: '加载失败' }).count()), `${tab}标签不应加载失败`)
    await noHorizontalScroll(page, `${tab}标签`)
  }
  await page.reload()
  await appears(page.getByRole('tab', { name: '执行记录', selected: true }), '刷新后仍停在地址里记着的标签')
  step('智能体用量、环境模板、执行记录标签都能打开（地址里记着 ?tab=，刷新后不变），没有加载失败；390px 宽度无横向滚动')

  await page.getByRole('tab', { name: '服务器设置' }).click()
  const policy = page.getByRole('form', { name: '回收策略' })
  await policy.waitFor()
  await policy.getByRole('button', { name: '保存回收策略' }).click()
  await page.getByRole('status').filter({ hasText: /已保存/ }).first().waitFor({ timeout: 30000 })
  step('服务器设置：回收策略显示生效值，保存成功')

  // ---------------------------------------------------------------- 卸载、吊销、删除 SSH 地址、移除容器服务器
  const manage = ssh.getByRole('group', { name: '卸载与吊销' })
  await manage.getByLabel(/卸载时同时删除接入包目录/).check()
  // 不是 root 账号：卸载要用 sudo，在部署表单里再填一次密码
  await ssh.getByRole('form', { name: '部署容器服务器' }).getByLabel(/登录密码/).fill(sshPassword)
  page.once('dialog', (d) => d.accept())
  await manage.getByRole('button', { name: '卸载 runner-agent' }).click()
  const removal = ssh.getByRole('region', { name: '卸载进度' })
  await removal.waitFor({ timeout: 30000 })
  await waitJob(removal, '卸载', 10 * 60 * 1000)
  check(await removal.getByText('卸载完成。').count() === 1, `卸载应成功：${await removal.getByRole('alert').textContent().catch(() => '')}`)
  const left = execFileSync('docker', ['ps', '-aq', '--filter', 'name=^myworkspace-runner-agent$'], { encoding: 'utf8' }).trim()
  check(left === '', 'runner-agent 容器应已停止并删除')
  step('卸载（同时删除接入包目录，sudo 再填一次密码）：页面显示每一步直到卸载完成，runner-agent 容器已删除')

  page.once('dialog', (d) => d.accept())
  await ssh.getByRole('group', { name: '卸载与吊销' }).getByRole('button', { name: '吊销专用密钥' }).click()
  await page.getByText(/专用密钥已吊销/).waitFor({ timeout: 60000 })
  check(await ssh.getByRole('group', { name: '卸载与吊销' }).count() === 0, '吊销后不再显示卸载与吊销')
  page.once('dialog', (d) => d.accept())
  await ssh.getByRole('form', { name: '部署容器服务器' }).getByRole('button', { name: '删除' }).click()
  await ssh.getByRole('form', { name: '容器服务器的 SSH 地址' }).waitFor()
  page.once('dialog', (d) => d.accept())
  await page.getByRole('button', { name: '移除容器服务器' }).click()
  await page.getByRole('form', { name: '添加容器服务器' }).waitFor({ timeout: 30000 })
  step('吊销专用密钥、删除 SSH 地址、移除容器服务器，回到“添加容器服务器”')

  // ---------------------------------------------------------------- 密码与流量
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }))
  check(!storage.includes(sshPassword), '浏览器存储里不应有登录密码')
  check(!(await page.content()).includes(sshPassword), '页面里不应有登录密码')
  check(bodies.every((b) => !b.includes(sshPassword)), '接口响应里不应有登录密码')
  check(posts.filter((b) => b.includes(sshPassword)).length === 2, '登录密码应只出现在开始部署与卸载的两个请求里')
  const outside = requests.filter((url) => !url.startsWith(base) && !url.startsWith('data:') && !url.startsWith('blob:'))
  check(outside.length === 0, `浏览器只应访问工作台自己的地址：${outside.slice(0, 5).join(', ')}`)
  step(`安全：密码只随两个请求发出，不在页面、${bodies.length} 个接口响应与浏览器存储里；${requests.length} 个请求都指向工作台`)

  await browser.close()
  if (problems.length) throw new Error(`页面错误：${problems.join('; ')}`)
  console.log('\n全部通过（页面无错误）')
}

main().catch((error) => {
  console.error(`失败  ${error.message}`)
  process.exitCode = 1
}).finally(() => process.exit())
