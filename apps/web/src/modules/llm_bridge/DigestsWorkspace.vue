<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import ManagementPage from '../../components/management/ManagementPage.vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import { bridgeError } from './api'
import { DIGEST_REASON, DIGEST_STATE, chatError, useLlmChat, type DigestItem } from './chat/chatApi'

// 会话纪要（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 5.2、5.3）：每个大模型会话一篇，工作台按会话记录整理、
// 写在知识库的“会话纪要”文件夹里，标为未确认。这里列出每一篇，带指回会话的链接；
// 可以转成正式笔记（人确认）、删除（进回收站）、关掉某个会话的纪要。
const props = defineProps<{ scope: string }>()
const chat = useLlmChat(props.scope)
const state = ref<string | null>(null)
const digests = chat.useDigests(state, ref(true))
const items = computed(() => digests.data.value?.digests ?? [])
const error = ref(''), notice = ref(''), busy = ref('')
async function act(item: DigestItem, action: 'promote' | 'delete' | 'off' | 'on' | 'write') {
  const ask = {
    promote: `把《${item.note_title}》转成正式笔记？\n\n转成后它就是你确认过的普通笔记，放到知识库的根目录，之后不再自动更新。`,
    delete: `删除《${item.note_title}》？\n\n纪要放进知识库的回收站（30 天内可以恢复），之后不再为这个会话整理纪要。`,
    off: '关掉这个会话的纪要？\n\n之后不再为它整理；已有的那篇不动。',
    on: '', write: '',
  }[action]
  if (ask && !window.confirm(ask)) return
  busy.value = item.id; error.value = notice.value = ''
  try {
    await chat.digest(item.bridge_id, item.session_id, action)
    notice.value = { promote: '已转成正式笔记。', delete: '纪要已放进回收站。', off: '已关掉这个会话的纪要。', on: '已重新打开。', write: '已整理。' }[action]
  } catch (e) { error.value = chatError(e) } finally { busy.value = '' }
}
const when = (at: string | null | undefined) => (at ? new Date(at).toLocaleString('zh-CN', { hour12: false }) : '—')
</script>

<template>
  <ManagementPage title="会话纪要" description="工作台把每个大模型会话的重点按记录整理成纪要（不调模型、不花 token），存进知识库的“会话纪要”文件夹，标为未确认：检索时排在最后，默认不会被带进提问。值得长期留下的转成正式笔记；不需要的删掉。">
    <ManagementSection title="全部纪要" description="智能体的会话放进它自己的知识库，人的放进公共知识库，关联了项目的放进项目知识库。在“管理器与账号 → 设置”里可以关掉整个功能。">
      <ActionFeedback :error="error" :notice="notice" />
      <form class="lb-filters" aria-label="筛选纪要" @submit.prevent>
        <div class="form-field">
          <label for="dg-state">状态</label>
          <select id="dg-state" v-model="state">
            <option :value="null">全部</option>
            <option v-for="(label, key) in DIGEST_STATE" :key="key" :value="key">{{ label }}</option>
          </select>
        </div>
      </form>
      <p v-if="digests.isPending.value" role="status" class="data-state">正在读取会话纪要…</p>
      <div v-else-if="digests.error.value" class="data-state">
        <p role="alert">{{ bridgeError(digests.error.value) }}</p>
        <button type="button" class="secondary-button" @click="digests.refetch()">重试</button>
      </div>
      <p v-else-if="!items.length" class="ws-state-line">{{ state ? '没有这种状态的纪要。' : '还没有会话纪要。会话有 3 个以上回合，并且平台压缩了上下文、会话归档、或者 24 小时没有新回合时会自动整理。' }}</p>
      <table v-else class="asv-table lb-table" aria-label="会话纪要">
        <thead><tr><th scope="col">纪要</th><th scope="col">会话</th><th scope="col">知识库</th><th scope="col">状态</th><th scope="col">整理时间</th><th scope="col">操作</th></tr></thead>
        <tbody>
          <tr v-for="d in items" :key="d.id">
            <th scope="row">
              <RouterLink v-if="d.note_id && !['deleted', 'purged'].includes(d.state)" class="asv-link lb-wrap" :to="`/knowledge/notes/${d.note_id}`">{{ d.note_title || '（没有标题）' }}</RouterLink>
              <span v-else class="asv-sub">{{ d.note_title || '（还没有写）' }}</span>
            </th>
            <td>
              <RouterLink class="asv-link lb-wrap" :to="{ path: '/llm_bridge', query: { m: d.bridge_id, s: d.session_id } }">{{ d.session_title || d.session_id }}</RouterLink>
              <span class="asv-sub">{{ d.owner || '—' }} · {{ d.bridge_name }}</span>
            </td>
            <td>{{ d.space_name || '—' }}</td>
            <td>{{ DIGEST_STATE[d.state] ?? d.state }}<span v-if="d.reason" class="asv-sub">{{ DIGEST_REASON[d.reason] ?? d.reason }}</span></td>
            <td>{{ when(d.written_at) }}<span class="asv-sub">{{ d.turns_written }} 个回合</span></td>
            <td class="lb-actions">
              <button v-if="d.state === 'active' && d.note_id" type="button" class="asv-link" :disabled="!!busy" @click="act(d, 'promote')">转成正式笔记</button>
              <button v-if="d.note_id && ['active', 'off', 'edited'].includes(d.state)" type="button" class="asv-link" :disabled="!!busy" @click="act(d, 'delete')">删除</button>
              <button v-if="d.state === 'active'" type="button" class="asv-link" :disabled="!!busy" @click="act(d, 'off')">关掉</button>
              <button v-if="d.state === 'off'" type="button" class="asv-link" :disabled="!!busy" @click="act(d, 'on')">重新打开</button>
            </td>
          </tr>
        </tbody>
      </table>
    </ManagementSection>
  </ManagementPage>
</template>
