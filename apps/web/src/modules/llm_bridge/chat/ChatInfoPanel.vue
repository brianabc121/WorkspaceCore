<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { X } from '@lucide/vue'
import { CHAT_NO_TOOLS, MODES, NO_QUOTA, SANDBOX, originName, platformName, quotaKind, quotaUsage, recentUse, seconds, sourceTasks, type Quota } from '../api'
import { DIGEST_REASON, DIGEST_STATE, TURN_STATUS, contextText, ownerText, type ChatDigest, type ChatMessage, type ChatSession } from './chatApi'

// 右栏：这个会话的信息、这个平台的额度（T-0242）和回合列表。只显示。
// quota 来自管理器的状态；旧版本的管理器没有，这一块不显示。
// models：这个会话能换成哪些模型（Claude / CodeBuddy 的会话才给；不给就只显示现在的）
const props = defineProps<{ session: ChatSession; messages: ChatMessage[]; managerName: string; quota?: Quota | null; models?: { id: string; name: string }[]; changing?: boolean; digest?: ChatDigest | null; canWrite?: boolean }>()
defineEmits<{ close: []; model: [id: string]; digest: [action: 'on' | 'off' | 'write' | 'promote' | 'delete']; shape: [action: 'compact' | 'continue'] }>()
// 会话纪要（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 5.2、5.3）：写在知识库里、标为未确认；可以关掉、现在整理、转成正式笔记、删除
const digestNote = computed(() => (props.digest?.note_id && !['deleted', 'purged'].includes(props.digest.state) ? props.digest : null))

const turns = computed(() => props.messages.filter(m => m.role === 'assistant' && m.turn_id))
// 出处：智能体建这个会话时正在执行的任务（工作台记的，不保证就是引起这个会话的那一个）
const tasks = computed(() => sourceTasks(props.session.source))
const sandbox = (id: string) => (SANDBOX as Record<string, string>)[id] ?? id
</script>

<template>
  <aside class="lc-info" aria-label="会话信息">
    <div class="lc-info-head">
      <h3>会话信息</h3>
      <button type="button" class="lc-icon" aria-label="收起会话信息" @click="$emit('close')"><X aria-hidden="true" /></button>
    </div>
    <dl class="lb-meta">
      <dt>平台</dt><dd>{{ platformName(session.platform) }}</dd>
      <dt>模型</dt>
      <dd v-if="models?.length">
        <select :value="session.model ?? ''" :disabled="changing" aria-label="之后的回合用的模型" @change="$emit('model', ($event.target as HTMLSelectElement).value)">
          <option v-if="session.model && !models.some(m => m.id === session.model)" :value="session.model">{{ session.model }}</option>
          <option v-for="m in models" :key="m.id" :value="m.id">{{ m.name || m.id }}</option>
        </select>
        <span class="lc-sub">换了之后从下一回合起用新的模型。</span>
      </dd>
      <dd v-else>{{ session.model || '平台默认' }}</dd>
      <dt>用法</dt><dd>{{ MODES[session.mode] ?? session.mode }}<template v-if="session.mode === 'chat'">：{{ CHAT_NO_TOOLS }}</template></dd>
      <template v-if="session.environment">
        <dt>开发环境</dt><dd>{{ session.environment.name }}（{{ sandbox(session.environment.sandbox) }}{{ session.environment.network ? '，可联网' : '，不联网' }}）</dd>
        <template v-if="session.environment.repo_url"><dt>仓库</dt><dd>{{ session.environment.repo_url }}{{ session.environment.branch ? ` @ ${session.environment.branch}` : '' }}</dd></template>
      </template>
      <dt>MCP 管理器</dt><dd>{{ managerName }}</dd>
      <dt>谁建的</dt><dd>{{ ownerText(session) }}</dd>
      <template v-if="tasks.length">
        <dt>当时在做</dt>
        <dd><span v-for="t in tasks" :key="t.task_id" class="lc-source-task"><RouterLink class="asv-link" :to="`/tasks?task=${t.task_id}`">{{ t.title || t.task_id }}</RouterLink></span></dd>
      </template>
      <dt>来源</dt><dd>{{ originName(session.origin) }}</dd>
      <dt>开始</dt><dd>{{ seconds(session.created_at) }}</dd>
      <dt>最后活动</dt><dd>{{ seconds(session.updated_at) }}</dd>
      <dt>状态</dt><dd>{{ session.status === 'archived' ? '已归档' : session.busy ? '正在回答' : '进行中' }}</dd>
      <template v-if="session.pinned?.length">
        <dt>固定带着</dt>
        <dd><span v-for="p in session.pinned" :key="p.note_id" class="lc-pinned-item">《{{ p.title }}》</span><span class="lc-sub">新建会话时选的笔记，整个会话一直带着</span></dd>
      </template>
      <template v-if="session.context && session.origin !== 'external'">
        <dt>上下文</dt>
        <dd>
          {{ contextText(session.context) }}
          <span v-if="session.context.last_compacted_at" class="lc-sub">最近一次压缩：{{ seconds(session.context.last_compacted_at) }}（{{ session.context.last_trigger === 'manual' ? '手动' : '平台自动' }}）</span>
          <span v-if="canWrite && session.status === 'active'" class="lc-context-actions">
            <button type="button" class="asv-link" :disabled="changing" title="让平台把之前的内容压成摘要" @click="$emit('shape', 'compact')">压缩一下</button>
            <button type="button" class="asv-link" :disabled="changing" title="要一份交接摘要，清空上下文后把摘要作为第一条发出" @click="$emit('shape', 'continue')">带着摘要另开会话</button>
          </span>
        </dd>
      </template>
    </dl>
    <section v-if="quota" class="lc-quota" :aria-label="`额度：${platformName(session.platform)}`">
      <h4>额度（{{ platformName(session.platform) }}）</h4>
      <p v-if="quota.paused_until" role="note" class="lc-note is-red">
        额度用完，这个平台暂停到 {{ seconds(quota.paused_until) }}{{ quota.pause_estimated ? '（平台没有给出重置时间，这是估计的）' : '' }}；到时间后自动恢复。
      </p>
      <p v-if="!quota.windows.length && quota.reports_quota === false" class="lc-note">{{ NO_QUOTA }}</p>
      <p v-else-if="!quota.windows.length" class="lc-note">还没有看到这个平台的额度{{ quota.can_refresh ? '：到“管理”页的额度里点“向平台查一次”' : '：跑过一个回合后会显示' }}。</p>
      <dl v-for="w in quota.windows" v-else :key="w.kind" class="lb-meta">
        <dt>{{ quotaKind(w.kind) }}</dt><dd>{{ quotaUsage(w) }}</dd>
        <dt>重置时间</dt><dd>{{ w.resets_at ? seconds(w.resets_at) : '平台没有给出' }}</dd>
      </dl>
      <p class="lc-note">{{ recentUse(quota) }}</p>
    </section>
    <section v-if="session.origin !== 'external'" class="lc-digest" aria-label="会话纪要">
      <h4>会话纪要</h4>
      <p v-if="digestNote" class="lc-note">
        <RouterLink class="asv-link" :to="`/knowledge/notes/${digestNote.note_id}`">《{{ digestNote.note_title }}》</RouterLink>
        <span class="lc-sub">{{ digestNote.space_name }} · {{ DIGEST_STATE[digestNote.state] ?? digestNote.state }}{{ digestNote.state === 'active' ? '（未确认）' : '' }}{{ digestNote.written_at ? ` · ${seconds(new Date(digestNote.written_at).getTime() / 1000)} 整理` : '' }}{{ digestNote.reason ? `（${DIGEST_REASON[digestNote.reason] ?? digestNote.reason}）` : '' }}</span>
      </p>
      <p v-else-if="digest?.state === 'off'" class="lc-note">这个会话的纪要已关掉。</p>
      <p v-else class="lc-note">还没有整理过。会话有 3 个以上回合，并且平台压缩了上下文、会话归档、或者 24 小时没有新回合时，自动整理进知识库（不调模型、不花 token）。</p>
      <div v-if="canWrite" class="lc-digest-actions">
        <button v-if="digest?.state !== 'off' && digest?.state !== 'converted' && digest?.state !== 'edited'" type="button" class="asv-link" :disabled="changing" @click="$emit('digest', 'write')">现在整理一次</button>
        <button v-if="digestNote && digestNote.state === 'active'" type="button" class="asv-link" :disabled="changing" @click="$emit('digest', 'promote')">转成正式笔记</button>
        <button v-if="digestNote && digestNote.state !== 'converted'" type="button" class="asv-link" :disabled="changing" @click="$emit('digest', 'delete')">删除纪要</button>
        <button v-if="digest?.state === 'off'" type="button" class="asv-link" :disabled="changing" @click="$emit('digest', 'on')">重新打开纪要</button>
        <button v-else-if="!digest || digest.state === 'active'" type="button" class="asv-link" :disabled="changing" @click="$emit('digest', 'off')">关掉这个会话的纪要</button>
      </div>
    </section>
    <template v-if="session.origin === 'external'">
      <p class="lc-note">这是官方程序里已有的会话：这里读到的是它的文字记录，没有回合列表。</p>
    </template>
    <template v-else>
      <h4>回合（{{ turns.length }}）</h4>
      <p v-if="!turns.length" class="lc-note">还没有回合。</p>
      <ol v-else class="lc-turns">
        <li v-for="(t, i) in turns" :key="t.turn_id ?? i">
          <span>第 {{ i + 1 }} 回合</span>
          <span class="lc-turn-state" :data-status="t.status">{{ TURN_STATUS[t.status ?? ''] ?? t.status }}</span>
          <time v-if="t.at">{{ seconds(t.at) }}</time>
        </li>
      </ol>
    </template>
  </aside>
</template>
