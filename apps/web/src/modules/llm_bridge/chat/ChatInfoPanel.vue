<script setup lang="ts">
import { computed } from 'vue'
import { X } from '@lucide/vue'
import { MODES, SANDBOX, originName, platformName, seconds } from '../api'
import { TURN_STATUS, ownerText, type ChatMessage, type ChatSession } from './chatApi'

// 右栏：这个会话的信息和回合列表。只显示。
const props = defineProps<{ session: ChatSession; messages: ChatMessage[]; managerName: string }>()
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
      <dt>用法</dt><dd>{{ MODES[session.mode] ?? session.mode }}</dd>
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
