<script setup lang="ts">
import { computed, ref, toRef } from 'vue'
import { RefreshCw } from '@lucide/vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import { MODES, bridgeError, originName, platformName, seconds, statusName, type Bridge, type LlmBridgeApi } from './api'

// 会话桥上的会话（只读）：表格 + 点开看回合。内容来自大模型和智能体，一律按纯文字显示。
const props = defineProps<{ api: LlmBridgeApi; bridge: Bridge }>()
const LIMIT = 50
const id = toRef(() => props.bridge.id)
const offset = ref(0)
const sessions = props.api.useSessions(id, offset, LIMIT)
const list = computed(() => sessions.data.value?.sessions ?? [])
const openId = ref<string | null>(null)
const open = computed(() => list.value.find(s => s.session_id === openId.value) ?? null)
const turns = props.api.useTurns(id, openId)

const error = ref(''), notice = ref(''), syncing = ref(false)
async function sync() {
  if (syncing.value) return
  syncing.value = true; error.value = notice.value = ''
  try {
    const out = await props.api.syncSessions(props.bridge.id, LIMIT)
    offset.value = 0
    if (out.sync_failed) error.value = `同步没有完全成功：${out.sync_failed}`
    else notice.value = '已同步官方程序里的会话。'
  } catch (e) { error.value = bridgeError(e) } finally { syncing.value = false }
}
function page(step: number) { offset.value = Math.max(0, offset.value + step * LIMIT); openId.value = null }
const failureText = (failure: Record<string, unknown>) => String(failure.message ?? failure.code ?? '没有给出原因')
</script>

<template>
  <ManagementSection title="会话" :label="`会话：${bridge.name}`" description="这里只能看到这座会话桥容器里的会话（包括官方程序保存在容器里的）。看不到 chatgpt.com、claude.ai 网页上的对话，也看不到其他机器上的会话。">
    <template #actions>
      <button type="button" class="secondary-button" :disabled="syncing" @click="sync"><RefreshCw aria-hidden="true" />{{ syncing ? '正在同步…' : '同步官方程序里的会话' }}</button>
    </template>
    <ActionFeedback :error="error" :notice="notice" />
    <p v-if="sessions.isPending.value" role="status" class="data-state">正在读取会话…</p>
    <div v-else-if="sessions.error.value" class="data-state">
      <p role="alert">{{ bridgeError(sessions.error.value) }}</p>
      <button type="button" class="secondary-button" @click="sessions.refetch()">重试</button>
    </div>
    <p v-else-if="!list.length" class="ws-state-line">{{ offset ? '后面没有更多会话了。' : '还没有会话。智能体新建会话后会出现在这里；容器里官方程序已有的会话，点“同步官方程序里的会话”登记进来。' }}</p>
    <table v-else class="asv-table lb-table" aria-label="会话">
      <thead><tr><th scope="col">标题</th><th scope="col">通道</th><th scope="col">模型</th><th scope="col">归属</th><th scope="col">来源</th><th scope="col">状态</th><th scope="col">更新时间</th></tr></thead>
      <tbody>
        <tr v-for="s in list" :key="s.session_id" :aria-selected="s.session_id === openId">
          <th scope="row"><button type="button" class="asv-link lb-wrap" :aria-label="`查看会话：${s.title || s.session_id}`" @click="openId = openId === s.session_id ? null : s.session_id">{{ s.title || '（没有标题）' }}</button><span class="asv-sub">{{ s.turns }} 个回合</span></th>
          <td data-label="通道">{{ platformName(s.platform) }} · {{ MODES[s.mode] ?? s.mode }}</td>
          <td data-label="模型">{{ s.model || '—' }}</td>
          <td data-label="归属">{{ s.owner || '—' }}</td>
          <td data-label="来源">{{ originName(s.origin) }}</td>
          <td data-label="状态">{{ statusName(s.status) }}</td>
          <td data-label="更新时间" class="asv-nowrap">{{ seconds(s.updated_at) }}</td>
        </tr>
      </tbody>
    </table>
    <div v-if="offset || list.length === LIMIT" class="lb-pager">
      <button type="button" class="secondary-button" :disabled="!offset" @click="page(-1)">上一页</button>
      <button type="button" class="secondary-button" :disabled="list.length < LIMIT" @click="page(1)">下一页</button>
    </div>

    <section v-if="open" class="lb-form" :aria-label="`回合：${open.title || open.session_id}`">
      <h3>「{{ open.title || '（没有标题）' }}」的回合（最新的在前）</h3>
      <p v-if="turns.isPending.value" role="status" class="data-state">正在读取回合…</p>
      <div v-else-if="turns.error.value" class="data-state">
        <p role="alert">{{ bridgeError(turns.error.value) }}</p>
        <button type="button" class="secondary-button" @click="turns.refetch()">重试</button>
      </div>
      <p v-else-if="!turns.data.value?.length" class="ws-state-line">这个会话还没有回合。</p>
      <ol v-else class="lb-turns">
        <li v-for="t in turns.data.value" :key="t.turn_id">
          <div class="lb-inline"><span class="asv-state" :class="t.status === 'completed' ? 'tone-green' : t.status === 'failed' || t.status === 'timeout' ? 'tone-red' : 'tone-blue'">{{ statusName(t.status) }}</span><span class="asv-sub">{{ seconds(t.queued_at) }}</span></div>
          <span class="lb-turn-label">指令</span>
          <pre>{{ t.text }}</pre>
          <template v-if="t.result"><span class="lb-turn-label">结果</span><pre>{{ t.result }}</pre></template>
          <template v-if="t.failure"><span class="lb-turn-label">失败原因</span><pre role="alert">{{ failureText(t.failure) }}</pre></template>
        </li>
      </ol>
    </section>
  </ManagementSection>
</template>
