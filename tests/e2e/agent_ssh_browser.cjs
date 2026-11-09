// 智能体服务器与 OpenClaw 部署的浏览器验收（T-0090；一台多实例 T-0148；两个独立页面与保存的登录密码 T-0183）：
// 真实后端 + 真实 sshd + 真实 OpenClaw——
// “智能体服务器”页点“添加服务器” → 只填 IP、端口、账号、密码（名称不填）→ “测试并保存” → 页面显示的指纹与 sshd 的一致，
// 勾选核对后“确认指纹并测试连接” → 连接正常 → 服务器详情“初始化服务器”，跟着每一步直到成功 → 旧链接 /agents?tab=servers&server=
// 换到新页 → “前往部署智能体”到 /agents?deploy=1&server_id= → 部署抽屉预选这台服务器、没有密码输入 → 填名称“开始部署”，
// 跟着真实步骤直到“部署完成，实例已连上工作台” → 智能体出现在列表、详情概览与服务器详情的“已部署 OpenClaw”里 →
// 实例运维（查看状态、立即上报、重启要再输一次**工作台**登录密码）→ 实例的负载与服务器详情的负载（份额、各智能体趋势、查看全部容器）→
// 卸载（删除这个实例的目录）→ 移除服务器 → 390px 宽度无横向滚动 →
// SSH 密码只随“添加服务器”那一个请求发出：不在页面、接口响应、浏览器存储里；浏览器只访问工作台自己的地址。
//
// 前提与 agent_ssh_acceptance.py 相同（CI 的 agents job 在它之后运行，复用同一个 sshd 与账号；那时账号已在 docker 组，
// 初始化的 Docker 一步会跳过）：
//   SSH_TARGET_HOST、SSH_TARGET_PORT、SSH_TARGET_USER、SSH_TARGET_PASSWORD、SSH_HOST_KEY_PUB（sshd 的主机公钥文件）；
//   Compose 全栈已启动并迁移，外网接入入口已配置。用户由同目录的 seed.py 写入（每次新的用户）；
//   不用 Compose 的本机环境可以先自己建好 owner，用 SSH_BROWSER_EMAIL / SSH_BROWSER_PASSWORD 传入。
// 本机的代理拦截 npm 的 TLS 时插件一步会失败：设 SSH_BROWSER_ALLOW_PLUGIN_FAILURE=1，部署停在插件一步也继续验收卸载与移除服务器。
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
const literal = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

// 轮询直到 test() 为真（页面数据按查询间隔刷新，不用固定等待）
async function until(page, test, timeout, reload = 0) {
  const end = Date.now() + timeout
  for (let i = 1; Date.now() < end; i++) {
    if (await test().catch(() => false)) return true
    await page.waitForTimeout(2000)
    if (reload && i % reload === 0) await page.reload()
  }
  return false
}
async function noHorizontalScroll(page) {
  await page.waitForTimeout(300)
  return !(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth))
}

// 跟着一次 SSH 任务（SshJob：初始化服务器 / 部署 / 卸载）直到结束，返回每一步的“文字=状态”
async function follow(page, region, kind, timeout) {
  const end = Date.now() + timeout
  const finished = region.getByText(new RegExp(`^${kind}：(成功|失败|已中断)$`))
  let last = ''
  while (Date.now() < end) {
    if (await finished.count()) break
    const current = await region.locator('li[data-status="running"]').first().textContent({ timeout: 1000 }).catch(() => '')
    if (current && current !== last) { console.log(`  … ${current.trim()}`); last = current }
    await page.waitForTimeout(3000)
  }
  check(await finished.count(), `${kind}没有在 ${timeout / 60000} 分钟内结束`)
  return region.locator('li').evaluateAll((items) => items.map((li) => `${li.textContent.trim()}=${li.dataset.status}`))
}

async function main() {
  const host = env('SSH_TARGET_HOST'), port = env('SSH_TARGET_PORT'), user = env('SSH_TARGET_USER')
  const sshPassword = env('SSH_TARGET_PASSWORD')
  const expected = execFileSync('ssh-keygen', ['-lf', env('SSH_HOST_KEY_PUB')], { encoding: 'utf8' }).split(/\s+/)[1]
  const serverName = host.includes(':') ? `[${host}]:${port}` : `${host}:${port}`
  let email = process.env.SSH_BROWSER_EMAIL, password = process.env.SSH_BROWSER_PASSWORD
  if (!email) {
    email = `ssh-ui-${run}@browser.example.com`; password = 'e2e ssh browser pass'
    execFileSync('python3', [path.join(__dirname, 'seed.py'), 'user', email, 'SSH 网页部署'], { encoding: 'utf8', env: { ...process.env, E2E_PASSWORD: password } })
  }

  const browser = await chromium.launch()
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  const problems = [], requests = [], bodies = [], writes = []
  page.on('pageerror', (error) => problems.push(error.message))
  // 401（登录前查询当前用户）、409（按钮在任务进行中被拒）、503 是预期的资源加载失败；移除服务器后它的详情可能再取一次得到 404
  page.on('console', (message) => { if (message.type() === 'error' && !/\b(401|404|409|503)\b/.test(message.text())) problems.push(message.text()) })
  page.on('request', (request) => {
    requests.push(request.url())
    if (request.method() !== 'GET') writes.push({ method: request.method(), url: request.url(), body: request.postData() ?? '' })
  })
  page.on('response', async (response) => {
    if (!response.url().startsWith(`${base}/api/`)) return
    try { bodies.push(await response.text()) } catch { /* 页面跳转后响应体已不可读 */ }
  })
  await page.goto(`${base}/login`)
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL(`${base}/`)
  check(await page.getByRole('link', { name: '智能体服务器', exact: true }).count() === 1 && await page.getByRole('link', { name: 'OpenClaw 智能体', exact: true }).count() === 1,
    '侧栏应有“智能体服务器”与“OpenClaw 智能体”两个入口')

  // ---------------------------------------------------------------- 智能体服务器页：添加服务器（只填四项）
  await page.getByRole('link', { name: '智能体服务器', exact: true }).click()
  await page.waitForURL(`${base}/agent-servers`)
  await page.getByRole('heading', { level: 1, name: '智能体服务器' }).waitFor()
  await page.getByRole('button', { name: '添加服务器', exact: true }).click()
  const add = page.getByRole('form', { name: '添加服务器' })
  await add.waitFor()
  check(await add.getByLabel('服务器名称（可选）').inputValue() === '', '名称是可选的，默认空')
  check(await add.getByLabel('SSH 端口 *').inputValue() === '22', 'SSH 端口默认 22')
  await add.getByLabel('IP 地址 *').fill(host)
  await add.getByLabel('SSH 端口 *').fill(port)
  await add.getByLabel('登录账号 *').fill(user)
  const field = add.getByLabel('登录密码 *')
  check(await field.getAttribute('type') === 'password', '登录密码输入框应是 password 类型')
  check(await add.getByRole('button', { name: '测试并保存' }).isDisabled(), '没填密码时不能保存')
  await field.fill(sshPassword)
  await add.getByRole('button', { name: '测试并保存' }).click()
  const verify = page.getByRole('group', { name: '核对主机指纹' })
  await verify.waitFor({ timeout: 60000 })
  await page.waitForURL(/\/agent-servers\?server=/)
  check(await add.count() === 0, '提交后表单（连同输入的密码）应收起')
  const serverId = new URL(page.url()).searchParams.get('server')
  check(serverId, '保存后应打开这台服务器的详情（地址栏带 server）')
  const shown = (await verify.getByTestId('ssh-fingerprint').textContent()).trim()
  check(shown === expected, `页面显示的指纹 ${shown} 应与 sshd 的主机公钥 ${expected} 一致`)
  check(await verify.getByText('ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub').isVisible(), '应给出在服务器上核对指纹的命令')
  const confirm = verify.getByRole('button', { name: '确认指纹并测试连接' })
  check(await confirm.isDisabled(), '勾选“已核对”之前不能确认指纹')
  step(`添加服务器只填 IP、端口、账号、密码（名称不填）：保存后只读取主机公钥，页面显示的 ${shown} 与 sshd 的一致，并给出核对命令`)
  await verify.getByLabel(/我已在服务器上核对/).check()
  await confirm.click()
  const result = page.getByRole('region', { name: '连接测试结果' })
  await result.getByText(/^(连接正常|密码被拒绝|连不上|主机身份变化|密码无法解密)$/).or(result.getByRole('alert')).first().waitFor({ timeout: 90000 })
  check(await result.getByText('连接正常', { exact: true }).count() === 1, `确认指纹后测试连接应成功：${await result.textContent()}`)
  await result.getByRole('button', { name: '完成' }).click()
  step('勾选核对后“确认指纹并测试连接”：用保存的密码登录，连接正常')

  // ---------------------------------------------------------------- 服务器详情：名称、连接状态，初始化
  const detail = page.getByRole('region', { name: `服务器：${serverName}` })
  await detail.waitFor()
  const list = page.getByRole('region', { name: '服务器列表' })
  const row = list.getByRole('row', { name: new RegExp(literal(serverName)) })
  await row.waitFor()
  check(await until(page, async () => /连接正常/.test(await row.textContent()) && /待初始化/.test(await row.textContent()), 60000),
    `列表里这台服务器应是“待初始化 / 连接正常”：${await row.textContent()}`)
  const panel = page.getByRole('region', { name: '连接与初始化' })
  check(/密码 · 已加密保存/.test(await panel.textContent()), '登录方式应显示“密码 · 已加密保存”')
  check(await panel.getByRole('button', { name: /专用密钥|吊销/ }).count() === 0, '不应再有专用密钥 / 吊销的按钮')
  step(`服务器名称默认是 ${serverName}；列表与详情显示连接正常、待初始化，登录方式是加密保存的密码`)

  await panel.getByRole('button', { name: '初始化服务器', exact: true }).click()
  const init = panel.getByRole('region', { name: '初始化服务器进度' })
  await init.waitFor({ timeout: 30000 })
  const initSteps = await follow(page, init, '初始化服务器', 15 * 60 * 1000)
  check(await init.getByText('服务器初始化完成：可以在这台服务器上部署智能体了。').count() === 1, `初始化应成功：${await init.getByRole('alert').textContent().catch(() => '')}\n${initSteps.join('\n')}`)
  check(initSteps.length === 6 && initSteps.every((s) => /=(done|skipped)$/.test(s)), `初始化的 6 步都应完成或跳过：${initSteps.join('; ')}`)
  check(/^用保存的密码登录服务器/.test(initSteps[0]) && !initSteps.some((s) => /专用公钥/.test(s)), `初始化用保存的密码登录、不装公钥：${initSteps.join('; ')}`)
  const go = detail.getByRole('link', { name: '前往部署智能体' })
  check(await until(page, async () => await go.count() === 1, 60000), '初始化后服务器详情应出现“前往部署智能体”')
  check(await go.getAttribute('href') === `/agents?deploy=1&server_id=${serverId}`, `“前往部署智能体”应带上这台服务器：${await go.getAttribute('href')}`)
  step(`服务器详情“初始化服务器”：页面跟着 ${initSteps.length} 步（登录、检测、Docker、重新登录、部署目录、验证）直到成功`)

  await page.goto(`${base}/agents?tab=servers&server=${serverId}`)
  await page.getByRole('region', { name: `服务器：${serverName}` }).waitFor()
  const moved = new URL(page.url())
  check(moved.pathname === '/agent-servers' && moved.searchParams.get('server') === serverId && !moved.searchParams.has('tab'), `旧链接应换到新的服务器页：${page.url()}`)
  step('旧链接 /agents?tab=servers&server= 换到 /agent-servers?server=（同一台服务器的详情）')

  // ---------------------------------------------------------------- OpenClaw 智能体页：选服务器部署（没有密码）
  await page.getByRole('link', { name: '前往部署智能体' }).click()
  await page.waitForURL(`${base}/agents`)
  await page.getByRole('heading', { level: 1, name: 'OpenClaw 智能体' }).waitFor()
  const deployForm = page.getByRole('form', { name: '部署 OpenClaw' })
  await deployForm.waitFor()
  const target = deployForm.getByLabel('部署服务器 *')
  check(await until(page, async () => await target.inputValue() === serverId, 60000), '部署抽屉应预选“前往部署”的那台服务器')
  check(await deployForm.locator('input[type="password"]').count() === 0 && await deployForm.getByLabel(/密码|IP|账号/).count() === 0, '部署抽屉不应索要 IP、账号或密码')
  check(await deployForm.getByText('平台推荐版本（已锁定）').count() === 1, '部署抽屉应显示锁定的 OpenClaw 版本')
  const name = `ssh-ui-${run}`
  await deployForm.getByLabel('智能体名称 *').fill(name)
  await deployForm.getByRole('button', { name: '开始部署' }).click()
  const progressRegion = page.getByRole('region', { name: '新智能体的部署' })
  await progressRegion.waitFor({ timeout: 30000 })
  const progress = progressRegion.getByRole('region', { name: '部署进度' })
  await progress.waitFor({ timeout: 30000 })
  step('“前往部署智能体”打开部署抽屉并预选这台服务器（没有 IP、账号、密码输入），填名称开始部署')

  const statuses = await follow(page, progress, '部署', INSTALL_TIMEOUT)
  const succeeded = await progress.getByText('部署完成，实例已连上工作台。').count() > 0
  await progressRegion.getByRole('button', { name: '查看智能体' }).click()
  await page.waitForURL(/\/agents\?instance=/)
  const instanceId = new URL(page.url()).searchParams.get('instance')
  const card = page.getByRole('article', { name: `实例：${name}` })
  await card.waitFor({ timeout: 30000 })
  const deployPanel = card.getByRole('region', { name: '部署到服务器' })
  if (!succeeded) {
    const reason = await progress.getByRole('alert').textContent().catch(() => '')
    const tolerated = process.env.SSH_BROWSER_ALLOW_PLUGIN_FAILURE === '1' && /plugin_failed/.test(reason)
    console.error(statuses.join('\n'))
    check(tolerated, `部署没有成功：${reason}`)
    check(await progress.locator('details pre').count() === 1, '失败时应能展开脱敏后的命令输出')
    step(`部署停在插件一步（本机代理拦截 npm，SSH_BROWSER_ALLOW_PLUGIN_FAILURE=1）：页面显示失败原因与脱敏日志：${reason.trim()}`)
  } else {
    check(statuses.length === 9 && statuses.every((s) => /=(done|skipped)$/.test(s)), `部署的每一步都应完成或跳过：${statuses.join('; ')}`)
    check(/^用保存的密码登录服务器/.test(statuses[0]) && !statuses.some((s) => /专用公钥|检测 \/ 安装 Docker/.test(s)), `部署只做环境预检、用保存的密码登录：${statuses.join('; ')}`)
    step(`页面跟着任务显示 ${statuses.length} 步直到“部署完成，实例已连上工作台”`)

    // ---------------------------------------------------------------- 列表、详情概览与服务器详情里都有它
    const overview = page.getByRole('region', { name: '智能体列表' })
    const listed = overview.getByRole('row', { name: new RegExp(literal(name)) })
    check(await until(page, async () => /部署成功/.test(await listed.textContent({ timeout: 1000 })), 60000), '智能体列表里应有它，部署任务“部署成功”')
    check(await listed.getByRole('link', { name: serverName }).count() === 1, '列表里它的所属服务器应链接到服务器页')
    const facts = page.getByRole('region', { name: '智能体概览' })
    check(/平台自动部署（Docker Compose）/.test(await facts.textContent()) && await facts.getByRole('link', { name: serverName }).count() >= 1, `详情概览应显示部署方式与所在服务器：${await facts.textContent()}`)
    const seen = card.locator('div', { has: page.getByText('最近在线', { exact: true }) }).locator('dd')
    check(await until(page, async () => !/从未连接/.test(await seen.first().textContent()), 180000, 10), '部署完成后实例卡片的“最近在线”应有时间')
    check(await deployPanel.getByRole('link', { name: serverName }).count() === 1, '实例卡片的“部署到服务器”显示所在服务器（链接到服务器页）')
    check(instanceId, '“查看智能体”应打开这个智能体的详情')
    step('智能体出现在列表（所属服务器、部署成功），详情概览显示平台自动部署与所在服务器，卡片显示最近在线')

    // ---------------------------------------------------------------- 实例的运维（T-0114）：二次认证用工作台登录密码
    const ops = card.getByRole('region', { name: '运维', exact: true })
    const finish = async (scope, label) => {
      const resultCard = scope.getByRole('article', { name: `运维结果：${label}` })
      await resultCard.getByText(/^(成功|失败|已中断)$/).waitFor({ timeout: 240000 })
      return resultCard
    }
    await ops.getByRole('button', { name: '查看状态' }).click()
    let outcome = await finish(ops, '查看状态')
    let output = await outcome.getByLabel(/输出/).textContent()
    check(await outcome.getByText('成功', { exact: true }).count() === 1 && /SERVICE/.test(output) && /openclaw/.test(output), `查看状态应成功并显示 docker compose ps：${output.slice(-400)}`)
    check(!/mwa_(?!\*\*\*)[A-Za-z0-9]/.test(output), '输出里不应有未脱敏的实例凭证')
    await ops.getByRole('button', { name: '立即上报一次' }).click()
    outcome = await finish(ops, '立即上报一次')
    check(await outcome.getByText('成功', { exact: true }).count() === 1, '立即上报一次应成功')
    await ops.getByRole('button', { name: '重启 OpenClaw' }).click()
    const confirmForm = ops.getByRole('form', { name: '确认：重启 OpenClaw' })
    const secret = confirmForm.getByLabel('你的登录密码')
    check(await secret.getAttribute('type') === 'password', '确认密码的输入框应是 password 类型')
    check(await confirmForm.getByRole('button', { name: '确认执行' }).isDisabled(), '没填密码时不能确认')
    await secret.fill(password)
    await confirmForm.getByRole('button', { name: '确认执行' }).click()
    outcome = await finish(ops, '重启 OpenClaw')
    check(await outcome.getByText('成功', { exact: true }).count() === 1, `重启 OpenClaw 应成功：${await outcome.textContent()}`)
    check(await ops.getByRole('button', { name: '清理无用镜像' }).count() === 0, '清理镜像在服务器页，不在实例卡片上')
    step('实例运维：查看状态、立即上报一次；重启 OpenClaw 先输一次自己的工作台登录密码，执行成功')

    // ---------------------------------------------------------------- 实例的负载（它自己的与所在服务器的）
    const loadPanel = card.getByRole('region', { name: '负载', exact: true })
    await loadPanel.getByRole('button', { name: '查看负载' }).click()
    const cpu = loadPanel.getByRole('region', { name: 'CPU', exact: true })
    await cpu.waitFor({ timeout: 180000 })
    check(/核/.test(await cpu.textContent()), '负载应显示所在服务器的 CPU 核心数')
    check(await loadPanel.getByRole('region', { name: '它的内存' }).count() === 1, '负载应显示这个智能体自己的内存')
    check(await loadPanel.getByText('CPU 使用率', { exact: true }).count() === 1 && await loadPanel.getByText('它的 CPU（占整机）', { exact: true }).count() === 1, '应有整机与这个智能体自己的 CPU 趋势图')
    step('实例的负载：它自己的 CPU / 内存与所在服务器的当前值、趋势')

    // ---------------------------------------------------------------- 服务器详情：已部署 OpenClaw、负载、服务器运维
    await deployPanel.getByRole('link', { name: serverName }).click()
    await page.waitForURL(new RegExp(`/agent-servers\\?server=${serverId}`))
    const deployed = page.getByRole('region', { name: '已部署 OpenClaw' })
    const deployedRow = deployed.getByRole('row', { name: new RegExp(literal(name)) })
    await deployedRow.waitFor({ timeout: 60000 })
    const deployedText = await deployedRow.textContent()
    check(/已部署/.test(deployedText) && /myworkspace-openclaw-/.test(deployedText), `“已部署 OpenClaw”里应有它（已部署、Compose 项目名）：${deployedText}`)
    check(await deployedRow.getByRole('link', { name: `查看智能体：${name}` }).getAttribute('href') === `/agents?instance=${instanceId}`, '“查看智能体”应链接到它的详情')
    check(/平台部署记录 1 条 · 部署成功 1/.test(await deployed.textContent()), `服务器详情应统计 1 条部署记录、1 个部署成功：${await deployed.textContent()}`)
    const whole = page.getByRole('region', { name: '整机负载' })
    check(await whole.getByText('CPU：每个智能体占多少').count() === 1 && await whole.getByText('内存：每个智能体占多少').count() === 1, '服务器详情应有每个智能体占整机的 CPU、内存份额')
    const loads = page.getByRole('region', { name: '各智能体负载' })
    await loads.getByRole('row', { name: new RegExp(literal(name)) }).getByRole('button', { name: '查看趋势' }).click()
    await page.getByRole('region', { name: `${name} 的趋势` }).waitFor()
    const serverOps = page.getByRole('region', { name: '服务器运维' })
    await serverOps.getByRole('button', { name: '查看全部容器' }).click()
    outcome = await finish(serverOps, '查看全部容器')
    output = await outcome.getByLabel(/输出/).textContent()
    check(await outcome.getByText('成功', { exact: true }).count() === 1 && /myworkspace-openclaw-/.test(output), `查看全部容器应列出这个实例的 Compose 项目：${output.slice(-400)}`)
    await page.setViewportSize({ width: 390, height: 844 })
    check(await noHorizontalScroll(page), '390px 宽度下服务器详情（表格在自己的滚动区里）不应出现横向滚动')
    await page.setViewportSize({ width: 1440, height: 1000 })
    step('服务器详情：“已部署 OpenClaw”里有它（已部署、项目名、链接到智能体），整机份额与各智能体趋势，查看全部容器；390px 无横向滚动')
  }

  // ---------------------------------------------------------------- 卸载（删除目录）、移除服务器
  await page.goto(`${base}/agents?instance=${instanceId}`)
  const undeploy = deployPanel.getByRole('group', { name: '卸载' })
  await undeploy.waitFor({ timeout: 30000 })
  await undeploy.getByLabel(/同时删除这个实例的目录/).check()
  page.once('dialog', (d) => d.accept())
  await undeploy.getByRole('button', { name: '从服务器上卸载' }).click()
  const removal = deployPanel.getByRole('region', { name: '卸载进度' })
  await removal.waitFor({ timeout: 30000 })
  await follow(page, removal, '卸载', 10 * 60 * 1000)
  check(await removal.getByText('卸载完成。').count() === 1, `卸载应成功：${await removal.getByRole('alert').textContent().catch(() => '')}`)
  step('卸载（同时删除这个实例的目录）：页面显示每一步直到卸载完成')

  await page.goto(`${base}/agent-servers?server=${serverId}`)
  const manage = page.getByRole('region', { name: '连接与初始化' })
  const remove = manage.getByRole('button', { name: '移除服务器' })
  check(await until(page, async () => await remove.isEnabled(), 60000), '卸载后（没有部署记录）应能移除服务器')
  page.once('dialog', (d) => d.accept())
  await remove.click()
  await page.waitForURL(`${base}/agent-servers`)
  check(await until(page, async () => await page.getByRole('region', { name: '服务器列表' }).getByRole('row', { name: new RegExp(literal(serverName)) }).count() === 0, 30000),
    '移除后服务器列表里没有它了')
  step('移除服务器（不删容器、不改 sshd），回到服务器列表')

  // ---------------------------------------------------------------- 两个页面在 390px 宽度下
  await page.setViewportSize({ width: 390, height: 844 })
  for (const route of ['/agent-servers', '/agents']) {
    await page.goto(`${base}${route}`)
    await page.getByRole('heading', { level: 1 }).waitFor()
    await page.waitForLoadState('networkidle')
    check(await noHorizontalScroll(page), `${route} 在 390px 宽度下不应出现横向滚动`)
  }
  await page.setViewportSize({ width: 1440, height: 1000 })
  step('“智能体服务器”与“OpenClaw 智能体”两页在 390px 宽度下无横向滚动')

  // ---------------------------------------------------------------- 密码与流量
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }))
  check(!storage.includes(sshPassword), '浏览器存储里不应有 SSH 密码')
  check(!(await page.content()).includes(sshPassword), '页面里不应有 SSH 密码')
  check(bodies.every((b) => !b.includes(sshPassword)), '接口响应里不应有 SSH 密码')
  check(bodies.every((b) => !b.includes('login_password_ref')), '接口响应里不应有密码的密文引用字段')
  const carrying = writes.filter((w) => w.body.includes(sshPassword))
  check(carrying.length === 1 && carrying[0].method === 'POST' && carrying[0].url === `${base}/api/v1/agents/servers`,
    `SSH 密码应只随“添加服务器”那一个请求发出：${carrying.map((w) => `${w.method} ${w.url}`).join(', ')}`)
  check(writes.filter((w) => w.body.includes(password)).length <= 2, '工作台登录密码只应出现在登录与确认重启的请求里')
  const outside = requests.filter((url) => !url.startsWith(base) && !url.startsWith('data:') && !url.startsWith('blob:'))
  check(outside.length === 0, `浏览器只应访问工作台自己的地址：${outside.slice(0, 5).join(', ')}`)
  step(`安全：SSH 密码只随“添加服务器”一个请求发出，不在页面、${bodies.length} 个接口响应与浏览器存储里；${requests.length} 个请求都指向工作台`)

  await page.goto(`${base}/agents?instance=${instanceId}`)
  page.once('dialog', (d) => d.accept())
  await card.getByRole('button', { name: '停用' }).click()
  await card.getByText('已停用').first().waitFor()
  step('收尾：停用本次部署的实例')
  await browser.close()
  if (problems.length) throw new Error(`页面错误：${problems.join('; ')}`)
  console.log('\n全部通过（页面无错误）')
}

main().catch((error) => {
  console.error(`失败  ${error.message}`)
  process.exitCode = 1
}).finally(() => process.exit())
