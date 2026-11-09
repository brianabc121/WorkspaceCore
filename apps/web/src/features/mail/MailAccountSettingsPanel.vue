<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowRight, Check, Info, Mail, RefreshCw, TriangleAlert, Unplug, X } from '@lucide/vue'
import type { Schema } from '../../services/api'
import ActionFeedback from '../../components/ActionFeedback.vue'
import { formatTime } from '../time'
import { ACCOUNT_STATUS, ACCOUNT_TONE, isVersionConflict, mailError, useMailActions, type MailAccount } from './api'
import MailAssign from './MailAssign.vue'

// 邮件设置右侧：所选邮箱（T-0194，效果图 05）。邮箱授权按实际的 can_read / can_send 分开显示，需要重新连接的
// 不显示成正常；分配（PUT …/agents）与署名（PATCH）各自保存、各用这个邮箱当前最新的 version，冲突时提示核对、
// 不自动重发。重新连接与断开交给页面处理（断开后这一栏就没有了）。父组件按邮箱 ID 重建本组件：换邮箱不带草稿。
const props = defineProps<{ scope: string; account: MailAccount; agents: Schema['MailAgentRef'][]; agentsLoading: boolean; agentsError: string; busy: boolean; canManageInstances: boolean }>()
const emit = defineEmits<{ reconnect: [account: MailAccount]; disconnect: [account: MailAccount] }>()
const actions = useMailActions(props.scope)
const saving = ref(false), error = ref(''), notice = ref('')
const heading = ref<HTMLHeadingElement | null>(null)
defineExpose({ focus: () => heading.value?.focus() })

const scopes = computed(() => ([['读取邮件', props.account.can_read], ['发送邮件', props.account.can_send]] as const).map(([name, granted]) => (
  !granted ? { name, state: '未授权', tone: 'is-off', icon: X }
  : props.account.status !== 'active' ? { name, state: `已授权 · ${props.account.status === 'needs_reconnect' ? '需要重新连接' : '已断开'}`, tone: 'is-warn', icon: TriangleAlert }
  : { name, state: '已授权', tone: 'is-ok', icon: Check })))

async function run(effect: () => Promise<unknown>, success: string) {
  if (saving.value) return
  saving.value = true; error.value = ''; notice.value = ''
  try { await effect(); notice.value = success }
  catch (cause) {
    if (isVersionConflict(cause)) {
      // 别人刚改过这个邮箱：载入最新数据，草稿留着，由用户核对后自己再保存
      error.value = '这个邮箱的设置刚被其他人修改：已载入最新数据，请核对后再保存。'
      await actions.refresh()
    } else error.value = mailError(cause)
  } finally { saving.value = false }
}
function toggleSignature() {
  void run(() => actions.patch(props.account, { agent_signature: !props.account.agent_signature }), '邮箱设置已保存')
}
function assign(instanceIds: string[]) {
  const account = props.account, before = new Set(account.agents.map(a => a.id))
  const added = props.agents.filter(a => instanceIds.includes(a.id) && !before.has(a.id)).map(a => a.name)
  if (added.length && !window.confirm(`把 ${account.email} 分配给 ${added.join('、')}？它们能查这个邮箱的邮件（每个实例还有自己的查询开关）；发信还要在“OpenClaw 智能体”页单独给实例打开。`)) return
  void run(() => actions.assign(account, instanceIds), '邮箱分配已保存')
}
</script>

<template>
  <section class="ws-panel ms-panel" :aria-label="`邮箱：${account.email}`">
    <div class="ms-panel-head">
      <span class="ag-avatar" aria-hidden="true"><Mail /></span>
      <h2 ref="heading" tabindex="-1">{{ account.email }}</h2>
      <span class="asv-state" :class="ACCOUNT_TONE[account.status]">{{ ACCOUNT_STATUS[account.status] }}</span>
    </div>
    <p class="asv-sub">连接于 {{ formatTime(account.connected_at) }}<template v-if="account.display_name && account.display_name !== account.email"> · {{ account.display_name }}</template></p>
    <div v-if="account.status === 'needs_reconnect'" class="ws-callout tone-amber" role="note">
      <strong><TriangleAlert aria-hidden="true" />Google 授权已失效</strong>
      <p>原因：{{ account.last_error ?? '已撤销或过期' }}。重新连接同一个邮箱后才能继续读取和发送；分配与署名设置保留。</p>
    </div>

    <div class="ms-block">
      <h3>邮箱授权</h3>
      <ul class="ms-scopes" aria-label="邮箱授权">
        <li v-for="s in scopes" :key="s.name" :class="s.tone"><component :is="s.icon" aria-hidden="true" /><span>{{ s.name }}</span><span class="ms-scope-state">{{ s.state }}</span></li>
      </ul>
      <p class="form-help">这是这个邮箱给工作台的授权；每个智能体能不能查、能不能发，还要看实例自己的邮件权限。</p>
    </div>

    <MailAssign :account="account" :agents="agents" :loading="agentsLoading" :load-error="agentsError" :busy="saving" @save="assign" />
    <p class="ms-instances">
      <RouterLink v-if="canManageInstances" class="asv-link" to="/agents">管理智能体邮件权限<ArrowRight aria-hidden="true" /></RouterLink>
      <span v-else class="asv-sub">智能体的查询 / 发信开关由所有者在“OpenClaw 智能体”页设置。</span>
    </p>

    <div class="ms-block ms-signature">
      <div class="ms-signature-head">
        <h3 id="ms-signature-title">智能体发信署名</h3>
        <button type="button" role="switch" class="ms-switch" aria-label="智能体发信署名" :aria-checked="account.agent_signature" :disabled="saving" @click="toggleSignature">
          <span class="ms-switch-track" aria-hidden="true"><span class="ms-switch-thumb"></span></span>
        </button>
      </div>
      <p class="form-help">开启后，智能体通过这个邮箱发的信末尾加一行“由工作台智能体「名字」发送”。人自己从邮件页发的信不加。</p>
    </div>
    <ActionFeedback :error="error" :notice="notice" />

    <div class="ms-panel-actions">
      <button type="button" class="secondary-button" :disabled="busy" @click="emit('reconnect', account)"><RefreshCw aria-hidden="true" />重新连接</button>
      <button type="button" class="secondary-button im-danger" :disabled="busy || saving" @click="emit('disconnect', account)"><Unplug aria-hidden="true" />断开邮箱</button>
    </div>
    <div class="mg-hint ms-notes">
      <Info aria-hidden="true" />
      <ul>
        <li>新邮箱默认不分配给智能体。</li>
        <li>断开会撤销 Google 授权，发件记录保留。</li>
      </ul>
    </div>
  </section>
</template>
