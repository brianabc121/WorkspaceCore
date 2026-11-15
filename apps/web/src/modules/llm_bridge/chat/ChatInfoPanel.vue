<script setup lang="ts">
import { computed } from 'vue'
import { X } from '@lucide/vue'
import { CHAT_NO_TOOLS, MODES, NO_QUOTA, SANDBOX, originName, platformName, quotaKind, quotaUsage, seconds, type Quota } from '../api'
import { TURN_STATUS, ownerText, type ChatMessage, type ChatSession } from './chatApi'

// 右栏：这个会话的信息、这个平台的额度（T-0242）和回合列表。只显示。
// quota 来自管理器的状态；旧版本的管理器没有，这一块不显示。
const props = defineProps<{ session: ChatSession; messages: ChatMessage[]; managerName: string; quota?: Quota | null }>()
defineEmits<{ close: [] }>()

const turns = computed(() => props.messages.filter(m => m.role === 'assistant' && m.turn_id))
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
      <dt>模型</dt><dd>{{ session.model || '平台默认' }}</dd>
      <dt>用法</dt><dd>{{ MODES[session.mode] ?? session.mode }}<template v-if="session.mode === 'chat'">：{{ CHAT_NO_TOOLS }}</template></dd>
      <template v-if="session.environment">
        <dt>开发环境</dt><dd>{{ session.environment.name }}（{{ sandbox(session.environment.sandbox) }}{{ session.environment.network ? '，可联网' : '，不联网' }}）</dd>
        <template v-if="session.environment.repo_url"><dt>仓库</dt><dd>{{ session.environment.repo_url }}{{ session.environment.branch ? ` @ ${session.environment.branch}` : '' }}</dd></template>
      </template>
      <dt>MCP 管理器</dt><dd>{{ managerName }}</dd>
      <dt>谁建的</dt><dd>{{ ownerText(session) }}</dd>
      <dt>来源</dt><dd>{{ originName(session.origin) }}</dd>
      <dt>开始</dt><dd>{{ seconds(session.created_at) }}</dd>
      <dt>最后活动</dt><dd>{{ seconds(session.updated_at) }}</dd>
      <dt>状态</dt><dd>{{ session.status === 'archived' ? '已归档' : session.busy ? '正在回答' : '进行中' }}</dd>
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
      <p class="lc-note">最近 5 小时经这个管理器用掉 {{ quota.tokens_5h.toLocaleString() }} 个 token（管理器自己记的，不含别处用的）。</p>
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
