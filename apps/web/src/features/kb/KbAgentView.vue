<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ChevronRight, CircleAlert, ShieldCheck, Waypoints } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { CHANGE_LABELS, GUARD_LABELS, NOTE_TYPE_LABELS, initial, kbError, useAgentKnowledge, useChanges, useKb, useNote, useNotes, useSpaces } from './api'
import { noteHtml, when } from './format'
import KbMobileHeader from './KbMobileHeader.vue'
import KbTabs from './KbTabs.vue'
import KbWeekChart from './KbWeekChart.vue'

// 智能体知识库主页（T-0099，效果图“电脑端 · 智能体知识库（自我提升）”）：
// 近 30 天的指标、自我提升循环、规则与写入开关、最近学到的、收到的纠正、它自己的笔记。
// T-0100：近 8 周的读取（自己的 / 项目和公共等共享的）与写入趋势；每晚复盘任务的说明。
const auth = useAuthStore(), route = useRoute(), router = useRouter(), scope = auth.workspaceId!
const kb = useKb(scope)
const spaces = useSpaces(scope)
const agentSpaces = computed(() => (spaces.data.value?.items ?? []).filter(s => s.kind === 'agent' && s.instance_id))
const instanceId = computed(() => typeof route.params.id === 'string' && route.params.id ? route.params.id : null)
watch([agentSpaces, instanceId], () => {
  if (!instanceId.value && agentSpaces.value[0]) void router.replace(`/knowledge/agents/${agentSpaces.value[0].instance_id}`)
}, { immediate: true })
const agent = useAgentKnowledge(scope, instanceId)
const info = computed(() => agent.data.value)
const space = computed(() => agentSpaces.value.find(s => s.instance_id === instanceId.value) ?? null)
const m = computed(() => info.value?.metrics)

const changes = useChanges(scope, computed(() => ({ instance_id: instanceId.value ?? undefined })))
const learned = computed(() => (changes.data.value?.pages[0]?.items ?? []).filter(c => c.kind === 'revision' && c.note_id).slice(0, 5))
const blocked = computed(() => (changes.data.value?.pages[0]?.items ?? []).filter(c => c.kind === 'guard').slice(0, 3))
const notes = useNotes(scope, computed(() => ({ space_id: info.value?.space_id })), computed(() => !!info.value?.space_id))
const noteItems = computed(() => (notes.data.value?.pages.flatMap(p => p.items) ?? []).filter(n => n.type !== 'correction'))
const correctionId = computed(() => (notes.data.value?.pages.flatMap(p => p.items) ?? []).find(n => n.type === 'correction')?.id ?? null)
const correction = useNote(scope, correctionId)
const correctionHtml = computed(() => correction.data.value ? noteHtml(correction.data.value) : '')

const busy = ref(false), failure = ref(''), message = ref('')
async function setWrites(enabled: boolean) {
  const s = space.value
  if (!s || busy.value) return
  busy.value = true; failure.value = ''
  try { await kb.updateSpace(s.id, { version: s.version, writes_enabled: enabled }); message.value = enabled ? '已允许它写入和删除' : '已关闭写入，它现在只能检索和阅读' }
  catch (error) { failure.value = kbError(error) } finally { busy.value = false }
}
async function setAccess(access: 'read' | 'none') {
  const s = space.value
  if (!s || busy.value) return
  busy.value = true; failure.value = ''
  try { await kb.updateSpace(s.id, { version: s.version, agent_access: access }); message.value = access === 'read' ? '其他智能体现在可以读它的知识库' : '其他智能体现在读不到它的知识库' }
  catch (error) { failure.value = kbError(error) } finally { busy.value = false }
}
async function resume() {
  const s = space.value
  if (!s || busy.value) return
  busy.value = true; failure.value = ''
  try { await kb.updateSpace(s.id, { version: s.version, resume_deletes: true }); message.value = '已恢复删除' }
  catch (error) { failure.value = kbError(error) } finally { busy.value = false }
}
const readWeeks = computed(() => (info.value?.weekly ?? []).map(w => ({ week_start: w.week_start, values: { shared: w.reads_shared, own: w.reads_own } })))
const writeWeeks = computed(() => (info.value?.weekly ?? []).map(w => ({ week_start: w.week_start, values: { writes: w.writes } })))
const readSeries = [{ key: 'shared', label: '项目、公共和别的智能体的' }, { key: 'own', label: '自己的' }]
const writeSeries = [{ key: 'writes', label: '写入与修改' }]
const loop = computed(() => m.value ? [
  { step: '1 先查知识库', value: m.value.searches, note: '接到任务时检索经验、规范和决定' },
  { step: '2 读取笔记', value: m.value.reads, note: `它写的被别的智能体读了 ${m.value.read_by_others} 次` },
  { step: '3 写入知识库', value: m.value.writes, note: `写进项目和公共 ${m.value.writes_shared} 次` },
  { step: '4 收到反馈', value: m.value.corrected, note: '被人修改或撤销，原因写回《收到的纠正》' },
] : [])
</script>

<template>
  <div class="kb-view is-mobile-main">
    <main class="kb-main">
      <KbMobileHeader :title="info?.name ?? space?.name ?? '智能体知识库'" back="/knowledge" back-label="返回知识库" />
      <KbTabs />
      <p v-if="spaces.isPending.value" class="kb-empty" role="status">正在加载…</p>
      <div v-else-if="!agentSpaces.length" class="kb-page"><p class="kb-empty">还没有 OpenClaw 实例。登记实例后，每个实例会自动有一个自己的知识库。<RouterLink v-if="auth.isOwner" to="/agents">登记实例</RouterLink></p></div>
      <div v-else class="kb-page">
        <nav class="kb-switcher" aria-label="切换智能体">
          <RouterLink v-for="s in agentSpaces" :key="s.id" :to="`/knowledge/agents/${s.instance_id}`" :aria-current="s.instance_id === instanceId ? 'page' : undefined">{{ s.name }}</RouterLink>
        </nav>
        <p v-if="agent.error.value" class="kb-error" role="alert">{{ kbError(agent.error.value) }}</p>
        <p v-else-if="agent.isPending.value" class="kb-meta" role="status">正在加载…</p>
        <template v-else-if="info && m">
          <header class="kb-hero">
            <div style="display: flex; align-items: center; gap: 14px">
              <span class="kb-avatar is-large" aria-hidden="true">{{ initial(info.name) }}</span>
              <div class="kb-stack" style="gap: 4px">
                <p class="kb-meta" style="margin: 0; display: flex; align-items: center; gap: 6px"><span class="kb-dot is-small k-agent" aria-hidden="true" />智能体知识库 · 跟随 OpenClaw 实例自动创建<template v-if="space?.archived"> · 实例已删除，只读</template></p>
                <h1>{{ info.name }}</h1>
                <p class="kb-meta" style="margin: 0">参与项目：{{ info.projects.length ? info.projects.map(p => p.title).join('、') : '还没有' }}</p>
              </div>
            </div>
            <div class="kb-actions"><RouterLink :to="{ path: '/knowledge/graph', query: { focus: info.space_id } }" class="kb-btn"><Waypoints aria-hidden="true" />在图谱中查看</RouterLink></div>
          </header>
          <p v-if="failure" class="kb-error" role="alert">{{ failure }}</p>
          <p v-if="message" class="kb-notice" role="status">{{ message }}</p>
          <div v-if="info.deletes_paused_at" class="kb-notice is-warning" role="alert"><CircleAlert aria-hidden="true" /><span class="kb-grow">它在 {{ when(info.deletes_paused_at) }}删除得太多，自己知识库的删除已自动暂停。到变更记录里核对后可以恢复。</span><RouterLink to="/knowledge/changes" class="kb-btn is-small">变更记录</RouterLink><button v-if="auth.canManage && space" type="button" class="kb-btn is-small" :disabled="busy" @click="resume">恢复删除</button></div>

          <section class="kb-stats" aria-label="近 30 天">
            <article class="kb-stat"><h2>自己的笔记</h2><p class="kb-stat-value">{{ m.notes }}</p><p>它自己知识库里现在的篇数</p></article>
            <article class="kb-stat"><h2>写入与修改</h2><p class="kb-stat-value">{{ m.writes }}</p><p>其中写进项目和公共 {{ m.writes_shared }} 次 · 删除 {{ m.deletes }} 篇</p></article>
            <article class="kb-stat"><h2>检索与读取</h2><p class="kb-stat-value">{{ m.searches }} / {{ m.reads }}</p><p>检索次数 / 读取笔记次数</p></article>
            <article class="kb-stat"><h2>被纠正 · 被拦下</h2><p class="kb-stat-value">{{ m.corrected }} · {{ m.blocked }}</p><p>{{ info.corrections_unseen ? `还有 ${info.corrections_unseen} 条纠正它没看到` : '纠正都已看到' }}</p></article>
            <article class="kb-stat"><h2>过了复核日期</h2><p class="kb-stat-value">{{ m.overdue_reviews }}</p><p>它写的、需要重新确认的笔记</p></article>
          </section>

          <section class="kb-card" aria-label="自我提升循环">
            <div class="kb-card-head"><h2>自我提升循环</h2><span class="kb-meta">近 30 天每一步发生的次数</span></div>
            <ol class="kb-loop">
              <template v-for="(s, i) in loop" :key="s.step">
                <li class="kb-step"><span>{{ s.step }}</span><strong>{{ s.value }}</strong><span>{{ s.note }}</span></li>
                <li v-if="i < loop.length - 1" class="kb-arrow" aria-hidden="true"><ChevronRight style="width: 18px; height: 18px" /></li>
              </template>
            </ol>
          </section>

          <section class="kb-card" aria-label="每周趋势">
            <div class="kb-card-head"><div class="kb-stack"><h2>每周趋势</h2><span class="kb-meta">读别人沉淀的知识越多、写进共享知识库越多，说明知识在智能体之间流动起来了。</span></div></div>
            <div class="kb-chart-pair">
              <KbWeekChart title="读取笔记" note="读到的是哪个知识库里的笔记" :series="readSeries" :weeks="readWeeks" empty="近 8 周它还没有读过知识库。" />
              <KbWeekChart title="写入与修改" note="新建、修改、改名（所有知识库）" :series="writeSeries" :weeks="writeWeeks" empty="近 8 周它还没有写过知识库。" />
            </div>
          </section>

          <div class="kb-grid-2">
            <section class="kb-card" aria-label="最近学到的">
              <div class="kb-card-head"><h2>最近学到的</h2><RouterLink :to="{ path: '/knowledge/changes' }" class="kb-meta">全部写入记录</RouterLink></div>
              <ul class="kb-timeline">
                <li v-for="c in learned" :key="c.id" style="grid-template-columns: minmax(0, 1fr) auto">
                  <span class="kb-stack"><RouterLink v-if="!c.note_deleted" :to="`/knowledge/notes/${c.note_id}`">{{ c.note_title }}</RouterLink><span v-else>{{ c.note_title }}（在回收站）</span><span class="kb-meta">{{ c.reason || '（没写原因）' }} · {{ c.space_name }}</span></span>
                  <span class="kb-meta">{{ when(c.created_at) }} · {{ c.change ? CHANGE_LABELS[c.change] : '' }}{{ c.version ? ` · 第 ${c.version} 版` : '' }}</span>
                </li>
              </ul>
              <p v-if="!changes.isPending.value && !learned.length" class="kb-meta" style="margin: 0">它还没有写过知识库。</p>
            </section>
            <section class="kb-card" aria-label="规则">
              <h2 style="font-size: 15px">规则</h2>
              <dl class="kb-dl" style="grid-template-columns: 96px minmax(0, 1fr)">
                <dt>自己的知识库</dt><dd>直接写入、修改；删除进回收站，30 天内可恢复。每次写入都留版本，可以随时撤销。</dd>
                <dt>项目和公共</dt><dd>按项目的参与权限与公共库的设置，同样直接读写，不用审核。</dd>
                <dt>能读哪些</dt><dd>自己的 + 参与的 {{ info.projects.length }} 个项目 + 公共（公共库允许时）+ 允许别人读的其他智能体的知识库</dd>
                <dt>写入上限</dt><dd>每小时写 20 次、删 5 篇，每篇 20 KB；写进来的内容有密钥会被拦下</dd>
              </dl>
              <label class="kb-check" style="align-items: flex-start"><input type="checkbox" :checked="info.writes_enabled" :disabled="!auth.canManage || busy || !space" @change="setWrites(($event.target as HTMLInputElement).checked)"><span class="kb-stack"><strong>允许写入和删除</strong><span class="kb-meta">关掉后它只能检索和阅读（所有知识库）</span></span></label>
              <label class="kb-check" style="align-items: flex-start"><input type="checkbox" :checked="space?.agent_access === 'read'" :disabled="!auth.canManage || busy || !space" @change="setAccess(($event.target as HTMLInputElement).checked ? 'read' : 'none')"><span class="kb-stack"><strong>其他智能体可以读它的知识库</strong><span class="kb-meta">只读；别的智能体不能改它的笔记</span></span></label>
              <div class="kb-notice"><ShieldCheck aria-hidden="true" style="stroke: var(--kb-good)" /><span class="kb-grow">每晚复盘：接入时在它的 OpenClaw 里建好一个定时任务（默认每晚 22:00，北京时间），回顾当天的工作，把值得长期记住的写进知识库，先看《收到的纠正》。项目简报更新后，它下次读写知识库时会被提醒先读。</span></div>
            </section>
          </div>

          <div class="kb-grid-2">
            <section class="kb-card" aria-label="收到的纠正">
              <div class="kb-card-head"><div class="kb-stack"><h2>收到的纠正</h2><span class="kb-meta">人修改或撤销它写的内容时，原因会写到这里；它下次读写知识库时会先看到。</span></div></div>
              <!-- eslint-disable-next-line vue/no-v-html -- 已经过 DOMPurify 消毒（markdown.ts） -->
              <div v-if="correction.data.value" class="kb-md" style="font-size: 13.5px; max-height: 320px; overflow-y: auto" v-html="correctionHtml" />
              <p v-else class="kb-meta" style="margin: 0">还没有纠正。</p>
              <ul v-if="blocked.length" class="kb-timeline">
                <li v-for="c in blocked" :key="c.id" style="grid-template-columns: minmax(0, 1fr) auto"><span>{{ c.guard ? GUARD_LABELS[c.guard] : '' }}：{{ c.detail }}</span><span class="kb-meta">{{ when(c.created_at) }}</span></li>
              </ul>
            </section>
            <section class="kb-card" aria-label="它的笔记">
              <div class="kb-card-head"><h2>它的笔记</h2><RouterLink :to="{ path: '/knowledge/notes', query: { space: info.space_id } }" class="kb-meta">全部 {{ m.notes }} 篇</RouterLink></div>
              <ul class="kb-list">
                <li v-for="n in noteItems.slice(0, 10)" :key="n.id"><RouterLink :to="`/knowledge/notes/${n.id}`" class="kb-list-item"><span class="kb-grow">{{ n.title }}</span><span class="kb-meta">{{ NOTE_TYPE_LABELS[n.type] }} · {{ when(n.updated_at) }}</span></RouterLink></li>
              </ul>
              <p v-if="!notes.isPending.value && !noteItems.length" class="kb-meta" style="margin: 0">还没有笔记。</p>
            </section>
          </div>
        </template>
      </div>
    </main>
  </div>
</template>
