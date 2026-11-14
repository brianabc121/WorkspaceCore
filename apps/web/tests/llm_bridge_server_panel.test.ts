// 两个服务器页面上的“MCP 管理器”区块（T-0234，docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md 第 7 节）：
// 模块定义的 serverPanels 扩展点（src/modules/ModuleServerPanels.vue）和大模型客户端模块提供的区块
// （src/modules/llm_bridge/ServerManagersPanel.vue）——列表与链接、没有管理器时的说明、部署入口只给 owner、模块停用后区块消失。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/vue'
import { me, mockApi, newsModule, openApp, workspace } from './helpers'
import { detail, readyServer, serverList } from './agents-fixtures'
import { host, overview } from './container-fixtures'
import { modules, moduleServerPanels } from '../src/modules/registry'
import type { Schema } from '../src/services/api'

type Role = Schema['WorkspaceOut']['role']
type Manager = Schema['LlmBridgeServerManagerOut']
type Install = Schema['LlmBridgeInstallOut']
const at = '2026-11-14T00:00:00Z'
const SERVER = '55555555-5555-4555-8555-555555555555', HOST = '88888888-8888-4888-8888-888888888888'
const ONE = '44444444-4444-4444-8444-444444444444', TWO = '11111111-1111-4111-8111-111111111111', THREE = '33333333-3333-4333-8333-333333333333'
const NOTE = '这些容器不受本页面的重启、清理、回收和卸载影响；移除这台服务器后它们也会继续运行。重新部署、重启、卸载在“大模型客户端”的管理页面上做。'
const path = (r: Request) => new URL(r.url).pathname
const lbModule: Schema['ModuleOut'] = { ...newsModule, key: 'llm_bridge', name: '大模型客户端', tools: [], widgets: [] }
const install = (over: Partial<Install> = {}): Install => ({ target_kind: 'agent_server', target_id: SERVER, server_name: '机房一号', mode: 'tunnel', port: 18787, needs_sudo: false, outdated: false, deployed_at: at, ...over })
const manager = (over: Partial<Manager> = {}): Manager => ({ bridge_id: ONE, name: '2 号', enabled: true, reachable: true, version: '0.1.0', platforms: ['chatgpt', 'codex', 'claude'], logged_in: ['claude', 'openai'], deployment: install(), ...over })

interface Options { role?: Role; managers?: Manager[]; available?: boolean; lb?: Schema['ModuleOut'] }
/** 智能体服务器页（一台初始化好的服务器）和容器服务器页（一台服务器）都能打开；installs 按 options 回 */
function setup(options: Options = {}) {
  const server = readyServer({ id: SERVER, name: '机房一号' })
  return mockApi(r => {
    const p = path(r)
    if (p === '/api/v1/me' && r.method === 'GET') return Response.json({ ...me, workspaces: [{ ...workspace, role: options.role ?? 'owner' }] })
    if (p === '/api/v1/modules' && r.method === 'GET') return Response.json({ items: [newsModule, options.lb ?? lbModule] })
    if (p === '/api/v1/agents/servers') return Response.json(serverList([server]))
    if (p === `/api/v1/agents/servers/${SERVER}`) return Response.json(detail(server))
    if (p.endsWith('/ops') && r.method === 'GET') return Response.json({ available: false, reason: 'not_deployed', running: null, items: [] })
    if (p === '/api/v1/runner/overview') return Response.json(overview({ host: host({ id: HOST, name: '容器一号' }) }))
    if (p === '/api/v1/llm_bridge/installs') return Response.json({ managers: options.managers ?? [], available: options.available ?? true })
    return undefined
  })
}
const installs = (requests: Request[]) => requests.filter(r => path(r) === '/api/v1/llm_bridge/installs')

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('服务器页面上的“MCP 管理器”区块', () => {
  it('is registered by the llm_bridge module for both server pages', () => {
    expect(moduleServerPanels.agentServer.map(p => p.module)).toEqual(['llm_bridge'])
    expect(moduleServerPanels.runnerHost.map(p => p.module)).toEqual(['llm_bridge'])
    // 其他模块没有提供区块
    expect(modules.filter(m => m.serverPanels).map(m => m.key)).toEqual(['llm_bridge'])
  })

  it('lists the managers on an agent server with links to the chat and the manage page', async () => {
    const requests = setup({ managers: [
      manager(),
      manager({ bridge_id: TWO, name: '3 号', enabled: false, reachable: null, version: null, logged_in: [], deployment: install({ mode: 'same_host', port: null }) }),
      manager({ bridge_id: THREE, name: '4 号', reachable: false, version: null, logged_in: [], deployment: install({ port: 18788, outdated: true }) }),
    ] })
    await openApp(`/agent-servers?server=${SERVER}`)
    const panel = await screen.findByRole('region', { name: 'MCP 管理器' })
    expect(await within(panel).findByRole('heading', { level: 3, name: 'MCP 管理器 · 3' })).toBeVisible()
    // 排在“已部署 OpenClaw”后面，外层和页面上别的区块一样
    expect(screen.getByRole('region', { name: '已部署 OpenClaw' }).compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(panel).toHaveClass('ws-panel', 'asv-section')

    const table = within(panel).getByRole('table', { name: '机房一号上的 MCP 管理器' })
    const online = within(table).getByRole('row', { name: /^2 号/ })
    expect(within(online).getByText('在线')).toBeVisible()
    expect(online).toHaveTextContent('经 SSH 通道（服务器本机端口 18787）')
    expect(online).toHaveTextContent('OpenAI、Claude')
    expect(within(online).getByRole('link', { name: '打开聊天：2 号' })).toHaveAttribute('href', `/llm_bridge?m=${ONE}`)
    expect(within(online).getByRole('link', { name: '管理：2 号' })).toHaveAttribute('href', `/llm_bridge?view=manage&m=${ONE}`)
    const off = within(table).getByRole('row', { name: /^3 号/ })
    expect(within(off).getByText('已停用')).toBeVisible()
    expect(off).toHaveTextContent('和工作台同机（内部网络）')
    const down = within(table).getByRole('row', { name: /^4 号/ })
    expect(within(down).getByText('离线')).toBeVisible()
    expect(down).toHaveTextContent('经 SSH 通道（服务器本机端口 18788）')
    expect(down).toHaveTextContent('有新版本，可以到管理页重新部署')

    expect(within(panel).getByText(NOTE)).toBeVisible()
    expect(within(panel).getByRole('link', { name: '在这台服务器上部署 MCP 管理器' })).toHaveAttribute('href', `/llm_bridge?view=manage&deploy=agent_server:${SERVER}`)
    // 只给入口：区块里没有重启、卸载之类的操作
    expect(within(panel).getAllByRole('button').map(b => b.textContent)).toEqual(['重新检查'])
    const asked = installs(requests)
    expect(Object.fromEntries(new URL(asked[0]!.url).searchParams)).toEqual({ target_kind: 'agent_server', target_id: SERVER })
    expect(asked[0]!.headers.get('X-Workspace-Id')).toBe('workspace-a')
  })

  it('shows only the note and the deploy entry on a container server without managers', async () => {
    const requests = setup()
    await openApp('/runner?tab=settings')
    const panel = await screen.findByRole('region', { name: 'MCP 管理器' })
    expect(await within(panel).findByRole('heading', { level: 2, name: 'MCP 管理器 · 0' })).toBeVisible()
    expect(panel).toHaveClass('panel', 'runner-card')
    expect(within(panel).queryByRole('table')).not.toBeInTheDocument()
    expect(within(panel).getByText('这台服务器上还没有工作台部署的 MCP 管理器。')).toBeVisible()
    expect(within(panel).getByText(NOTE)).toBeVisible()
    expect(within(panel).getByRole('link', { name: '在这台服务器上部署 MCP 管理器' })).toHaveAttribute('href', `/llm_bridge?view=manage&deploy=runner_host:${HOST}`)
    expect(Object.fromEntries(new URL(installs(requests)[0]!.url).searchParams)).toEqual({ target_kind: 'runner_host', target_id: HOST })
    // 在这台服务器的设置里：排在“回收策略”前面
    expect(panel.compareDocumentPosition(screen.getByRole('form', { name: '回收策略' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('offers the deploy entry only to the owner, and only when the workbench can deploy', async () => {
    setup({ role: 'admin', managers: [manager({ deployment: install({ target_kind: 'runner_host', target_id: HOST, server_name: '容器一号' }) })] })
    const view = await openApp('/runner?tab=settings')
    const panel = await screen.findByRole('region', { name: 'MCP 管理器' })
    // admin 能看、能打开聊天和管理页（管理页对 admin 是只读的），不能部署
    expect(await within(panel).findByRole('link', { name: '管理：2 号' })).toHaveAttribute('href', `/llm_bridge?view=manage&m=${ONE}`)
    expect(within(panel).getByText(NOTE)).toBeVisible()
    expect(within(panel).queryByRole('link', { name: '在这台服务器上部署 MCP 管理器' })).not.toBeInTheDocument()
    view.unmount()

    // 这个工作台没有带 MCP 管理器的构建文件：owner 也没有部署入口
    setup({ available: false })
    await openApp('/runner?tab=settings')
    const second = await screen.findByRole('region', { name: 'MCP 管理器' })
    expect(await within(second).findByText(NOTE)).toBeVisible()
    expect(within(second).queryByRole('link', { name: '在这台服务器上部署 MCP 管理器' })).not.toBeInTheDocument()
  })

  it('renders nothing on either server page when the module is disabled', async () => {
    const requests = setup({ lb: { ...lbModule, enabled: false } })
    const view = await openApp(`/agent-servers?server=${SERVER}`)
    expect(await screen.findByRole('region', { name: '已部署 OpenClaw' })).toBeVisible()
    expect(await screen.findByRole('region', { name: '整机负载' })).toBeVisible()
    expect(screen.queryByRole('region', { name: 'MCP 管理器' })).not.toBeInTheDocument()
    view.unmount()

    await openApp('/runner?tab=settings')
    expect(await screen.findByRole('form', { name: '回收策略' })).toBeVisible()
    expect(screen.queryByRole('region', { name: 'MCP 管理器' })).not.toBeInTheDocument()
    await waitFor(() => expect(requests.some(r => path(r) === '/api/v1/runner/overview')).toBe(true))
    expect(installs(requests)).toEqual([])
  })
})
