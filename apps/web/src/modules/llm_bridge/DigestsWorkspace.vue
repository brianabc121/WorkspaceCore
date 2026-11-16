<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { BookOpen, ChevronDown, ExternalLink, FileText, Folder, Info, MoreHorizontal, RefreshCw, Settings, Sparkles } from '@lucide/vue'
import ManagementPage from '../../components/management/ManagementPage.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import { useNote } from '../../features/kb/api'
import { bridgeError, useLlmBridge } from './api'
import { DIGEST_REASON, DIGEST_STATE, chatError, listTime, useLlmChat, type DigestItem } from './chat/chatApi'
import { answerHtml } from './chat/markdown'
import '../../features/kb/kb.css'

// 会话纪要（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 5.2、5.3）：每个大模型会话一篇，工作台按会话记录整理、
// 写在知识库的“会话纪要”文件夹里，标为未确认。左边按状态、按管理器列出每一篇；右边预览选中的那篇：
// 按固定的几栏（结论和决定、没解决的问题、涉及的文件和命令、后续要做的、回合清单）分开显示，回合可以展开。
// 可以转成正式笔记（人确认）、删除（进回收站）、关掉或重新打开某个会话的自动更新、现在整理一次。
const props = defineProps<{ scope: string }>()
const chat = useLlmChat(props.scope), bridgesApi = useLlmBridge(props.scope)
const bridges = bridgesApi.useBridges()
const TABS = [{ key: null, name: '全部' }, { key: 'active', name: '自动更新' }, { key: 'off', name: '已关闭' }, { key: 'converted', name: '已转正式笔记' }, { key: 'edited', name: '手动修改' }] as const
const MORE = [{ key: 'deleted', name: '已删除' }, { key: 'purged', name: '随保留期清掉' }] as const
const state = ref<string | null>(null), bridge = ref<string | null>(null)
const digests = chat.useDigests(state, ref(true), bridge)
const items = computed(() => digests.data.value?.digests ?? [])

// “更多”里的两种状态：点开是一组按钮
const moreOpen = ref(false), moreBox = ref<HTMLElement | null>(null)
const moreTab = computed(() => MORE.find(m => m.key === state.value) ?? null)
function outside(event: MouseEvent) { if (moreOpen.value && moreBox.value && !moreBox.value.contains(event.target as Node)) moreOpen.value = false }
document.addEventListener('click', outside, true)
onBeforeUnmount(() => document.removeEventListener('click', outside, true))
function pick(key: string | null) { state.value = key; moreOpen.value = false }

// 选中的那篇：列表变了、选中的不在了，就选第一篇
const selectedId = ref<string | null>(null)
watch(items, list => { if (!list.some(d => d.id === selectedId.value)) selectedId.value = list[0]?.id ?? null }, { immediate: true })
const selected = computed(() => items.value.find(d => d.id === selectedId.value) ?? null)
const hasNote = (d: DigestItem | null) => !!d?.note_id && !['deleted', 'purged'].includes(d.state)
const noteId = computed(() => (hasNote(selected.value) ? selected.value!.note_id! : null))
const note = useNote(props.scope, noteId)

// 纪要正文按“## 栏目”分开；“回合清单”再按“### 第 N 回合”分开，最新的在前
type Turn = { title: string; status: string; link: string | null; body: string }
const parsed = computed(() => {
  const body = note.data.value?.body ?? ''
  const parts = body.split(/^## /m).slice(1).map(part => { const nl = part.indexOf('\n'); return { title: (nl < 0 ? part : part.slice(0, nl)).trim(), body: nl < 0 ? '' : part.slice(nl + 1).trim() } })
  const list = parts.find(p => p.title.startsWith('回合清单'))
  const [listNote, ...blocks] = (list?.body ?? '').split(/^### /m)
  const turns: Turn[] = blocks.map(block => {
    const nl = block.indexOf('\n'), head = (nl < 0 ? block : block.slice(0, nl)).trim()
    const link = /\[打开\]\(([^)\s]+)\)\s*$/.exec(head)?.[1] ?? null
    const plain = head.replace(/\s*\[打开\]\([^)]*\)\s*$/, '')
    const found = /^(第 \d+ 回合)（[^，）]*，?([^）]*)）/.exec(plain)
    return { title: found?.[1] ?? plain, status: found?.[2] ?? '', link: link && link.startsWith('/') ? link : null, body: nl < 0 ? '' : block.slice(nl + 1).trim() }
  }).reverse()
  return { sections: parts.filter(p => p !== list), turns, turnsNote: listNote?.trim() ?? '', hasTurns: !!list }
})
const openTurn = ref<number | null>(0)
watch(selectedId, () => { openTurn.value = 0 })

const error = ref(''), notice = ref(''), busy = ref('')
const menuOpen = ref(false), menuBox = ref<HTMLElement | null>(null)
function outsideMenu(event: MouseEvent) { if (menuOpen.value && menuBox.value && !menuBox.value.contains(event.target as Node)) menuOpen.value = false }
document.addEventListener('click', outsideMenu, true)
onBeforeUnmount(() => document.removeEventListener('click', outsideMenu, true))
async function act(item: DigestItem, action: 'promote' | 'delete' | 'off' | 'on' | 'write') {
  menuOpen.value = false
  const ask = {
    promote: `把《${item.note_title}》转成正式笔记？\n\n转成后它就是你确认过的普通笔记，放到知识库的根目录，之后不再自动更新。`,
    delete: `删除《${item.note_title}》？\n\n纪要放进知识库的回收站（30 天内可以恢复），之后不再为这个会话整理纪要。`,
    off: '关闭这个会话的自动更新？\n\n之后不再为它整理；已有的那篇不动。',
    on: '', write: '',
  }[action]
  if (ask && !window.confirm(ask)) return
  busy.value = item.id; error.value = notice.value = ''
  try {
    await chat.digest(item.bridge_id, item.session_id, action)
    notice.value = { promote: '已转成正式笔记。', delete: '纪要已放进回收站。', off: '已关闭这个会话的自动更新。', on: '已重新打开自动更新。', write: '已整理。' }[action]
    if (action === 'write') void note.refetch()
  } catch (e) { error.value = chatError(e) } finally { busy.value = '' }
}
const seconds = (at: string | null | undefined) => (at ? Date.parse(at) / 1000 : 0)
// 今天的写“今天 16:40”，昨天的写“昨天 16:28”，更早的写月日
const when = (at: string | null | undefined) => { if (!at) return '—'; const t = listTime(seconds(at)); return /^\d\d:\d\d$/.test(t) ? `今天 ${t}` : t }
const full = (at: string | null | undefined) => (at ? new Date(at).toLocaleString('zh-CN', { hour12: false }) : '—')
const badge = (d: DigestItem) => (d.state === 'active' ? { text: '未确认', tone: 'is-amber' } : d.state === 'converted' ? { text: '正式笔记', tone: 'is-green' } : d.state === 'edited' ? { text: '人改过', tone: 'is-muted' } : null)
const STATUS: Record<string, string> = { active: '自动更新', off: '已关闭', converted: '已转正式笔记', edited: '手动修改', deleted: '已删除', purged: '已清掉' }
</script>

<template>
  <ManagementPage embedded class="lb-page dg-page" title="会话纪要" description="回顾会话记录，将值得保留的内容转成正式笔记">
    <template #actions>
      <RouterLink class="secondary-button lb-head-btn" :to="{ path: '/llm_bridge', query: { view: 'manage', tab: 'settings' } }" title="在“管理器与账号 → 运行设置”里打开或关掉整个纪要功能"><Settings aria-hidden="true" />纪要设置</RouterLink>
    </template>
    <p class="dg-banner"><Info aria-hidden="true" />纪要按记录整理，不额外调用模型；未经确认，默认不带入提问。智能体的会话放进它自己的知识库，人的放进公共知识库，关联了项目的放进项目知识库。</p>
    <ActionFeedback :error="error" :notice="notice" />

    <div class="dg-box">
      <div class="dg-tabs">
        <div role="tablist" aria-label="按状态看纪要">
          <button v-for="t in TABS" :key="t.name" type="button" role="tab" :aria-selected="state === t.key" @click="pick(t.key)">{{ t.name }}</button>
        </div>
        <div ref="moreBox" class="lb-menu-wrap">
          <button type="button" class="dg-more" :class="{ 'is-on': !!moreTab }" aria-haspopup="true" :aria-expanded="moreOpen" @click="moreOpen = !moreOpen">{{ moreTab?.name ?? '更多' }}<ChevronDown aria-hidden="true" /></button>
          <div v-if="moreOpen" class="lb-menu dg-more-menu" role="group" aria-label="更多状态">
            <button v-for="m in MORE" :key="m.key" type="button" @click="pick(m.key)">{{ m.name }}</button>
          </div>
        </div>
      </div>

      <div class="dg-layout">
        <section class="dg-list" aria-label="纪要列表">
          <div class="dg-tools">
            <select v-model="bridge" aria-label="按 MCP 管理器筛选">
              <option :value="null">全部管理器</option>
              <option v-for="b in bridges.data.value ?? []" :key="b.id" :value="b.id">{{ b.name }}</option>
            </select>
            <button type="button" class="secondary-button lb-head-btn" :disabled="digests.isFetching.value" @click="digests.refetch()"><RefreshCw aria-hidden="true" />刷新</button>
          </div>
          <h3>纪要列表</h3>
          <p v-if="digests.isPending.value" role="status" class="data-state">正在读取会话纪要…</p>
          <div v-else-if="digests.error.value" class="data-state">
            <p role="alert">{{ bridgeError(digests.error.value) }}</p>
            <button type="button" class="secondary-button" @click="digests.refetch()">重试</button>
          </div>
          <p v-else-if="!items.length" class="dg-none">{{ state || bridge ? '没有符合条件的纪要。' : '还没有会话纪要。会话有 3 个以上回合，并且平台压缩了上下文、会话归档、或者 24 小时没有新回合时会自动整理。' }}</p>
          <ul v-else class="dg-items">
            <li v-for="d in items" :key="d.id">
              <button type="button" class="dg-item" :aria-current="d.id === selectedId ? 'true' : undefined" @click="selectedId = d.id">
                <span class="dg-item-icon" aria-hidden="true"><FileText /></span>
                <span class="dg-item-main">
                  <span class="dg-item-top">
                    <strong>{{ d.session_title || d.note_title || '（没有标题）' }}</strong>
                    <span v-if="badge(d)" class="lb-badge" :class="badge(d)!.tone">{{ badge(d)!.text }}</span>
                    <span class="dg-item-state" :data-state="d.state">{{ STATUS[d.state] ?? d.state }}</span>
                  </span>
                  <span class="dg-item-meta">{{ d.owner || '—' }} · {{ d.bridge_name }}</span>
                  <span class="dg-item-foot"><span class="dg-item-space"><Folder aria-hidden="true" />{{ d.space_name || '—' }}</span><span>{{ when(d.written_at) }} · {{ d.turns_written }} 个回合</span></span>
                </span>
              </button>
            </li>
          </ul>
        </section>

        <section class="dg-preview" aria-label="纪要预览">
          <template v-if="selected">
            <div class="dg-preview-top"><span>纪要预览</span><span>最新整理在前</span></div>
            <div class="dg-preview-title">
              <h3>{{ selected.note_title || selected.session_title || '（没有标题）' }}</h3>
              <span v-if="badge(selected)" class="lb-badge" :class="badge(selected)!.tone">{{ badge(selected)!.text }}</span>
            </div>
            <dl class="dg-meta">
              <dt>原会话</dt><dd><RouterLink class="asv-link" :to="{ path: '/llm_bridge', query: { m: selected.bridge_id, s: selected.session_id } }">{{ selected.session_title || selected.session_id }}</RouterLink></dd>
              <dt>知识库</dt><dd><Folder aria-hidden="true" class="dg-inline-icon" />{{ selected.space_name || '—' }}</dd>
              <dt>已整理</dt><dd>{{ selected.turns_written }} 个回合</dd>
              <dt>管理器</dt><dd>{{ selected.bridge_name }}</dd>
              <dt>整理触发</dt><dd>{{ selected.reason ? DIGEST_REASON[selected.reason] ?? selected.reason : '—' }}</dd>
              <dt>整理时间</dt><dd :title="full(selected.written_at)">{{ when(selected.written_at) }}</dd>
            </dl>
            <div class="dg-actions">
              <button v-if="selected.state === 'active' && selected.note_id" type="button" class="primary-button lb-head-btn" :disabled="!!busy" @click="act(selected, 'promote')"><Sparkles aria-hidden="true" />转成正式笔记</button>
              <RouterLink class="secondary-button lb-head-btn" :to="{ path: '/llm_bridge', query: { m: selected.bridge_id, s: selected.session_id } }"><ExternalLink aria-hidden="true" />查看原会话</RouterLink>
              <RouterLink v-if="hasNote(selected)" class="secondary-button lb-head-btn" :to="`/knowledge/notes/${selected.note_id}`"><BookOpen aria-hidden="true" />知识库中打开</RouterLink>
              <div ref="menuBox" class="lb-menu-wrap">
                <button type="button" class="secondary-button lb-head-btn dg-dots" aria-label="更多操作" aria-haspopup="true" :aria-expanded="menuOpen" @click="menuOpen = !menuOpen"><MoreHorizontal aria-hidden="true" /></button>
                <div v-if="menuOpen" class="lb-menu" role="group" aria-label="更多操作">
                  <button v-if="!['off', 'converted', 'edited', 'deleted', 'purged'].includes(selected.state)" type="button" :disabled="!!busy" @click="act(selected, 'write')">现在整理一次</button>
                  <button v-if="selected.note_id && ['active', 'off', 'edited'].includes(selected.state)" type="button" class="is-danger" :disabled="!!busy" @click="act(selected, 'delete')">删除纪要</button>
                  <p v-if="['converted', 'deleted', 'purged'].includes(selected.state)" class="dg-menu-none">没有别的操作。</p>
                </div>
              </div>
            </div>

            <div class="dg-body">
              <p v-if="!hasNote(selected)" class="dg-none">{{ DIGEST_STATE[selected.state] ?? selected.state }}：没有可以预览的内容。</p>
              <p v-else-if="note.isPending.value" role="status" class="data-state">正在读取纪要内容…</p>
              <div v-else-if="note.error.value" class="data-state"><p role="alert">读不到纪要内容：{{ chatError(note.error.value) }}</p><button type="button" class="secondary-button" @click="note.refetch()">重试</button></div>
              <template v-else>
                <section v-for="sec in parsed.sections" :key="sec.title" class="dg-section" :aria-label="sec.title">
                  <h4>{{ sec.title }}</h4>
                  <!-- eslint-disable-next-line vue/no-v-html -- answerHtml 已用 DOMPurify 消毒，不允许原始 HTML（chat/markdown.ts） -->
                  <div class="kb-md dg-md" v-html="answerHtml(sec.body)"></div>
                </section>
                <section v-if="parsed.hasTurns" class="dg-section" aria-label="回合清单">
                  <h4>回合清单（{{ parsed.turns.length }}）</h4>
                  <p v-if="parsed.turnsNote" class="dg-none">{{ parsed.turnsNote.replace(/^（|）$/g, '') }}</p>
                  <ol class="dg-turns">
                    <li v-for="(t, i) in parsed.turns" :key="t.title" :class="{ 'is-open': openTurn === i }">
                      <button type="button" class="dg-turn-head" :aria-expanded="openTurn === i" @click="openTurn = openTurn === i ? null : i">
                        <ChevronDown aria-hidden="true" /><strong>{{ t.title }}</strong><span v-if="t.status" class="dg-turn-status">· {{ t.status }}</span>
                      </button>
                      <div v-if="openTurn === i" class="dg-turn-body">
                        <!-- eslint-disable-next-line vue/no-v-html -- 同上，已消毒 -->
                        <div class="kb-md dg-md" v-html="answerHtml(t.body)"></div>
                        <RouterLink v-if="t.link" class="asv-link" :to="t.link">打开这一回合</RouterLink>
                      </div>
                    </li>
                  </ol>
                </section>
              </template>
            </div>

            <div class="dg-foot">
              <p class="dg-foot-state"><span class="lb-dot" :data-state="selected.state === 'active' ? 'ok' : 'idle'" aria-hidden="true" />{{ selected.state === 'active' ? '自动更新中，关闭后保留已有内容。' : DIGEST_STATE[selected.state] ?? selected.state }}</p>
              <button v-if="selected.state === 'active'" type="button" class="secondary-button" :disabled="!!busy" @click="act(selected, 'off')">关闭自动更新</button>
              <button v-else-if="selected.state === 'off'" type="button" class="secondary-button" :disabled="!!busy" @click="act(selected, 'on')">重新打开自动更新</button>
            </div>
            <p class="dg-tip"><Info aria-hidden="true" />转为正式笔记后不再自动更新；删除后进入知识库回收站。</p>
          </template>
          <p v-else class="dg-none dg-empty">选一篇纪要在这里预览。</p>
        </section>
      </div>
    </div>
  </ManagementPage>
</template>
