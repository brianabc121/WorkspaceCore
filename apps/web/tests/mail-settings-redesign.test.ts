// 邮件设置页改版（T-0194，方案 docs/v2/ai-admin-redesign 计划 B4，效果图 05）：OAuth 客户端卡片、多邮箱表、
// 所选邮箱的授权 / 分配 / 署名。分配（PUT …/agents）与署名（PATCH）分别提交、各用最新 version，冲突不覆盖；
// 分配不打开实例的发信开关；客户端密钥只写不读；设置页不读邮件内容。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace, fail } from './helpers'
import type { Schema } from '../src/services/api'

type Account = Schema['MailAccountOut']
const at = '2026-11-09T08:00:00Z'
const path = (r: Request) => new URL(r.url).pathname
// 回调地址故意不是效果图里的示例域名：页面必须显示接口给的值
const settings: Schema['MailSettingsOut'] = { configured: true, client_id: '555000-client.apps.googleusercontent.com', client_secret_set: true, redirect_uri: 'https://ws.internal.test/api/v1/mail/oauth/callback', version: 4 }
const AGENTS: Schema['MailAgentRef'][] = [{ id: 'inst-dev', name: '开发助手' }, { id: 'inst-ops', name: '运维助手' }, { id: 'inst-news', name: '新闻助手' }]
const account = (over: Partial<Account>): Account => ({ id: 'acc-x', email: 'x@example.com', display_name: 'x@example.com', status: 'active', can_read: true, can_send: true, agents: [], agent_signature: false, last_error: null, connected_at: at, version: 1, ...over })
const fixtures = (): Account[] => [
  account({ id: 'acc-work', email: 'work@example.com', display_name: 'work@example.com', agents: [AGENTS[0], AGENTS[1]], agent_signature: true, version: 5 }),
  account({ id: 'acc-personal', email: 'personal@example.com', display_name: 'personal@example.com', can_send: false, version: 1 }),
  account({ id: 'acc-reports', email: 'reports@example.com', display_name: 'reports@example.com', status: 'needs_reconnect', last_error: 'invalid_grant', agents: [AGENTS[2]], agent_signature: true, version: 3 }),
]
const AUTHORIZE = 'https://accounts.google.com/o/oauth2/v2/auth?state=s'
// 读邮件内容的接口：设置页一个都不能请求
const CONTENT = /^\/api\/v1\/mail\/(messages|search|sends|accounts\/[^/]+\/(messages|threads|labels|send))/

// 有状态的假后端：PATCH / PUT 检查 version、成功后 version + 1；DELETE 之后列表里没有这个邮箱
function setup(options: { role?: Schema['WorkspaceOut']['role']; handler?: (r: Request) => Response | Promise<Response | undefined> | undefined } = {}) {
  const state = { accounts: fixtures(), settings: { ...settings } }
  const requests = mockApi(async r => {
    const custom = await options.handler?.(r)
    if (custom) return custom
    const p = path(r)
    if (p === '/api/v1/me' && r.method === 'GET') return Response.json({ ...me, workspaces: [{ ...workspace, role: options.role ?? 'owner' }] })
    if (p === '/api/v1/mail/settings' && r.method === 'GET') return Response.json(state.settings)
    if (p === '/api/v1/mail/settings' && r.method === 'PUT') {
      const body = await r.clone().json() as Schema['MailSettingsPut']
      state.settings = { ...state.settings, client_id: body.client_id, version: (state.settings.version ?? 0) + 1 }
      return Response.json(state.settings)
    }
    if (p === '/api/v1/mail/accounts' && r.method === 'GET') return Response.json({ items: state.accounts })
    if (p === '/api/v1/mail/agents' && r.method === 'GET') return Response.json({ items: AGENTS })
    if (p === '/api/v1/mail/connect' && r.method === 'POST') return Response.json({ authorize_url: AUTHORIZE })
    const one = /^\/api\/v1\/mail\/accounts\/([^/]+)(\/agents)?$/.exec(p)
    const found = one && state.accounts.find(a => a.id === one[1])
    if (found && r.method === 'DELETE' && !one[2]) { state.accounts = state.accounts.filter(a => a !== found); return new Response(null, { status: 204 }) }
    if (found && (r.method === 'PATCH' || r.method === 'PUT')) {
      const body = await r.clone().json() as { version: number; agent_signature?: boolean; instance_ids?: string[] }
      if (body.version !== found.version) return fail(409, 'VERSION_CONFLICT')
      if (r.method === 'PATCH') found.agent_signature = body.agent_signature ?? found.agent_signature
      else found.agents = AGENTS.filter(a => body.instance_ids!.includes(a.id))
      found.version += 1
      return Response.json(found)
    }
    return undefined
  })
  return { requests, state }
}
const writes = (requests: Request[]) => requests.filter(r => r.method !== 'GET')
async function rowOf(email: string) { return screen.findByRole('row', { name: `邮箱：${email}` }) }
async function panel() { return screen.findByRole('complementary', { name: '所选邮箱' }) }
async function select(email: string) {
  await fireEvent.click(within(await rowOf(email)).getByRole('button', { name: '管理' }))
  const aside = await panel()
  await within(aside).findByRole('heading', { level: 2, name: email })
  return aside
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('mail settings redesign', () => {
  it('lists all three mailboxes, keeps the one that needs reconnecting and reconnects it through Google', async () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign, origin: window.location.origin })
    const { requests } = setup()
    await openApp('/settings/mail')
    expect(await screen.findByRole('heading', { level: 1, name: '邮件设置' })).toBeVisible()
    expect(screen.getByRole('link', { name: /前往邮件/ })).toHaveAttribute('href', '/mail')
    const list = screen.getByRole('region', { name: '邮箱账户' })
    expect(await within(list).findByRole('heading', { name: /邮箱账户 · 3/ })).toBeVisible()
    expect(within(list).getByText('1 个邮箱需要重新连接')).toBeVisible()
    const work = await rowOf('work@example.com')
    expect(within(work).getByText('正常')).toBeVisible()
    expect(within(work).getByText('开发助手、运维助手')).toBeVisible()
    expect(within(await rowOf('personal@example.com')).getByText('未分配')).toBeVisible()
    const reports = await rowOf('reports@example.com')
    expect(within(reports).getByText('需要重新连接')).toBeVisible()
    expect(within(reports).getByText('新闻助手')).toBeVisible()
    expect(within(work).queryByRole('button', { name: '重新连接' })).not.toBeInTheDocument()

    await fireEvent.click(within(reports).getByRole('button', { name: '重新连接' }))
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith(AUTHORIZE))
    // 重新连接走原来的 connect（Google 里再选一次账户、回到原回调），登录提示只是预填
    const connect = requests.find(r => path(r) === '/api/v1/mail/connect')!
    expect(await connect.clone().json()).toEqual({ login_hint: 'reports@example.com' })
    expect(connect.headers.get('X-Workspace-Id')).toBe('workspace-a')
  })

  it('shows read and send scopes separately and never marks a mailbox that needs reconnecting as fully working', async () => {
    setup()
    await openApp('/settings/mail')
    let aside = await select('personal@example.com')
    const scopes = () => within(aside).getByRole('list', { name: '邮箱授权' })
    expect(within(scopes()).getByText('读取邮件').closest('li')).toHaveTextContent('已授权')
    expect(within(scopes()).getByText('发送邮件').closest('li')).toHaveTextContent('未授权')

    aside = await select('reports@example.com')
    expect(within(aside).getByText('需要重新连接', { selector: '.asv-state' })).toBeVisible()
    expect(within(aside).getByText(/invalid_grant/)).toBeVisible()
    for (const name of ['读取邮件', '发送邮件']) {
      const item = within(scopes()).getByText(name).closest('li')!
      expect(item).toHaveTextContent('需要重新连接')
      expect(item).not.toHaveClass('is-ok')
    }
    expect(within(aside).getByRole('button', { name: '重新连接' })).toBeVisible()
  })

  it('opens the mailbox from ?account= and does not carry an assignment draft to another mailbox', async () => {
    const { requests } = setup()
    const { router } = await openApp('/settings/mail?account=acc-work')
    let aside = await panel()
    await within(aside).findByRole('heading', { level: 2, name: 'work@example.com' })
    let group = within(aside).getByRole('group', { name: '分配 work@example.com 给智能体' })
    expect(within(group).getByLabelText('开发助手')).toBeChecked()
    await fireEvent.click(within(group).getByLabelText('新闻助手'))
    expect(within(group).getByRole('button', { name: '保存分配' })).toBeEnabled()

    aside = await select('personal@example.com')
    expect(router.currentRoute.value.query.account).toBe('acc-personal')
    group = within(aside).getByRole('group', { name: '分配 personal@example.com 给智能体' })
    for (const agent of AGENTS) expect(within(group).getByLabelText(agent.name)).not.toBeChecked()
    expect(within(group).getByRole('button', { name: '保存分配' })).toBeDisabled()

    aside = await select('work@example.com')
    group = within(aside).getByRole('group', { name: '分配 work@example.com 给智能体' })
    expect(within(group).getByLabelText('新闻助手')).not.toBeChecked()
    expect(within(group).getByRole('button', { name: '保存分配' })).toBeDisabled()
    expect(writes(requests)).toEqual([])
  })

  it('keeps a new mailbox unassigned and assigning it does not turn on any instance mail permission', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { requests } = setup()
    await openApp('/settings/mail?account=acc-personal')
    const aside = await panel()
    const group = await within(aside).findByRole('group', { name: '分配 personal@example.com 给智能体' })
    expect(within(group).getByText(/还没有分配给智能体/)).toBeVisible()
    expect(within(aside).getByText('新邮箱默认不分配给智能体。')).toBeVisible()
    expect(within(aside).getByRole('link', { name: /管理智能体邮件权限/ })).toHaveAttribute('href', '/agents')
    await fireEvent.click(within(group).getByLabelText('开发助手'))
    await fireEvent.click(within(group).getByRole('button', { name: '保存分配' }))
    expect(await within(aside).findByText('邮箱分配已保存')).toBeVisible()
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('把 personal@example.com 分配给 开发助手'))
    const put = requests.find(r => r.method === 'PUT')!
    expect(path(put)).toBe('/api/v1/mail/accounts/acc-personal/agents')
    expect(await put.clone().json()).toEqual({ version: 1, instance_ids: ['inst-dev'] })
    // 只有这一个写请求：不碰实例的邮件开关（/agents/instances/{id}/mail）
    expect(writes(requests).map(path)).toEqual(['/api/v1/mail/accounts/acc-personal/agents'])
    expect(requests.some(r => path(r).startsWith('/api/v1/agents/'))).toBe(false)
    expect(within(await rowOf('personal@example.com')).getByText('开发助手')).toBeVisible()
  })

  it('saves the signature and the assignment separately, the assignment with the version the signature save returned', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { requests } = setup()
    await openApp('/settings/mail?account=acc-work')
    const aside = await panel()
    const group = await within(aside).findByRole('group', { name: '分配 work@example.com 给智能体' })
    await fireEvent.click(within(group).getByLabelText('新闻助手'))
    const signature = within(aside).getByRole('switch', { name: '智能体发信署名' })
    expect(signature).toHaveAttribute('aria-checked', 'true')
    await fireEvent.click(signature)
    expect(await within(aside).findByText('邮箱设置已保存')).toBeVisible()
    await vi.waitFor(() => expect(signature).toHaveAttribute('aria-checked', 'false'))
    // 署名保存后，没保存的分配草稿还在
    expect(within(group).getByLabelText('新闻助手')).toBeChecked()
    await fireEvent.click(within(group).getByRole('button', { name: '保存分配' }))
    expect(await within(aside).findByText('邮箱分配已保存')).toBeVisible()
    const [patch, put] = writes(requests)
    expect(path(patch)).toBe('/api/v1/mail/accounts/acc-work')
    expect(await patch.clone().json()).toEqual({ version: 5, agent_signature: false })
    expect(path(put)).toBe('/api/v1/mail/accounts/acc-work/agents')
    expect(await put.clone().json()).toEqual({ version: 6, instance_ids: ['inst-dev', 'inst-ops', 'inst-news'] })
  })

  it('shows a re-check message on a version conflict and does not retry or overwrite', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { requests, state } = setup()
    await openApp('/settings/mail?account=acc-work')
    const aside = await panel()
    const group = await within(aside).findByRole('group', { name: '分配 work@example.com 给智能体' })
    // 别人在这期间改了署名：服务器上的 version 已经是 6
    const work = state.accounts.find(a => a.id === 'acc-work')!
    work.version = 6; work.agent_signature = false
    await fireEvent.click(within(group).getByLabelText('新闻助手'))
    await fireEvent.click(within(group).getByRole('button', { name: '保存分配' }))
    expect(await within(aside).findByRole('alert')).toHaveTextContent(/刚被其他人修改.*核对/)
    expect(writes(requests)).toHaveLength(1)
    expect(work.agents.map(a => a.id)).toEqual(['inst-dev', 'inst-ops'])
    // 重新载入了最新数据（署名显示为别人改后的值），草稿保留，等用户核对后自己再保存
    await vi.waitFor(() => expect(within(aside).getByRole('switch', { name: '智能体发信署名' })).toHaveAttribute('aria-checked', 'false'))
    expect(within(group).getByLabelText('新闻助手')).toBeChecked()
    expect(screen.queryByText('邮箱分配已保存')).not.toBeInTheDocument()
  })

  it('keeps an unsaved assignment through a background refresh and asks to re-check when someone else changed it', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { requests, state } = setup()
    const { queryClient } = await openApp('/settings/mail?account=acc-work')
    const aside = await panel()
    const group = await within(aside).findByRole('group', { name: '分配 work@example.com 给智能体' })
    await fireEvent.click(within(group).getByLabelText('新闻助手'))
    // 别人在另一处把“运维助手”移出了这个邮箱；列表在后台刷新（例如 SSE）
    const work = state.accounts.find(a => a.id === 'acc-work')!
    work.agents = [AGENTS[0]]; work.version = 6
    await queryClient.invalidateQueries({ queryKey: ['mail'] })
    await vi.waitFor(() => expect(within(group).getByText(/能用这个邮箱的智能体：开发助手。/)).toBeVisible())
    expect(within(group).getByLabelText('新闻助手')).toBeChecked()
    expect(within(group).getByLabelText('运维助手')).toBeChecked()
    // 第一次点保存：不发请求，先提示核对别人改后的分配
    await fireEvent.click(within(group).getByRole('button', { name: '保存分配' }))
    expect(within(group).getByRole('alert')).toHaveTextContent('这个邮箱的分配刚被其他人修改，现在是：开发助手')
    expect(writes(requests)).toEqual([])
    // 核对后再点：用最新的 version 提交用户确认过的勾选
    await fireEvent.click(within(group).getByLabelText('运维助手'))
    await fireEvent.click(within(group).getByRole('button', { name: '保存分配' }))
    expect(await within(aside).findByText('邮箱分配已保存')).toBeVisible()
    expect(await writes(requests)[0].clone().json()).toEqual({ version: 6, instance_ids: ['inst-dev', 'inst-news'] })
  })

  it('shows the callback address from the API with a copy button and keeps the stored client secret when left blank', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(window.navigator, 'clipboard', { value: { writeText }, configurable: true })
    const { requests } = setup()
    await openApp('/settings/mail')
    const client = await screen.findByRole('region', { name: 'Google OAuth 客户端' })
    expect(await within(client).findByText(settings.redirect_uri)).toBeVisible()
    expect(within(client).getByText(settings.client_id!)).toBeVisible()
    expect(within(client).getByText('已设置 · 不回显')).toBeVisible()
    expect(within(client).getByText(/读取邮件 \/ 发送邮件/)).toBeVisible()
    expect(document.body).not.toHaveTextContent('workspace.example.com')
    await fireEvent.click(within(client).getByRole('button', { name: '复制授权回调地址' }))
    expect(writeText).toHaveBeenCalledWith(settings.redirect_uri)

    await fireEvent.click(within(client).getByRole('button', { name: '修改客户端' }))
    const secret = within(client).getByLabelText('客户端密钥')
    expect(secret).toHaveValue('')
    expect(secret).toHaveAttribute('type', 'password')
    // 输入了又取消：关掉表单就清空，不留到下次
    await fireEvent.update(secret, 'GOCSPX-abandoned')
    await fireEvent.click(within(client).getByRole('button', { name: '取消' }))
    await fireEvent.click(within(client).getByRole('button', { name: '修改客户端' }))
    expect(within(client).getByLabelText('客户端密钥')).toHaveValue('')
    await fireEvent.update(within(client).getByLabelText('客户端 ID'), '777000-new.apps.googleusercontent.com')
    await fireEvent.click(within(client).getByRole('button', { name: '保存 OAuth 客户端' }))
    expect(await within(client).findByText('OAuth 客户端已保存')).toBeVisible()
    const put = requests.find(r => r.method === 'PUT')!
    expect(await put.clone().json()).toEqual({ client_id: '777000-new.apps.googleusercontent.com', version: 4 })
    expect(window.location.href).not.toContain('GOCSPX')
    expect(JSON.stringify({ ...localStorage })).not.toContain('GOCSPX')
    delete (window.navigator as { clipboard?: unknown }).clipboard
  })

  it('asks before disconnecting, says Google access is revoked and sent records are kept, and never reads mail content', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    const { requests } = setup()
    const { router } = await openApp('/settings/mail?account=acc-work')
    const aside = await panel()
    await within(aside).findByRole('heading', { level: 2, name: 'work@example.com' })
    await fireEvent.click(within(aside).getByRole('button', { name: '断开邮箱' }))
    expect(confirm).toHaveBeenLastCalledWith(expect.stringMatching(/断开 work@example\.com.*撤销 Google 授权.*发件记录保留/))
    expect(requests.some(r => r.method === 'DELETE')).toBe(false)
    await fireEvent.click(within(aside).getByRole('button', { name: '断开邮箱' }))
    expect(await screen.findByText('邮箱已断开')).toBeVisible()
    expect(requests.filter(r => r.method === 'DELETE').map(path)).toEqual(['/api/v1/mail/accounts/acc-work'])
    await vi.waitFor(() => expect(screen.queryByRole('row', { name: '邮箱：work@example.com' })).not.toBeInTheDocument())
    await vi.waitFor(() => expect(router.currentRoute.value.query.account).toBeUndefined())
    expect(await rowOf('personal@example.com')).toBeVisible()
    expect(requests.filter(r => CONTENT.test(path(r)))).toEqual([])
  })

  it('lets admins assign mailboxes but points instance mail permissions to the owner', async () => {
    setup({ role: 'admin' })
    await openApp('/settings/mail?account=acc-work')
    const aside = await panel()
    await within(aside).findByRole('group', { name: '分配 work@example.com 给智能体' })
    expect(within(aside).queryByRole('link', { name: /管理智能体邮件权限/ })).not.toBeInTheDocument()
    expect(within(aside).getByText(/由所有者在“OpenClaw 智能体”页设置/)).toBeVisible()
  })
})
