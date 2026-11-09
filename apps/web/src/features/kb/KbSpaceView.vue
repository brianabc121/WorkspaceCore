<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { CircleAlert, CircleCheck, FilePlus2, Send, ShieldCheck, SquarePen, Trash2, Waypoints } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf } from '../../services/api'
import { imError, useImStatus } from '../im/queries'
import { CHANGE_LABELS, GUARD_LABELS, NOTE_TYPE_LABELS, initial, kbError, useChanges, useKb, useNote, useNotes, useSpaceActivity, useSpaces, type Space } from './api'
import { noteHtml, when } from './format'
import KbMobileHeader from './KbMobileHeader.vue'
import KbTabs from './KbTabs.vue'
import KbWeekChart from './KbWeekChart.vue'
import { spaceLink } from './KbSpaceList.vue'

// 项目知识库 / 公共知识库主页（T-0099，效果图“电脑端 · 项目知识库（认知对齐）”）：
// 项目简报、参与的智能体（是否读过最新简报）、写入规则、最近的变化、笔记目录。
// T-0100：近 8 周人 / 智能体的修改趋势；管理员可以用 IM 把“去读最新简报”直接发给还没读的智能体。
const auth = useAuthStore(), route = useRoute(), router = useRouter(), scope = auth.workspaceId!
const canEdit = computed(() => ['owner', 'admin', 'member'].includes(auth.currentWorkspace?.role ?? ''))
const kb = useKb(scope)
const spaces = useSpaces(scope)
const items = computed(() => spaces.data.value?.items ?? [])
const id = computed(() => typeof route.params.id === 'string' && route.params.id ? route.params.id : null)
// 没带 id：打开第一个项目知识库（没有项目时打开公共库）；智能体的知识库转到智能体主页
watch([items, id], () => {
  if (!items.value.length) return
  const target = id.value ? items.value.find(s => s.id === id.value) : (items.value.find(s => s.kind === 'project' && !s.archived) ?? items.value.find(s => s.kind === 'public'))
  if (target && (!id.value || target.kind === 'agent')) void router.replace(spaceLink(target))
}, { immediate: true })
const space = computed(() => items.value.find(s => s.id === id.value) ?? null)
const switcher = computed(() => items.value.filter(s => s.kind !== 'agent'))

const briefId = computed(() => space.value?.brief_note_id ?? null)
const brief = useNote(scope, briefId)
const briefHtml = computed(() => brief.data.value ? noteHtml(brief.data.value) : '')
const changes = useChanges(scope, computed(() => ({ space_id: id.value ?? undefined })))
const recent = computed(() => (changes.data.value?.pages[0]?.items ?? []).slice(0, 6))
const notes = useNotes(scope, computed(() => ({ space_id: id.value ?? undefined })), computed(() => !!id.value))
const noteItems = computed(() => notes.data.value?.pages.flatMap(p => p.items) ?? [])
const activity = useSpaceActivity(scope, id)
const activityWeeks = computed(() => (activity.data.value?.weeks ?? []).map(w => ({ week_start: w.week_start, values: { human: w.human, agent: w.agent } })))
const activitySeries = [{ key: 'human', label: '人' }, { key: 'agent', label: '智能体' }]

// 参与的智能体（管理员可以加、改权限、移出）
const candidates = computed(() => items.value.filter(s => s.kind === 'agent' && s.instance_id && !s.archived && !space.value?.agents.some(a => a.instance_id === s.instance_id)))
const adding = ref(''), addAccess = ref<'read_write' | 'read'>('read_write')
const busy = ref(false), failure = ref(''), message = ref('')
function flash(text: string) { message.value = text; setTimeout(() => { if (message.value === text) message.value = '' }, 4000) }
async function act(work: () => Promise<unknown>, done: string) {
  if (busy.value) return
  busy.value = true; failure.value = ''
  try { await work(); flash(done) } catch (error) { failure.value = kbError(error) } finally { busy.value = false }
}
const addAgent = () => space.value && adding.value && act(async () => { await kb.putAgent(space.value!.id, adding.value, addAccess.value); adding.value = '' }, '已加入，智能体现在可以读写本项目的知识库')
const setAccess = (instanceId: string, access: 'read_write' | 'read') => space.value && act(() => kb.putAgent(space.value!.id, instanceId, access), access === 'read' ? '已改为只读' : '已改为可以读写')
const removeAgent = (instanceId: string, name: string) => space.value && window.confirm(`把 ${name} 移出本项目？它将不能再读写本项目的知识库，已写的笔记保留。`) && act(() => kb.removeAgent(space.value!.id, instanceId), `已移出 ${name}`)
const setPublicAccess = (access: 'read_write' | 'read' | 'none') => space.value && act(() => kb.updateSpace(space.value!.id, { version: space.value!.version, agent_access: access }), '已更新智能体的权限')
const setAll = (access: 'read_write' | 'read') => space.value && act(async () => { for (const a of space.value!.agents) if (a.access !== access) await kb.putAgent(space.value!.id, a.instance_id, access) }, access === 'read' ? '参与的智能体都改为只读' : '参与的智能体都可以读写')
// 删除的自动暂停是按智能体记的（在它自己的知识库上）
const pausedAgents = computed(() => items.value.filter(s => s.kind === 'agent' && s.deletes_paused_at && space.value?.agents.some(a => a.instance_id === s.instance_id)))
const resume = (spaceId: string, version: number) => act(() => kb.updateSpace(spaceId, { version, resume_deletes: true }), '已恢复删除')
const editingDesc = ref(false), desc = ref('')
function editDesc() { desc.value = space.value?.description ?? ''; editingDesc.value = true }
const saveDesc = () => space.value && act(async () => { await kb.updateSpace(space.value!.id, { version: space.value!.version, description: desc.value.trim() }); editingDesc.value = false }, '说明已保存')
function readState(a: { brief_version_read: number | null }) {
  const latest = space.value?.brief_version
  if (!latest) return { ok: true, text: '还没有简报' }
  if (a.brief_version_read === latest) return { ok: true, text: `已读最新简报（第 ${latest} 版）` }
  if (a.brief_version_read) return { ok: false, text: `简报更新后还没读（停在第 ${a.brief_version_read} 版）` }
  return { ok: false, text: '还没读过简报' }
}
function follow(event: MouseEvent) {
  const a = (event.target as HTMLElement).closest('a'), href = a?.getAttribute('href')
  if (!a || !href || event.button !== 0 || event.metaKey || event.ctrlKey || a.target === '_blank' || !href.startsWith('/')) return
  event.preventDefault(); void router.push(href)
}

// “现在发给它”（T-0100，方案 3.9 第 1 条）：经工作台的 IM 接口发一条固定格式的消息（浏览器不直连 OpenIM）。
// 同一个智能体、同一版简报只用一个 Idempotency-Key：结果未知时重试也不会发两次。
const im = useImStatus(scope)
const canRemind = computed(() => auth.canManage && !!im.data.value?.available && space.value?.kind === 'project' && !!space.value.project_id && !!space.value.brief_version && !space.value.archived)
const remindKeys = new Map<string, string>()
const reminded = ref<string[]>([]), reminding = ref<string | null>(null)
const remindTag = (instanceId: string) => `${instanceId}:${space.value?.brief_version ?? 0}`
const remindText = (s: Space) => `项目「${s.name}」的简报更新到了第 ${s.brief_version} 版。请先用 knowledge_brief 工具（project_id: ${s.project_id}）读最新简报，再继续这个项目的任务。`
async function remind(instanceId: string, name: string) {
  const s = space.value
  if (!s || !canRemind.value || reminding.value) return
  const tag = remindTag(instanceId)
  let key = remindKeys.get(tag)
  if (!key) { key = crypto.randomUUID(); remindKeys.set(tag, key) }
  reminding.value = instanceId; failure.value = ''
  const header = { 'X-Workspace-Id': scope }
  try {
    const conversation = dataOf(await auth.api.POST('/api/v1/im/conversations', { params: { header }, body: { instance_id: instanceId } }))
    dataOf(await auth.api.POST('/api/v1/im/conversations/{conversation_id}/messages', { params: { header: { ...header, 'Idempotency-Key': key }, path: { conversation_id: conversation.conversation_id } }, body: { kind: 'text', text: remindText(s), at_im_ids: [] } }))
    reminded.value = [...reminded.value, tag]
    flash(`已通过 IM 发给 ${name}`)
  } catch (error) { failure.value = imError(error) } finally { reminding.value = null }
}
const newBriefHref = computed(() => space.value ? `/knowledge/notes/new?space=${space.value.id}&type=brief&title=${encodeURIComponent(space.value.name + ' 简报')}` : '')
</script>

<template>
  <div class="kb-view is-mobile-main">
    <main class="kb-main">
      <KbMobileHeader :title="space?.name ?? '知识库'" back="/knowledge" back-label="返回知识库" />
      <KbTabs />
      <p v-if="spaces.isPending.value" class="kb-empty" role="status">正在加载…</p>
      <p v-else-if="spaces.error.value" class="kb-error" role="alert" style="margin: 16px">{{ kbError(spaces.error.value) }}</p>
      <div v-else-if="space" class="kb-page">
        <nav class="kb-switcher" aria-label="切换知识库">
          <RouterLink v-for="s in switcher" :key="s.id" :to="`/knowledge/spaces/${s.id}`" :aria-current="s.id === space.id ? 'page' : undefined"><span class="kb-dot is-small" :class="`k-${s.kind}`" aria-hidden="true" /> {{ s.name }}</RouterLink>
        </nav>
        <header class="kb-hero">
          <div class="kb-stack" style="gap: 4px">
            <p class="kb-meta" style="margin: 0; display: flex; align-items: center; gap: 6px"><span class="kb-dot is-small" :class="`k-${space.kind}`" aria-hidden="true" />{{ space.kind === 'project' ? `项目知识库 · 跟随项目「${space.name}」自动创建` : '公共知识库 · 整个工作空间共用' }}<template v-if="space.archived"> · 项目已归档，智能体只读</template></p>
            <h1>{{ space.name }}</h1>
            <p class="kb-meta kb-num" style="margin: 0">{{ space.notes }} 篇笔记<template v-if="space.kind === 'project'"> · {{ space.agents.length }} 个智能体参与</template><template v-if="space.last_updated_at"> · 最近更新 {{ when(space.last_updated_at) }}</template></p>
          </div>
          <div class="kb-actions">
            <RouterLink :to="{ path: '/knowledge/graph', query: { focus: space.id } }" class="kb-btn"><Waypoints aria-hidden="true" />在图谱中查看</RouterLink>
            <RouterLink v-if="canEdit" :to="`/knowledge/notes/new?space=${space.id}`" class="kb-btn is-primary"><FilePlus2 aria-hidden="true" />新建笔记</RouterLink>
          </div>
        </header>
        <p v-if="failure" class="kb-error" role="alert">{{ failure }}</p>
        <div v-for="a in pausedAgents" :key="a.id" class="kb-notice is-warning" role="alert">
          <CircleAlert aria-hidden="true" /><span class="kb-grow">{{ a.name }} 在 {{ when(a.deletes_paused_at) }}删除得太多，它的删除已自动暂停（写入不受影响）。先到变更记录里核对。</span>
          <RouterLink to="/knowledge/changes?tab=trash" class="kb-btn is-small">查看回收站</RouterLink>
          <button v-if="auth.canManage" type="button" class="kb-btn is-small" :disabled="busy" @click="resume(a.id, a.version)">恢复删除</button>
        </div>
        <div class="kb-grid-2">
          <!-- 项目简报 / 公共库说明 -->
          <section v-if="space.kind === 'project'" class="kb-card" aria-label="项目简报">
            <div class="kb-card-head">
              <div class="kb-stack"><h2>项目简报</h2><span class="kb-meta">参与本项目的智能体接到任务前先读这一页（用 knowledge_brief 工具）。<template v-if="brief.data.value"> 第 {{ brief.data.value.version }} 版 · {{ brief.data.value.updated_by.name }} {{ when(brief.data.value.updated_at) }}更新</template></span></div>
              <RouterLink v-if="canEdit && briefId" :to="`/knowledge/notes/${briefId}/edit`" class="kb-btn is-small"><SquarePen aria-hidden="true" />编辑简报</RouterLink>
            </div>
            <!-- eslint-disable-next-line vue/no-v-html -- 已经过 DOMPurify 消毒（markdown.ts） -->
            <div v-if="brief.data.value" class="kb-md" style="font-size: 14px" @click="follow" v-html="briefHtml" />
            <div v-else-if="!briefId" class="kb-notice">
              <span class="kb-grow">还没有简报。写下目标、已定的事、规矩、分工和别做的事，让参与的智能体认知一致。</span>
              <RouterLink v-if="canEdit" :to="newBriefHref" class="kb-btn is-small is-primary">写简报</RouterLink>
            </div>
            <p v-else class="kb-meta" role="status">正在加载简报…</p>
          </section>
          <section v-else class="kb-card" aria-label="说明">
            <div class="kb-card-head"><h2>说明</h2><button v-if="auth.canManage && !editingDesc" type="button" class="kb-btn is-small" @click="editDesc">编辑</button></div>
            <template v-if="editingDesc">
              <textarea v-model="desc" class="kb-textarea" maxlength="2000" aria-label="知识库说明" />
              <div style="display: flex; justify-content: flex-end; gap: 8px"><button type="button" class="kb-btn is-small" @click="editingDesc = false">取消</button><button type="button" class="kb-btn is-small is-primary" :disabled="busy" @click="saveDesc">保存</button></div>
            </template>
            <p v-else style="margin: 0; white-space: pre-wrap">{{ space.description || '放所有智能体和人都该知道的东西：安全红线、命名规范、通用流程、术语。' }}</p>
          </section>

          <!-- 参与的智能体 / 公共库的智能体权限 -->
          <section v-if="space.kind === 'project'" class="kb-card" aria-label="参与的智能体">
            <div class="kb-card-head"><h2>参与的智能体</h2></div>
            <ul class="kb-agents">
              <li v-for="a in space.agents" :key="a.instance_id">
                <span class="kb-avatar" aria-hidden="true">{{ initial(a.name) }}</span>
                <span class="kb-grow">
                  <span style="display: flex; flex-wrap: wrap; align-items: center; gap: 8px"><RouterLink :to="`/knowledge/agents/${a.instance_id}`"><strong>{{ a.name }}</strong></RouterLink><span class="kb-badge">{{ a.access === 'read_write' ? '读、写、改、删' : '只读' }}</span></span>
                  <span class="kb-meta" style="display: flex; align-items: center; gap: 4px"><CircleCheck v-if="readState(a).ok" aria-hidden="true" style="width: 14px; height: 14px; stroke: var(--kb-good)" /><CircleAlert v-else aria-hidden="true" style="width: 14px; height: 14px; stroke: var(--kb-warning)" />{{ readState(a).text }}</span>
                  <span v-if="!readState(a).ok" class="kb-meta">下次它读写知识库时会被提醒先读简报；读取本项目的任务时也能看到简报有更新。</span>
                  <span v-if="!readState(a).ok && canRemind" style="display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 4px">
                    <span v-if="reminded.includes(remindTag(a.instance_id))" class="kb-meta">已通过 IM 发给它，等它读完这里会变成“已读”。</span>
                    <button v-else type="button" class="kb-btn is-small" :disabled="!!reminding" @click="remind(a.instance_id, a.name)"><Send aria-hidden="true" />{{ reminding === a.instance_id ? '正在发送…' : '现在发给它' }}</button>
                  </span>
                  <span v-if="auth.canManage" style="display: flex; gap: 6px; margin-top: 4px">
                    <button type="button" class="kb-btn is-small" :disabled="busy" @click="setAccess(a.instance_id, a.access === 'read' ? 'read_write' : 'read')">{{ a.access === 'read' ? '改为可以读写' : '改为只读' }}</button>
                    <button type="button" class="kb-btn is-small is-danger" :disabled="busy" @click="removeAgent(a.instance_id, a.name)"><Trash2 aria-hidden="true" />移出</button>
                  </span>
                </span>
              </li>
            </ul>
            <p v-if="!space.agents.length" class="kb-meta" style="margin: 0">还没有智能体参与。加入后它们可以直接读写本项目的知识库（不用审核）。</p>
            <div v-if="auth.canManage && candidates.length" style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center">
              <label class="kb-sr" for="kb-add-agent">添加智能体</label>
              <select id="kb-add-agent" v-model="adding" class="kb-select" style="flex: 1 1 160px"><option value="">选择智能体…</option><option v-for="c in candidates" :key="c.instance_id!" :value="c.instance_id!">{{ c.name }}</option></select>
              <label class="kb-sr" for="kb-add-access">权限</label>
              <select id="kb-add-access" v-model="addAccess" class="kb-select"><option value="read_write">读、写、改、删</option><option value="read">只读</option></select>
              <button type="button" class="kb-btn is-small is-primary" :disabled="busy || !adding" @click="addAgent">添加</button>
            </div>
            <p class="kb-meta" style="margin: 0">只有这里列出的智能体能读写本项目的知识库。它们的每次修改都记在“变更记录”里，可以撤销。</p>
          </section>
          <section v-else class="kb-card" aria-label="智能体的权限">
            <h2 style="font-size: 15px">智能体的权限</h2>
            <fieldset class="kb-fieldset" :disabled="!auth.canManage || busy">
              <legend>所有智能体对公共知识库可以</legend>
              <label v-for="o in [{ v: 'read_write', t: '读、写、改、删', d: '不用审核，直接生效。每次修改留版本，删除进回收站 30 天。' }, { v: 'read', t: '只读', d: '只能检索和阅读，不能改动。' }, { v: 'none', t: '不能访问', d: '智能体看不到公共知识库。' }] as const" :key="o.v" class="kb-radio">
                <input type="radio" name="kb-public-access" :checked="space.agent_access === o.v" @change="setPublicAccess(o.v)"><span class="kb-stack"><strong>{{ o.t }}</strong><span class="kb-meta">{{ o.d }}</span></span>
              </label>
            </fieldset>
          </section>
        </div>

        <div class="kb-grid-2">
          <section class="kb-card" aria-label="笔记">
            <div class="kb-card-head"><h2>笔记</h2><RouterLink :to="{ path: '/knowledge/notes', query: { space: space.id } }" class="kb-meta">全部 {{ space.notes }} 篇</RouterLink></div>
            <ul class="kb-list">
              <li v-for="n in noteItems.slice(0, 12)" :key="n.id"><RouterLink :to="`/knowledge/notes/${n.id}`" class="kb-list-item"><span class="kb-grow">{{ n.title }}</span><span class="kb-meta">{{ NOTE_TYPE_LABELS[n.type] }} · {{ when(n.updated_at) }}</span></RouterLink></li>
            </ul>
            <p v-if="!notes.isPending.value && !noteItems.length" class="kb-meta" style="margin: 0">还没有笔记。</p>
          </section>
          <section class="kb-card" aria-label="写入规则">
            <h2 style="font-size: 15px">本知识库的写入规则</h2>
            <fieldset v-if="space.kind === 'project'" class="kb-fieldset" :disabled="!auth.canManage || busy || !space.agents.length">
              <legend>参与的智能体可以（一次改全部；单个智能体在上面改）</legend>
              <label v-for="o in [{ v: 'read_write', t: '读、写、改、删', d: '不用审核，直接生效。每次修改留版本，删除进回收站 30 天。' }, { v: 'read', t: '只读', d: '只能检索和阅读，不能改动。' }] as const" :key="o.v" class="kb-radio">
                <input type="radio" name="kb-project-access" :checked="space.agents.length > 0 && space.agents.every(a => a.access === o.v)" @change="setAll(o.v)"><span class="kb-stack"><strong>{{ o.t }}</strong><span class="kb-meta">{{ o.d }}</span></span>
              </label>
            </fieldset>
            <div class="kb-notice"><ShieldCheck aria-hidden="true" style="stroke: var(--kb-good)" /><span class="kb-grow">安全保护一直开着：写入里有密钥会被拦下；每个智能体每小时最多写 20 次、删 5 篇，删除超过上限会自动暂停并通知管理员；每篇最多 20 KB。要让某个智能体只读所有知识库，到它的主页关掉“允许写入和删除”。</span></div>
          </section>
        </div>

        <div class="kb-grid-2">
          <section class="kb-card" aria-label="最近的变化">
            <div class="kb-card-head"><h2>最近的变化</h2><RouterLink to="/knowledge/changes" class="kb-meta">变更记录</RouterLink></div>
            <ul class="kb-timeline">
              <li v-for="c in recent" :key="c.id">
                <span class="kb-meta">{{ when(c.created_at) }}</span>
                <span>{{ c.author.name }}{{ c.kind === 'guard' ? (c.guard ? ` · ${GUARD_LABELS[c.guard]}：${c.detail}` : '') : `${c.change ? CHANGE_LABELS[c.change] : ''}了` }}<RouterLink v-if="c.kind === 'revision' && c.note_id && !c.note_deleted" :to="`/knowledge/notes/${c.note_id}`">《{{ c.note_title }}》</RouterLink><template v-else-if="c.note_title">《{{ c.note_title }}》</template><span v-if="c.reason" class="kb-meta">：“{{ c.reason }}”</span></span>
                <span class="kb-meta">{{ c.revertable ? '可撤销' : c.version ? `第 ${c.version} 版` : '' }}</span>
              </li>
            </ul>
            <p v-if="!changes.isPending.value && !recent.length" class="kb-meta" style="margin: 0">还没有改动。</p>
          </section>
          <section class="kb-card" aria-label="每周的修改">
            <p v-if="activity.error.value" class="kb-error" role="alert">{{ kbError(activity.error.value) }}</p>
            <p v-else-if="activity.isPending.value" class="kb-meta" role="status">正在加载…</p>
            <KbWeekChart v-else title="每周的修改" note="新建、修改、改名、退回的次数，按周一起算" :series="activitySeries" :weeks="activityWeeks" empty="近 8 周还没有人或智能体改过这个知识库。" />
          </section>
        </div>
      </div>
      <p v-else class="kb-empty">找不到这个知识库。<RouterLink to="/knowledge">回到知识库</RouterLink></p>
    </main>
    <p v-if="message" class="kb-toast" role="status">{{ message }}</p>
  </div>
</template>
