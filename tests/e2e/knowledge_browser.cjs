// 知识库浏览器验收（T-0100，方案 docs/v2/KNOWLEDGE_PLAN.md 第 6 节 K-6）：真实后端上的独立知识库页面——
// 知识图谱点节点 → 编辑器 [[ 补全、保存后链接解析、行内 #标签 → 两个标签页同时编辑，三方合并后保存 →
// 删除进回收站再恢复 → 项目知识库主页（简报、每周修改的趋势图与悬停提示）→ 手机宽度 → viewer 只读。
//
// 前提：Compose 全栈已启动并迁移（make up && make migrate）。
// 不要求空库：每次用新的工作台用户；用户由同目录的 seed.py 写入，笔记与项目经工作台接口建。
// 用法（依赖由同目录的 package.json / package-lock.json 锁定）：
//   cd tests/e2e && npm ci && npm run browser:install    # 首次：安装依赖与 Chromium
//   node knowledge_browser.cjs [BASE_URL]                  # 默认 http://127.0.0.1:${HTTP_PORT:-8080}
// CI（.github/workflows/ci-infra.yml）与本机共用。
const { execFileSync } = require('node:child_process')
const { randomBytes, randomUUID } = require('node:crypto')
const path = require('node:path')
const { chromium } = require('playwright')

const base = (process.argv[2] || `http://127.0.0.1:${process.env.HTTP_PORT || '8080'}`).replace(/\/$/, '')
const password = 'e2e knowledge browser pass'
const run = randomBytes(4).toString('hex')
const ownerEmail = `kb-owner-${run}@browser.example.com`
const viewerEmail = `kb-viewer-${run}@browser.example.com`

function seed(...args) {
  return execFileSync('python3', [path.join(__dirname, 'seed.py'), ...args], {
    encoding: 'utf8',
    env: { ...process.env, E2E_PASSWORD: password },
  }).trim().split(/\s+/)
}
function check(condition, message) { if (!condition) throw new Error(message) }
// 1×1 的 PNG；带上本次运行的标记，每次内容（哈希）不同
function tinyPng(run) {
  const { crc32 } = require('node:zlib')
  const chunk = (type, data) => {
    const head = Buffer.alloc(4); head.writeUInt32BE(data.length)
    const body = Buffer.concat([Buffer.from(type), data])
    const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(body) >>> 0)
    return Buffer.concat([head, body, crc])
  }
  const ihdr = Buffer.from([0, 0, 0, 1, 0, 0, 0, 1, 8, 2, 0, 0, 0])
  const idat = require('node:zlib').deflateSync(Buffer.from([0, 0x2a, 0x78, 0xd6]))
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr), chunk('tEXt', Buffer.from(`Comment\0${run}`)), chunk('IDAT', idat), chunk('IEND', Buffer.alloc(0))])
}

// 页面里直接调用接口（带会话 Cookie、CSRF 与工作空间），用于准备数据
async function api(page, workspace, method, pathname, body, extra = {}) {
  const result = await page.evaluate(async ({ method, pathname, body, workspace, extra }) => {
    const csrf = document.cookie.split('; ').find((item) => item.startsWith('ws_csrf='))?.slice(8)
    const headers = { 'X-Workspace-Id': workspace, ...(csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {}), ...extra }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    const response = await fetch(pathname, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
    return { status: response.status, body: response.status === 204 ? null : await response.json() }
  }, { method, pathname, body, workspace, extra })
  check(result.status < 300, `${method} ${pathname} 失败：${result.status} ${JSON.stringify(result.body).slice(0, 300)}`)
  return result.body
}

function watch(page, problems) {
  page.on('pageerror', (error) => problems.push(error.message))
  page.on('console', (message) => {
    // 登录前查询当前用户的 401、故意制造的版本冲突（409）是预期响应，浏览器会把它们记为资源加载失败
    if (message.type() === 'error' && !/status of (401|409)\b/.test(message.text())) problems.push(message.text())
  })
  page.on('dialog', (dialog) => dialog.accept())
}
async function login(browser, email, problems) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', timezoneId: 'Asia/Shanghai' })
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
const editorReady = (page) => page.waitForFunction(() => document.querySelector('.cm-content'))
const saveButton = (page) => page.getByRole('button', { name: '保存', exact: true }).last()
const NOTE_URL = /\/knowledge\/notes\/[0-9a-f-]{36}$/

;(async () => {
  const [, workspace] = seed('user', ownerEmail, 'KbOwner')
  const [viewerId] = seed('user', viewerEmail, 'KbViewer')
  seed('member', workspace, viewerId, 'viewer')

  const browser = await chromium.launch()
  const problems = []
  const step = (text) => console.log(`通过  ${text}`)
  const { context, page } = await login(browser, ownerEmail, problems)

  // ---------------------------------------------------------------- 准备：公共库的流程笔记、一个项目（项目库自动建）与简报
  const spaces = (await api(page, workspace, 'GET', '/api/v1/knowledge/spaces')).items
  const pub = spaces.find((s) => s.kind === 'public')
  check(pub, '新工作空间应自动有公共知识库')
  const project = await api(page, workspace, 'POST', '/api/v1/projects', { title: `知识库浏览器 ${run}`, description: '' }, { 'Idempotency-Key': randomUUID() })
  const projectSpace = (await api(page, workspace, 'GET', '/api/v1/knowledge/spaces')).items.find((s) => s.project_id === project.id)
  check(projectSpace, '新项目应自动有项目知识库')
  const flowTitle = `发布流程 ${run}`
  const flow = await api(page, workspace, 'POST', '/api/v1/knowledge/notes', { space_id: pub.id, title: flowTitle, body: '1. 备份\n2. 迁移\n3. 发布', type: 'process' })
  await api(page, workspace, 'POST', '/api/v1/knowledge/notes', { space_id: projectSpace.id, title: `知识库浏览器 ${run} 简报`, body: `目标：浏览器验收。流程见 [[${flowTitle}]]`, type: 'brief' })

  // ---------------------------------------------------------------- 知识总览（T-0173）：/knowledge 的统计来自服务端，旧的 ?focus= 进完整图谱
  await page.goto(`${base}/knowledge`)
  await page.getByRole('navigation', { name: '知识库功能' }).waitFor()
  const monday = new Date(); monday.setHours(0, 0, 0, 0); monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7))
  const nextMonday = new Date(monday); nextMonday.setDate(nextMonday.getDate() + 7)
  const totals = (await api(page, workspace, 'GET', `/api/v1/knowledge/overview?updated_after=${encodeURIComponent(monday.toISOString())}&updated_before=${encodeURIComponent(nextMonday.toISOString())}`)).summary
  const noteStat = page.getByRole('group', { name: `笔记：${totals.note_count.toLocaleString('zh-CN')} 篇` })
  await noteStat.waitFor()
  check(await page.getByRole('group', { name: `本周更新：${totals.updated_note_count.toLocaleString('zh-CN')} 篇` }).count() === 1, '总览的“本周更新”应是服务端统计')
  check(await page.getByRole('region', { name: '最近更新' }).getByRole('button', { name: flowTitle }).count() === 1, '最近更新里应有刚写的笔记')
  await page.goto(`${base}/knowledge?focus=${pub.id}`)
  await page.waitForURL((url) => url.pathname === '/knowledge/graph' && url.searchParams.get('focus') === pub.id)
  step('知识总览 /knowledge：四项统计与服务端一致、最近更新；旧链接 /knowledge?focus= 转到完整图谱')

  // ---------------------------------------------------------------- 独立页面与知识图谱
  await page.goto(`${base}/knowledge/graph`)
  await page.getByRole('navigation', { name: '知识库功能' }).waitFor()
  check(await page.getByRole('navigation', { name: '主导航' }).count() === 0, '知识库是独立页面，不应显示工作台的主导航')
  const node = page.getByRole('button', { name: new RegExp(`^${flowTitle}，笔记`) })
  await node.waitFor()
  await node.click()
  const selected = page.getByRole('complementary', { name: '所选笔记' })
  check((await selected.getByRole('link', { name: '打开笔记' }).getAttribute('href')) === `/knowledge/notes/${flow.id}`, '所选笔记的“打开笔记”应指向这篇笔记')
  step('独立页面 /knowledge/graph：功能条与知识图谱，点节点显示所选笔记')

  // ---------------------------------------------------------------- 按类型着色与按类型筛选（T-0102）
  check((await page.getByRole('combobox', { name: '着色' }).inputValue()) === 'type', '图谱默认应按类型着色')
  check(await node.locator('circle').first().evaluate(c => c.classList.contains('f-g-rule')), '“流程”类型的笔记应是“规范与流程”组的颜色')
  check((await page.getByRole('button', { name: new RegExp(`^${flowTitle}，笔记（流程）`) }).count()) === 1, '节点的说明应包含具体类型')
  const legend = page.getByRole('group', { name: '按类型显示' })
  check((await legend.innerText()).includes('规范 流程 操作手册 模板'), '图例应按组列出类型')
  await page.getByRole('button', { name: '类型：全部' }).click()
  await page.getByRole('group', { name: '按类型筛选' }).getByRole('checkbox', { name: /^流程\s*\d/ }).uncheck()
  await node.waitFor({ state: 'detached' })
  await page.getByRole('group', { name: '按类型筛选' }).getByRole('button', { name: '全部显示' }).click()
  await node.waitFor()
  check(!(await selected.innerText()).includes('人已确认'), '不应再显示“人已确认”')
  step('图谱按类型着色（默认），图例按组列出类型，可按单个类型筛选；没有“人已确认”')

  // ---------------------------------------------------------------- 编辑器：[[ 补全、保存、链接解析、行内 #标签
  const mineTitle = `浏览器写的笔记 ${run}`
  await page.goto(`${base}/knowledge/notes/new?space=${pub.id}`)
  const title = page.getByRole('textbox', { name: '标题' })
  await title.waitFor()
  await editorReady(page)
  await title.fill(mineTitle)
  await page.locator('.cm-content').click()
  await page.keyboard.type('流程见 [[发布流程')
  const option = page.locator('.cm-tooltip-autocomplete li', { hasText: flowTitle })
  await option.first().waitFor({ timeout: 10000 })
  await option.first().click()
  check((await page.locator('.cm-content').innerText()).includes(`[[${flowTitle}]]`), `选中补全后应插入 [[${flowTitle}]]`)
  await page.keyboard.type(' 和 #验收 ')
  await saveButton(page).click()
  await page.waitForURL(NOTE_URL)
  const mineUrl = page.url()
  const link = page.getByRole('article').getByRole('link', { name: flowTitle })
  await link.waitFor()
  check((await link.getAttribute('href')) === `/knowledge/notes/${flow.id}`, '保存后 [[链接]] 应解析为那篇笔记的站内链接')
  check(await page.getByRole('link', { name: '#验收' }).count() > 0, '行内 #验收 应并入标签')
  step('编辑器：[[ 补全、保存后链接解析为站内链接、行内 #标签')

  // ---------------------------------------------------------------- 两个标签页同时编辑：提示版本冲突，三方合并保留两边的改动
  const other = await context.newPage()
  watch(other, problems)
  await other.goto(`${mineUrl}/edit`)
  await editorReady(other)
  await page.goto(`${mineUrl}/edit`)
  await editorReady(page)
  await other.locator('.cm-content').click()
  await other.keyboard.press('Control+End')
  await other.keyboard.type('\n另一处加的一行')
  await saveButton(other).click()
  await other.waitForURL(NOTE_URL)
  await other.close()
  await page.locator('.cm-content').click()
  await page.keyboard.press('Control+Home')
  await page.keyboard.type('开头加的一行\n')
  await saveButton(page).click()
  await page.getByText(/保存了第 2 版/).waitFor()
  await page.getByRole('button', { name: '合并它的改动' }).click()
  await page.getByText(/已合并第 2 版的改动/).waitFor()
  const merged = await page.locator('.cm-content').innerText()
  check(merged.includes('开头加的一行') && merged.includes('另一处加的一行'), '三方合并应保留两边的改动')
  await saveButton(page).click()
  await page.waitForURL(NOTE_URL)
  const saved = await page.getByRole('article').innerText()
  check(saved.includes('开头加的一行') && saved.includes('另一处加的一行'), '合并后应保存成功')
  step('版本冲突：另一个标签页先保存时提示，三方合并后保存（第 3 版）')

  // ---------------------------------------------------------------- 删除进回收站，在变更记录里恢复
  await page.getByRole('button', { name: '更多操作' }).click()
  await page.getByRole('menuitem', { name: /删除/ }).click()
  const dialog = page.getByRole('dialog', { name: '删除笔记' })
  await dialog.getByLabel(/原因/).fill('浏览器验收：删除再恢复')
  await dialog.getByRole('button', { name: '放进回收站' }).click()
  await page.waitForURL(/\/knowledge\/spaces\//)
  await page.goto(`${base}/knowledge/changes?tab=trash`)
  await page.getByRole('listitem').filter({ hasText: mineTitle }).getByRole('button', { name: '恢复' }).click()
  await page.getByText(`已恢复《${mineTitle}》`).waitFor()
  await page.goto(mineUrl)
  await page.getByRole('article').waitFor()
  check(await page.getByRole('button', { name: '恢复' }).count() === 0, '恢复后笔记不应还显示在回收站里')
  step('删除进回收站，在变更记录的回收站里恢复')

  // ---------------------------------------------------------------- 项目知识库主页：简报、每周修改的趋势图
  await page.goto(`${base}/knowledge/spaces/${projectSpace.id}`)
  await page.getByRole('region', { name: '项目简报' }).getByText('目标：浏览器验收').waitFor()
  const chart = page.getByRole('region', { name: '每周的修改' })
  const thisWeek = chart.getByRole('listitem').last()
  await thisWeek.waitFor()
  const described = await thisWeek.getAttribute('aria-label')
  check(/人 1 次，智能体 0 次$/.test(described || ''), `本周应有人的 1 次修改（简报）：${described}`)
  await thisWeek.hover()
  await chart.getByRole('status').filter({ hasText: '合计' }).waitFor()
  await chart.getByText('以表格查看').click()
  await chart.getByRole('table').waitFor()
  check(await chart.getByRole('row').count() === 9, '趋势图的表格视图应有 8 周（加表头）')
  step('项目知识库主页：简报、每周修改的趋势图（悬停提示、表格视图）')

  // ---------------------------------------------------------------- 图片（T-0103）：编辑器上传、阅读页显示与放大、图谱图片节点
  const imageTitle = `带图的笔记 ${run}`
  await page.goto(`${base}/knowledge/notes/new?space=${pub.id}`)
  await page.getByRole('textbox', { name: '标题' }).waitFor()
  await editorReady(page)
  await page.getByRole('textbox', { name: '标题' }).fill(imageTitle)
  await page.locator('.cm-content').click()
  await page.keyboard.type('拓扑图：')
  await page.locator('input[type=file]').setInputFiles({ name: '拓扑.png', mimeType: 'image/png', buffer: tinyPng(run) })
  await page.waitForFunction(() => /!\[拓扑\]\(\/api\/v1\/knowledge\/files\/[0-9a-f-]{36}\)/.test(document.querySelector('.cm-content')?.textContent ?? ''), null, { timeout: 15000 })
  await saveButton(page).click()
  await page.waitForURL(/\/knowledge\/notes\/[0-9a-f-]{36}$/)
  const picture = page.locator('article img.kb-img')
  await picture.waitFor()
  await page.waitForFunction(() => { const img = document.querySelector('article img.kb-img'); return !!img && img.complete && img.naturalWidth > 0 })
  await picture.click()
  await page.getByRole('dialog', { name: '拓扑' }).waitFor()
  await page.keyboard.press('Escape')
  await page.goto(`${base}/knowledge/graph`)
  await page.getByRole('checkbox', { name: '显示图片' }).check()
  await page.locator('.kb-node rect.f-file').first().waitFor()
  step('图片：编辑器上传后插入链接，阅读页显示并可放大，图谱“显示图片”有图片节点')

  // ---------------------------------------------------------------- 图片（T-0106）：粘贴进编辑器；没有笔记引用的图片被每日清理删掉
  // 往编辑区派发带图片文件的 paste 事件（与从剪贴板粘贴截图相同）；返回插入的图片地址
  async function pasteImage(content) {
    await page.locator('.cm-content').evaluate((el, bytes) => {
      const data = new DataTransfer()
      data.items.add(new File([new Uint8Array(bytes)], '截图.png', { type: 'image/png' }))
      el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }))
    }, [...content])
    const handle = await page.waitForFunction((known) => {
      const found = [...(document.querySelector('.cm-content')?.textContent ?? '').matchAll(/!\[[^\]]*\]\((\/api\/v1\/knowledge\/files\/[0-9a-f-]{36})\)/g)].map((m) => m[1])
      return found.find((url) => !known.includes(url)) ?? false
    }, [], { timeout: 15000 })
    return handle.jsonValue()
  }
  const pastedTitle = `粘贴截图的笔记 ${run}`
  await page.goto(`${base}/knowledge/notes/new?space=${pub.id}`)
  await editorReady(page)
  await page.getByRole('textbox', { name: '标题' }).fill(pastedTitle)
  await page.locator('.cm-content').click()
  await page.keyboard.type('报错截图：')
  const pastedUrl = await pasteImage(tinyPng(`${run}-pasted`))
  await saveButton(page).click()
  await page.waitForURL(NOTE_URL)
  await page.locator(`article img.kb-img[src="${pastedUrl}"]`).waitFor()
  await page.waitForFunction((src) => { const img = document.querySelector(`article img.kb-img[src="${src}"]`); return !!img && img.complete && img.naturalWidth > 0 }, pastedUrl)
  // 粘贴后没保存就离开：这张图没有任何笔记引用
  await page.goto(`${base}/knowledge/notes/new?space=${pub.id}`)
  await editorReady(page)
  await page.locator('.cm-content').click()
  const orphanUrl = await pasteImage(tinyPng(`${run}-orphan`))
  await page.goto(`${base}/operations`)
  // 运维页改版（T-0177）：知识库图片存储在“存储状态”面板里
  const storage = page.getByRole('region', { name: '存储状态' })
  await storage.getByText('3 张图片').waitFor()
  const removed = Number(seed('cleanup-files', workspace)[0])
  check(removed === 1, `每日清理应只删掉没有引用的那张图片：删了 ${removed} 张`)
  await page.reload()
  await storage.getByText('2 张图片').waitFor()
  // 用浏览器上下文的会话直接问服务器（不走页面缓存：图片按内容寻址、可长期缓存）
  const fetchStatus = async (url) => (await page.request.get(base + url)).status()
  check(await fetchStatus(orphanUrl) === 404, '清理后没有引用的图片应返回 404')
  check(await fetchStatus(pastedUrl) === 200, '笔记里引用的图片不应被清理')
  step('图片：粘贴截图后上传并插入链接、阅读页显示；没有笔记引用的图片被每日清理删掉，运维页的张数随之减少')

  // ---------------------------------------------------------------- 手机宽度：底部导航，不横向滚动
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'zh-CN', storageState: await context.storageState() })
  const phone = await mobile.newPage()
  watch(phone, problems)
  for (const url of ['/knowledge', '/knowledge/graph', `/knowledge/notes/${flow.id}`, `/knowledge/spaces/${projectSpace.id}`, '/knowledge/changes']) {
    await phone.goto(base + url)
    await phone.getByRole('navigation', { name: '知识库导航' }).waitFor()
    await phone.waitForLoadState('networkidle')
    check(await noHorizontalScroll(phone), `手机宽度下 ${url} 不应横向滚动`)
  }
  await mobile.close()
  step('手机宽度（390）：底部导航，各页面不横向滚动')

  // ---------------------------------------------------------------- viewer 只读
  const viewer = await login(browser, viewerEmail, problems)
  await viewer.page.getByLabel('当前工作空间').selectOption({ label: 'KbOwner 的空间' })
  await viewer.page.goto(`${base}/knowledge/notes/${flow.id}`)
  await viewer.page.getByRole('article').getByText('备份').waitFor()
  check(await viewer.page.getByRole('link', { name: '编辑' }).count() === 0, 'viewer 不应看到编辑')
  await viewer.page.goto(`${base}/knowledge/spaces/${projectSpace.id}`)
  await viewer.page.getByRole('region', { name: '项目简报' }).waitFor()
  check(await viewer.page.getByRole('link', { name: '新建笔记' }).count() === 0, 'viewer 不应看到新建笔记')
  check(await viewer.page.getByRole('link', { name: '编辑简报' }).count() === 0, 'viewer 不应看到编辑简报')
  step('viewer：能读笔记与项目知识库，看不到编辑与新建')

  check(problems.length === 0, `页面错误：${problems.join(' | ')}`)
  await browser.close()
  console.log('知识库浏览器验收通过')
})().catch((error) => { console.error(error); process.exit(1) })
