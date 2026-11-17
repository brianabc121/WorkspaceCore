<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { CheckCircle2, ChevronDown, CircleAlert, CircleDashed, CodeXml, FilePlus2, Info, LoaderCircle, MessageSquare, Minimize2, X } from '@lucide/vue'
import { CHAT_NO_TOOLS, MODES, NO_QUOTA, SANDBOX, credits, originName, platformName, quotaKind, recentUse, resetText, seconds, sourceTasks, type Quota, type QuotaWindow } from '../api'
import { DIGEST_REASON, DIGEST_STATE, TURN_KIND, TURN_STATUS, contextText, ownerText, sessionState, turnTime, type ChatDigest, type ChatMessage, type ChatSession } from './chatApi'
import ChatPlatformIcon from './ChatPlatformIcon.vue'

// 会话详情（效果图第二张）：“概览”里是会话（平台与用法）、开发环境、上下文用量、这个平台的账号额度、会话纪要（开关）、基本信息，
// 其余（模型、来源、当时在做、仓库、固定带着的笔记）收在“更多信息”里；“执行记录”是全部回合。
// 只显示，操作都发事件给对话那一栏。quota 来自管理器的状态；旧版本的管理器没有，这一块不显示。
// 换模型在对话头部的模型标签上（Claude / CodeBuddy 的会话）；这里只显示现在的。
const props = defineProps<{ session: ChatSession; messages: ChatMessage[]; managerName: string; quota?: Quota | null; changing?: boolean; digest?: ChatDigest | null; canWrite?: boolean }>()
defineEmits<{ close: []; digest: [action: 'on' | 'off' | 'write' | 'promote' | 'delete']; shape: [action: 'compact' | 'continue'] }>()
// 会话纪要（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 5.2、5.3）：写在知识库里、标为未确认；可以关掉、现在整理、转成正式笔记、删除
const digestNote = computed(() => (props.digest?.note_id && !['deleted', 'purged'].includes(props.digest.state) ? props.digest : null))
const digestOn = computed(() => props.digest?.state !== 'off')
// 转成正式笔记或人改过以后，纪要不再自动更新：开关也不再有意义
const digestFixed = computed(() => props.digest?.state === 'converted' || props.digest?.state === 'edited')

const tab = ref<'overview' | 'turns'>('overview')
const turns = computed(() => props.messages.filter(m => m.role === 'assistant' && m.turn_id).map((m, i) => ({ ...m, no: i + 1 })))
const turnIcon = (status?: string | null) => (status === 'completed' ? CheckCircle2 : status === 'running' ? LoaderCircle : status === 'queued' ? CircleDashed : CircleAlert)
// 出处：智能体建这个会话时正在执行的任务（工作台记的，不保证就是引起这个会话的那一个）
const tasks = computed(() => sourceTasks(props.session.source))
const sandbox = (id: string) => (SANDBOX as Record<string, string>)[id] ?? id
// 状态和会话列表里的一样：回合在回答或排队时“进行中”，做完了“已完成”（T-____：原来没在回答也写“进行中”）
const state = computed(() => sessionState(props.session))
const external = computed(() => props.session.origin === 'external')
const mode = computed(() => `${MODES[props.session.mode] ?? props.session.mode}模式`)
const env = computed(() => props.session.environment)

// 上下文：已用比例、用了多少 / 窗口多大；平台没报窗口时只写用了多少
const context = computed(() => (!external.value ? props.session.context ?? null : null))
const contextBar = computed(() => Math.min(100, Math.max(0, context.value?.percent ?? 0)))
const contextAmount = computed(() => {
  const c = context.value
  if (!c) return ''
  if (c.used && c.window) return `${fine(c.used)} / ${fine(c.window)} tokens`
  return contextText(c)
})
const canShape = computed(() => !!context.value && !!props.canWrite && props.session.status === 'active')

// 账号额度：每个窗口一行（已用多少、进度条、什么时候重置）
const used = (w: QuotaWindow) => (w.used_percent == null ? null : Math.min(100, Math.max(0, Math.round(w.used_percent))))
const windowName = (kind: string) => quotaKind(kind).replace(/额度/, '').trim() || kind
function usedText(w: QuotaWindow) {
  if (w.reset_passed) return '已过重置时间'
  const n = used(w)
  return n == null ? (w.status === 'exceeded' ? '已用完' : '没有给出用量') : `已用 ${n}%`
}
// token 数：10 万以内留一位小数（29.7k），更多的取整
function fine(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
  if (n >= 100_000) return `${Math.round(n / 1000)}k`
  if (n >= 1000) return `${(n / 1000).toFixed(1).replace(/\.0$/, '')}k`
  return String(n)
}
const recent = computed(() => {
  const q = props.quota
  if (!q) return ''
  return `管理器近 5 小时用量 ${fine(q.tokens_5h)} tokens${typeof q.credits_5h === 'number' ? `、${credits(q.credits_5h)} 积分` : ''}`
})
</script>

<template>
  <aside class="lc-info" aria-label="会话信息">
    <div class="lc-info-head">
      <h3>会话详情</h3>
      <button type="button" class="lc-icon" aria-label="收起会话信息" @click="$emit('close')"><X aria-hidden="true" /></button>
    </div>
    <div class="lc-info-bar">
      <div class="lc-info-tabs" role="tablist" aria-label="会话详情">
        <button type="button" role="tab" :aria-selected="tab === 'overview'" @click="tab = 'overview'">概览</button>
        <button v-if="!external" type="button" role="tab" :aria-selected="tab === 'turns'" @click="tab = 'turns'">执行记录<span v-if="turns.length" class="lc-tab-count" aria-hidden="true">{{ turns.length }}</span></button>
      </div>
      <span class="lc-state" :data-state="state.key">{{ state.text }}</span>
    </div>

    <div v-if="tab === 'overview' || external" class="lc-info-body">
      <section class="lc-info-session" aria-label="会话">
        <ChatPlatformIcon :platform="session.platform" :mode="session.mode" />
        <div class="lc-info-title">
          <strong>{{ session.title || '（没有标题）' }}</strong>
          <span>{{ platformName(session.platform) }} · {{ mode }}</span>
        </div>
      </section>
      <section class="lc-info-env" :aria-label="env ? '开发环境' : '用法'">
        <span class="lc-info-env-icon" aria-hidden="true"><component :is="session.mode === 'chat' ? MessageSquare : CodeXml" /></span>
        <div v-if="env" class="lc-info-title">
          <strong>{{ env.name }}</strong>
          <span>{{ sandbox(env.sandbox) }} · {{ env.network ? '可联网' : '不联网' }}</span>
        </div>
        <div v-else class="lc-info-title">
          <strong>{{ mode }}</strong>
          <span>{{ session.mode === 'chat' ? CHAT_NO_TOOLS : '在管理器给的工作目录里执行' }}</span>
        </div>
      </section>

      <section v-if="context" class="lc-info-sec" aria-label="上下文">
        <div class="lc-sec-head"><h4>上下文</h4><b v-if="context.percent != null" class="lc-sec-figure">{{ context.percent }}%</b></div>
        <span v-if="context.percent != null" class="lc-bar" :class="{ 'is-warn': context.near_limit }" aria-hidden="true"><span :style="{ width: `${contextBar}%` }" /></span>
        <p class="lc-sec-note">{{ contextAmount }}<template v-if="context.compactions"> · 压缩过 {{ context.compactions }} 次</template></p>
        <p v-if="context.last_compacted_at" class="lc-sec-note">最近一次压缩：{{ seconds(context.last_compacted_at) }}（{{ context.last_trigger === 'manual' ? '手动' : '平台自动' }}）</p>
        <div v-if="canShape" class="lc-sec-actions">
          <button type="button" class="lc-act" :disabled="changing" title="让平台把之前的内容压成摘要" @click="$emit('shape', 'compact')"><Minimize2 aria-hidden="true" />压缩上下文</button>
          <button type="button" class="lc-act" :disabled="changing" title="要一份交接摘要，清空上下文后把摘要作为第一条发出" @click="$emit('shape', 'continue')"><FilePlus2 aria-hidden="true" />带摘要新建</button>
        </div>
      </section>

      <section v-if="quota" class="lc-info-sec" :aria-label="`账号额度：${platformName(session.platform)}`">
        <div class="lc-sec-head"><h4>账号额度</h4><span class="lc-sec-side">{{ platformName(session.platform) }}</span></div>
        <p v-if="quota.paused_until" role="note" class="lc-note is-red">
          额度用完，这个平台暂停到 {{ seconds(quota.paused_until) }}{{ quota.pause_estimated ? '（平台没有给出重置时间，这是估计的）' : '' }}；到时间后自动恢复。
        </p>
        <p v-if="!quota.windows.length && quota.reports_quota === false" class="lc-sec-note">{{ NO_QUOTA }}</p>
        <p v-else-if="!quota.windows.length" class="lc-sec-note">还没有看到这个平台的额度{{ quota.can_refresh ? '：到“管理”页的额度里点“向平台查一次”' : '：跑过一个回合后会显示' }}。</p>
        <div v-for="w in quota.windows" v-else :key="w.kind" class="lc-quota-row">
          <div class="lc-quota-top"><b>{{ windowName(w.kind) }}</b><span>{{ usedText(w) }}</span></div>
          <span class="lc-bar" :class="{ 'is-warn': (used(w) ?? 0) >= 80 }" aria-hidden="true"><span :style="{ width: `${used(w) ?? 0}%` }" /></span>
          <span class="lc-sec-note">{{ resetText(w.resets_at) }}</span>
        </div>
        <p class="lc-sec-note lc-sec-info" :title="recentUse(quota)"><Info aria-hidden="true" />{{ recent }}</p>
      </section>

      <section v-if="!external" class="lc-info-sec lc-digest" aria-label="会话纪要">
        <div class="lc-sec-head">
          <h4>会话纪要<span class="lc-sec-help" title="会话有 3 个以上回合，并且平台压缩了上下文、会话归档、或者 24 小时没有新回合时，自动整理进知识库（不调模型、不花 token）。" aria-hidden="true"><Info /></span></h4>
          <button
            v-if="!digestFixed" type="button" role="switch" class="lc-switch" :aria-checked="digestOn" aria-label="这个会话的会话纪要"
            :title="digestOn ? '关掉这个会话的纪要' : '重新打开纪要'" :disabled="!canWrite || changing" @click="$emit('digest', digestOn ? 'off' : 'on')"
          >
            <span aria-hidden="true" />
          </button>
        </div>
        <p v-if="digestNote" class="lc-digest-note">
          <RouterLink class="asv-link" :to="`/knowledge/notes/${digestNote.note_id}`">《{{ digestNote.note_title }}》</RouterLink>
          <span class="lc-sub">{{ digestNote.space_name }} · {{ DIGEST_STATE[digestNote.state] ?? digestNote.state }}{{ digestNote.state === 'active' ? '（未确认）' : '' }}{{ digestNote.written_at ? ` · ${seconds(new Date(digestNote.written_at).getTime() / 1000)} 整理` : '' }}{{ digestNote.reason ? `（${DIGEST_REASON[digestNote.reason] ?? digestNote.reason}）` : '' }}</span>
        </p>
        <p v-else-if="!digestOn" class="lc-digest-empty"><b>纪要已关掉</b><span>这个会话不再整理纪要；打开开关恢复。</span></p>
        <p v-else class="lc-digest-empty"><b>尚未生成纪要</b><span>满足条件后自动整理</span></p>
        <div v-if="canWrite" class="lc-digest-actions">
          <button v-if="digestOn && !digestFixed" type="button" class="lc-soft" :disabled="changing" @click="$emit('digest', 'write')">立即整理</button>
          <button v-if="digestNote && digestNote.state === 'active'" type="button" class="lc-act" :disabled="changing" @click="$emit('digest', 'promote')">转成正式笔记</button>
          <button v-if="digestNote && digestNote.state !== 'converted'" type="button" class="lc-act" :disabled="changing" @click="$emit('digest', 'delete')">删除纪要</button>
        </div>
      </section>

      <section class="lc-info-sec" aria-label="基本信息">
        <h4>基本信息</h4>
        <dl class="lc-kv is-plain">
          <dt>MCP 管理器</dt><dd>{{ managerName }}</dd>
          <dt>创建者</dt><dd>{{ ownerText(session) }}</dd>
          <dt>创建时间</dt><dd>{{ seconds(session.created_at) }}</dd>
          <dt>最近活动</dt><dd>{{ seconds(session.updated_at) }}</dd>
        </dl>
        <details class="lc-more-info">
          <summary>更多信息<ChevronDown aria-hidden="true" /></summary>
          <dl class="lc-kv is-plain">
            <dt>平台</dt><dd>{{ platformName(session.platform) }}</dd>
            <dt>模型</dt><dd>{{ session.model || '平台默认' }}</dd>
            <dt>用法</dt><dd>{{ mode }}<span v-if="session.mode === 'chat'" class="lc-sub">{{ CHAT_NO_TOOLS }}</span></dd>
            <template v-if="env?.repo_url"><dt>仓库</dt><dd>{{ env.repo_url }}{{ env.branch ? ` @ ${env.branch}` : '' }}</dd></template>
            <dt>来源</dt><dd>{{ originName(session.origin) }}</dd>
            <template v-if="tasks.length">
              <dt>当时在做</dt>
              <dd><span v-for="t in tasks" :key="t.task_id" class="lc-source-task"><RouterLink class="asv-link" :to="`/tasks?task=${t.task_id}`">{{ t.title || t.task_id }}</RouterLink></span></dd>
            </template>
            <template v-if="session.pinned?.length">
              <dt>固定带着</dt>
              <dd><span v-for="p in session.pinned" :key="p.note_id" class="lc-pinned-item">《{{ p.title }}》</span><span class="lc-sub">新建会话时选的笔记，整个会话一直带着</span></dd>
            </template>
          </dl>
        </details>
      </section>
      <p v-if="external" class="lc-note">这是官方程序里已有的会话：这里读到的是它的文字记录，没有回合列表。</p>
    </div>

    <div v-else class="lc-info-body">
      <section class="lc-info-sec" aria-label="执行记录">
        <h4>执行记录（{{ turns.length }}）</h4>
        <p v-if="!turns.length" class="lc-sec-note">还没有回合。</p>
        <ol v-else class="lc-turns">
          <li v-for="t in [...turns].reverse()" :key="t.turn_id ?? t.no" :data-status="t.status">
            <component :is="turnIcon(t.status)" class="lc-turn-icon" aria-hidden="true" />
            <span class="lc-turn-name">第 {{ t.no }} 回合<span v-if="t.kind && TURN_KIND[t.kind]" class="lc-sub">{{ TURN_KIND[t.kind]!.label }}</span></span>
            <span class="lc-turn-state" :data-status="t.status">{{ TURN_STATUS[t.status ?? ''] ?? t.status }}</span>
            <time v-if="t.at" :title="seconds(t.at)">{{ turnTime(t.at) }}</time>
          </li>
        </ol>
      </section>
    </div>
  </aside>
</template>
