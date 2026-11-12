// 图片存储设置浏览器验收（T-0223）：真实后端 + 真实前端，不出站。
//
// owner 打开“设置 → 图片存储”（侧栏“管理”分组）→ 默认本机、不复制 → 选 R2 但没填完整：拒绝、不保存 →
// 账户 ID 填成别的主机名：拒绝、焦点回到账户 ID → 主存储留在本机，填好 R2 的连接信息（没选它，不会去连 R2）：
// 保存前对本机做自检、通过才保存 → 刷新后连接信息还在、密钥只显示“已设置”、页面与接口都没有密钥 →
// 运维页的存储状态显示本机与这次自检的结果、有到设置页的链接 → 清空 R2 设置 → admin 只读、PUT 403 →
// member 看不到、GET 403 → 切到另一个工作空间（owner 在那里是 admin）显示那个空间自己的设置 →
// 手机宽度不横向滚动 → 暗色主题。
// R2 / OSS 的真实连接（官方域名、签名、读写删）由后端测试（假的 S3 服务与 CI 里的 Garage）覆盖。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate）。
// 用法：node storage_browser.cjs [BASE_URL]（默认 http://127.0.0.1:${HTTP_PORT:-8080}）；SCREENSHOT_DIR 设了时保存截图。
// 不要求空库：每次新建工作台用户。
const { execFileSync } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const password = 'e2e storage browser pass'
const run = randomBytes(4).toString('hex')
const shots = process.env.SCREENSHOT_DIR || ''
// 假的连接信息（没选 R2，后端不会拿它去连），不是真实凭证
const ACCOUNT = `0123456789abcdef0123456789ab${run}`
const SECRET = `e2e-r2-secret-${run}`

function seed(...argv) {
  return execFileSync('python3', [path.join(__dirname, 'seed.py'), ...argv], {
    encoding: 'utf8', env: { ...process.env, E2E_PASSWORD: password },
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
    return { status: response.status, text: await response.text() }
  }, { method, pathname, body, workspace })
}
function watch(page, problems, outbound) {
  page.on('pageerror', (error) => problems.push(error.message))
  page.on('request', (request) => {
    // 浏览器只和工作台说话：不直连 R2 / OSS
    if (!request.url().startsWith(base) && !request.url().startsWith('data:')) outbound.push(request.url())
  })
  page.on('response', (response) => {
    if (response.status() >= 500) problems.push(`${response.status()} ${response.request().method()} ${new URL(response.url()).pathname}`)
  })
  page.on('console', (message) => {
    // 预期内的拒绝：member / admin 的 403、校验的 422
    if (message.type() === 'error' && !/status of (401|403|409|422)\b|Failed to load resource/.test(message.text())) problems.push(message.text())
  })
  page.on('dialog', (dialog) => { problems.push(`不应弹出浏览器对话框：${dialog.message()}`); void dialog.dismiss() })
}
async function login(browser, email, problems, outbound, options = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', ...options })
  const page = await context.newPage()
  watch(page, problems, outbound)
  await page.goto(`${base}/login`)
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL(`${base}/`)
  return { context, page }
}
// seed.py 给每个用户都建了自己的空间：打开页面后切到要验收的工作空间
async function openAs(page, workspace, target) {
  await page.goto(`${base}${target}`)
  await page.getByRole('heading', { level: 1 }).first().waitFor()
  const select = page.getByLabel('当前工作空间')
  if (await select.count() === 0) return
  await select.waitFor({ state: 'attached' })
  if ((await select.inputValue()) === workspace) return
  if (await select.isVisible()) await select.selectOption(workspace)
  else await select.evaluate((element, value) => { element.value = value; element.dispatchEvent(new Event('change', { bubbles: true })) }, workspace)
}
async function shot(page, name) {
  if (!shots) return
  fs.mkdirSync(shots, { recursive: true })
  await page.screenshot({ path: path.join(shots, `${name}.png`), fullPage: true })
}
const form = (page) => page.getByRole('form', { name: '图片存储设置' })
const r2 = (page) => page.getByRole('region', { name: 'Cloudflare R2 连接' })
// 主存储是三张可选卡片（单选框藏在卡片里）：点卡片
const choose = (page, name) => form(page).locator('label.st-choice').filter({ hasText: name }).click()
const saveButton = (page) => page.getByRole('button', { name: '检查并保存' })
const alertWith = (page, text) => page.getByRole('alert').filter({ hasText: text })

;(async () => {
  const problems = [], outbound = []
  const ownerEmail = `st-owner-${run}@browser.example.com`, adminEmail = `st-admin-${run}@browser.example.com`, memberEmail = `st-member-${run}@browser.example.com`
  const [ownerId, workspace] = seed('user', ownerEmail, '存储验收')
  const [adminId] = seed('user', adminEmail, '存储管理员')
  const [memberId, memberSpace] = seed('user', memberEmail, '存储成员')
  seed('member', workspace, adminId, 'admin')
  seed('member', workspace, memberId, 'member')
  // 切换工作空间用：owner 在成员自己的空间里是 admin
  seed('member', memberSpace, ownerId, 'admin')
  const browser = await chromium.launch()
  try {
    // ---------------------------------------------------------------- 默认值与侧栏入口
    const { page } = await login(browser, ownerEmail, problems, outbound)
    await openAs(page, workspace, '/settings/storage')
    await page.getByRole('heading', { level: 1, name: '图片存储' }).waitFor()
    await form(page).waitFor()
    const nav = page.getByRole('navigation', { name: '主导航' })
    check(await nav.locator('a[href="/settings/storage"]').count() === 1, '侧栏没有“图片存储”')
    check(await page.getByRole('radio', { name: /本机/ }).isChecked(), '默认主存储应是本机')
    check((await page.getByLabel('副本').inputValue()) === 'none', '默认不复制')
    check(await r2(page).count() === 0, '没选、没保存过的 R2 不应显示连接信息')
    check(await saveButton(page).isDisabled(), '没改动时不能保存')
    step('owner 打开“设置 → 图片存储”：默认本机、不复制，侧栏“管理”里有入口')

    // ---------------------------------------------------------------- 选了 R2 没填完整：拒绝
    await choose(page, 'Cloudflare R2')
    await r2(page).waitFor()
    // 回归：点选主存储、出现 R2 一栏后，“存储位置”一栏不能塌掉（曾因 fieldset 里的容器查询高度变成 0）
    check(await page.getByRole('radio', { name: /本机/ }).locator('xpath=..').isVisible(), '点选后“存储位置”一栏应仍然可见')
    await saveButton(page).click()
    await alertWith(page, '选了 R2 就要把它的连接信息填完整').waitFor()
    let current = JSON.parse((await api(page, workspace, 'GET', '/api/v1/settings/storage')).text)
    check(current.version === 0 && current.primary === 'local', `没填完整不应保存：${JSON.stringify(current)}`)
    step('选了 R2 但没填完整：提示填完整，设置不变')

    // ---------------------------------------------------------------- 账户 ID 填成主机名：拒绝、焦点回到输入框
    await page.getByLabel('账户 ID').fill('evil.example.com/x')
    await page.getByLabel('桶名').fill('kb-images')
    await page.getByLabel('Access Key ID').fill('AKE2E')
    await page.getByLabel('Secret Access Key').fill(SECRET)
    await saveButton(page).click()
    await alertWith(page, '账户 ID 只能是字母和数字').waitFor()
    await page.waitForFunction(() => document.activeElement?.id === 'storage-r2-account_id')
    check((await page.getByLabel('Secret Access Key').inputValue()) === '', '提交后应清空密钥输入')
    step('账户 ID 填成别的主机名：拒绝（地址只能是 R2 的官方域名），焦点回到账户 ID，密钥输入已清空')

    // ---------------------------------------------------------------- 主存储留在本机，保存 R2 的连接信息：自检本机后保存
    await choose(page, '本机')
    await page.getByLabel('账户 ID').fill(ACCOUNT.toUpperCase())
    await page.getByLabel('辖区（可不填）').fill('eu')
    await page.getByLabel('Secret Access Key').fill(SECRET)
    await r2(page).getByText(`https://${ACCOUNT}.eu.r2.cloudflarestorage.com/kb-images`).waitFor()
    await saveButton(page).click()
    const results = page.getByRole('region', { name: '自检结果' })
    await results.getByText('自检通过').waitFor()
    await results.getByText('本机', { exact: true }).waitFor()
    await page.getByRole('status').filter({ hasText: '已保存：自检通过' }).waitFor()
    const saved = await api(page, workspace, 'GET', '/api/v1/settings/storage')
    check(!saved.text.includes(SECRET), '接口不应返回密钥')
    current = JSON.parse(saved.text)
    check(current.version === 1 && current.primary === 'local' && current.r2.secret_set === true, `保存结果不对：${saved.text}`)
    check(current.r2.account_id === ACCOUNT && current.r2.jurisdiction === 'eu', `账户 ID 与辖区应转成小写：${saved.text}`)
    await shot(page, 'storage-settings')
    step('主存储本机 + 填好 R2 的连接信息：保存前自检本机通过才保存，账户 ID 转成小写')

    await page.reload()
    await form(page).waitFor()
    check((await page.getByLabel('账户 ID').inputValue()) === ACCOUNT, '刷新后账户 ID 应保留')
    check((await page.getByLabel('Secret Access Key').getAttribute('placeholder')) === '已设置，不改就留空', '密钥应只显示“已设置”')
    check((await page.getByLabel('Secret Access Key').inputValue()) === '', '密钥不回显')
    check(!(await page.content()).includes(SECRET), '页面里不应有密钥')
    await page.getByText(/上次修改：/).waitFor()
    step('刷新后连接信息还在，密钥只显示“已设置”，页面里没有密钥')

    // ---------------------------------------------------------------- 运维页的存储状态
    await openAs(page, workspace, '/operations')
    const panel = page.getByRole('region', { name: '存储状态' })
    await panel.getByText('本机（卷 kb-files）').waitFor()
    await panel.getByText(/自检通过/).first().waitFor()
    check(!(await panel.textContent()).includes('FILE_STORAGE'), '运维页不应再提 .env 的 FILE_STORAGE')
    await panel.getByRole('link', { name: '设置 → 图片存储' }).first().click()
    await page.waitForURL(`${base}/settings/storage`)
    await form(page).waitFor()
    step('运维页显示本机与保存时的自检结果，链接到“设置 → 图片存储”')

    // ---------------------------------------------------------------- 清空 R2 设置
    await r2(page).getByRole('button', { name: '清空 R2 设置' }).click()
    await r2(page).getByText('保存后清空 R2 的连接信息与密钥').waitFor()
    await saveButton(page).click()
    await page.getByRole('status').filter({ hasText: '已保存' }).waitFor()
    current = JSON.parse((await api(page, workspace, 'GET', '/api/v1/settings/storage')).text)
    check(current.version === 2 && current.r2.secret_set === false && current.r2.account_id === '', `清空后不对：${JSON.stringify(current)}`)
    await r2(page).waitFor({ state: 'detached' })
    step('清空 R2 设置：连接信息与密钥都删掉，R2 一栏收起')

    // ---------------------------------------------------------------- admin 只读、member 看不到
    const admin = await login(browser, adminEmail, problems, outbound)
    await openAs(admin.page, workspace, '/settings/storage')
    await form(admin.page).waitFor()
    await admin.page.getByText('只有 owner 能修改图片存储设置；你可以查看当前设置。').waitFor()
    check(await admin.page.getByRole('radio', { name: /Cloudflare R2/ }).isDisabled(), 'admin 不能改主存储')
    check(await saveButton(admin.page).count() === 0, 'admin 没有保存按钮')
    const denied = await api(admin.page, workspace, 'PUT', '/api/v1/settings/storage', { version: 2, primary: 'local' })
    check(denied.status === 403, `admin 保存应 403：${denied.status}`)
    await admin.context.close()
    step('admin 只读（看得到、改不了），接口 403')

    const member = await login(browser, memberEmail, problems, outbound)
    await openAs(member.page, workspace, '/settings/storage')
    check(await form(member.page).count() === 0, 'member 不应看到设置表单')
    check(await member.page.getByRole('navigation', { name: '主导航' }).locator('a[href="/settings/storage"]').count() === 0, 'member 的侧栏不应有图片存储')
    const hidden = await api(member.page, workspace, 'GET', '/api/v1/settings/storage')
    check(hidden.status === 403, `member 读取应 403：${hidden.status}`)
    await member.context.close()
    step('member 看不到页面与侧栏入口，接口 403')

    // ---------------------------------------------------------------- 切换工作空间：显示那个空间自己的设置
    await page.getByLabel('当前工作空间').selectOption(memberSpace)
    await page.getByText('只有 owner 能修改图片存储设置；你可以查看当前设置。').waitFor()
    check(await page.getByRole('radio', { name: /本机/ }).isChecked(), '另一个空间应是自己的设置（本机）')
    await page.getByLabel('当前工作空间').selectOption(workspace)
    await saveButton(page).waitFor()
    step('切换工作空间后显示那个空间自己的设置与角色（那里是 admin，只读）')

    // ---------------------------------------------------------------- 手机宽度与暗色
    const phone = await login(browser, ownerEmail, problems, outbound, { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })
    await openAs(phone.page, workspace, '/settings/storage')
    await form(phone.page).waitFor()
    await choose(phone.page, 'Cloudflare R2')
    await r2(phone.page).waitFor()
    check(!(await phone.page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)), '手机宽度不应横向滚动')
    await shot(phone.page, 'storage-settings-phone')
    await phone.context.close()
    const dark = await login(browser, ownerEmail, problems, outbound, { colorScheme: 'dark' })
    await openAs(dark.page, workspace, '/settings/storage')
    await form(dark.page).waitFor()
    const background = await dark.page.evaluate(() => getComputedStyle(document.body).backgroundColor)
    check(background !== 'rgb(255, 255, 255)', `暗色主题背景不应是白色：${background}`)
    await shot(dark.page, 'storage-settings-dark')
    await dark.context.close()
    step('手机宽度不横向滚动；暗色主题')

    check(outbound.length === 0, `浏览器不应直连外部地址：${outbound.slice(0, 5).join(', ')}`)
    check(problems.length === 0, `页面出错：\n${problems.join('\n')}`)
    step('浏览器只请求工作台，没有页面错误')
  } finally {
    await browser.close()
  }
})().catch((error) => {
  console.error(error)
  process.exit(1)
})
