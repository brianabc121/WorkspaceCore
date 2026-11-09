<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { ArrowRight, Bot, Calendar, CalendarClock, Clock, Lock, ShieldCheck, User, UserCheck, X } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import ApprovalActions from './ApprovalActions.vue'
import { RISK_LABELS, STATUS_LABELS, aiError, ownKeys, useExecution, useMemberNames } from './queries'
import {
  EXECUTED, RISK_TONE, SOURCE_ICONS, STATUS_TONE, approvalSteps, dayTime, formatRemaining, readableFields, relatedLink, remainingSeconds,
  safeJson, shortId, sourceOf, toolEffect, toolIcon, triggeredBy,
} from './approvalFormat'
import './approval-center.css'

// 审批详情（T-0191，效果图 01 右侧；规范 §4）：按 ID 读取（不在当前列表里的记录也能打开，例如通知与 IM 卡片的链接）。
// 只展示服务端给的：平台生成的说明、脱敏后的固定参数、来源（推断的明确标出）、真实状态与执行结果；
// 没有 AI 申请理由、修改前后的值或聊天原文，不编造。外部文字一律按文本渲染。
// 倒计时按服务端时钟（serverNow = 本机时间 + summary.as_of 的偏差）；归零禁用批准并重新读取，状态以服务端为准。
const props = defineProps<{ scope: string; executionId: string; serverNow: number | null; expiringWindow?: number; aside?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const auth = useAuthStore(), cache = useQueryClient(), user = auth.user?.id
const record = useExecution(props.scope, () => props.executionId)
const names = useMemberNames(props.scope)
const e = computed(() => record.data.value ?? null)
const pending = computed(() => e.value?.status === 'pending_approval')
const remaining = computed(() => (pending.value && props.serverNow !== null && e.value ? remainingSeconds(e.value.expires_at, props.serverNow) : null))
const expired = computed(() => remaining.value === 0)
const source = computed(() => (e.value ? sourceOf(e.value.source) : null))
const fields = computed(() => (e.value ? readableFields(e.value.tool_name, e.value.arguments) : null))
const json = computed(() => (e.value ? safeJson(e.value.arguments) : ''))
const link = computed(() => (e.value ? relatedLink(e.value) : null))
const steps = computed(() => (e.value ? approvalSteps(e.value) : []))
const needsApproval = computed(() => !!e.value && (e.value.risk_level === 'write' || e.value.risk_level === 'critical'))
const executed = computed(() => !!e.value && EXECUTED.includes(e.value.status))
const canDecide = computed(() => pending.value && auth.canManage)
const notice = ref('')
const uid = useId()

// 归零：重新读取这条记录、列表与汇总（只读）；同一条只读一次，服务端仍说待审批时也保持禁用
let refreshedAtZero = false
watch(expired, (now) => {
  if (!now || refreshedAtZero) return
  refreshedAtZero = true
  void cache.invalidateQueries({ predicate: ownKeys(user, props.scope) })
})

function person(id: string | null) {
  if (!id) return '未记录'
  if (id === auth.user?.id) return `你（${auth.user.display_name || auth.user.email}）`
  return names.value.get(id) ?? `成员 ${shortId(id)}`
}
const conversationHint = computed(() => (source.value?.kind === 'group' ? '在群聊里 @ 它' : source.value?.kind === 'direct' ? '私聊它' : ''))
const RESULT: Record<string, { title: string; tone: string }> = {
  executing: { title: '执行中', tone: 'blue' }, succeeded: { title: '执行成功', tone: 'green' }, failed: { title: '执行失败', tone: 'red' },
  unknown: { title: '结果未知', tone: 'amber' }, rejected: { title: '已拒绝', tone: 'muted' }, expired: { title: '已过期', tone: 'muted' }, denied: { title: '权限不够', tone: 'muted' },
}
</script>

<template>
  <section class="ac-detail" aria-label="审批详情">
    <div v-if="aside" class="ac-detail-head">
      <p class="ac-eyebrow">审批详情</p>
      <button type="button" class="icon-button ac-close" aria-label="关闭审批详情" @click="emit('close')"><X aria-hidden="true" /></button>
    </div>

    <template v-if="e">
      <h2 class="ac-title preserve-text">{{ e.summary }}</h2>
      <div class="ac-pills">
        <span class="ac-risk" :class="`tone-${RISK_TONE[e.risk_level]}`">{{ RISK_LABELS[e.risk_level] }}</span>
        <span class="asv-state" :class="`tone-${STATUS_TONE[e.status]}`">{{ STATUS_LABELS[e.status] }}</span>
        <span v-if="!executed" class="asv-state tone-muted ac-not-run">未执行</span>
      </div>

      <p v-if="pending" class="ac-banner" :class="{ 'is-expired': expired, 'is-soon': remaining !== null && remaining <= (expiringWindow ?? 180) }">
        <Clock aria-hidden="true" />
        <template v-if="remaining === null">到期时间 {{ dayTime(e.expires_at) }} · 到期后自动失效。</template>
        <template v-else-if="expired">已到期，不能再批准；正在向服务端确认最新状态。</template>
        <template v-else>剩余 {{ formatRemaining(remaining) }} · 到期后自动失效。</template>
      </p>

      <section class="ac-block" :aria-labelledby="`${uid}-info`">
        <h3 :id="`${uid}-info`">申请信息</h3>
        <dl class="ac-facts">
          <dt><Bot aria-hidden="true" />发起智能体</dt>
          <dd>
            <RouterLink v-if="e.instance_id && auth.canManage" class="asv-link ac-inline" :to="{ path: '/agents', query: { instance: e.instance_id } }"><span class="preserve-text">{{ e.instance_name }}</span><ArrowRight aria-hidden="true" /></RouterLink>
            <span v-else class="preserve-text">{{ e.instance_name }}</span>
            <span v-if="!e.instance_id" class="ac-tag">实例已删除</span>
            <span v-else class="asv-sub" :title="e.instance_id">ID {{ shortId(e.instance_id) }}</span>
          </dd>
          <dt><UserCheck aria-hidden="true" />执行身份</dt>
          <dd><span title="实例登记的执行人；批准后以这个身份的权限执行">{{ person(e.acting_user_id) }}</span></dd>
          <dt><User aria-hidden="true" />触发者</dt>
          <dd><span class="preserve-text">{{ triggeredBy(e.source) ?? '未记录' }}</span><span v-if="conversationHint && triggeredBy(e.source)" class="asv-sub">{{ conversationHint }}</span></dd>
          <dt><component :is="SOURCE_ICONS[source!.kind]" aria-hidden="true" />来源</dt>
          <dd>
            <!-- IM 页只有 owner / admin 能进；其他人只看到来源文字 -->
            <RouterLink v-if="source!.conversation && auth.canManage" class="asv-link ac-inline" :to="{ path: '/im', query: { c: source!.conversation } }">{{ source!.label }}</RouterLink>
            <span v-else>{{ source!.label }}</span>
            <template v-if="source!.inferred && source!.conversation">
              <span class="ac-tag is-inferred">来源推断</span>
              <span class="asv-sub">根据实例最近收到的消息推断，不是确定的聊天证据。</span>
            </template>
          </dd>
          <dt><Clock aria-hidden="true" />提交时间</dt>
          <dd><time :datetime="e.created_at">{{ dayTime(e.created_at) }}</time></dd>
          <template v-if="e.expires_at">
            <dt><CalendarClock aria-hidden="true" />到期时间</dt>
            <dd><time :datetime="e.expires_at">{{ dayTime(e.expires_at) }}</time></dd>
          </template>
          <template v-if="e.decided_by">
            <dt><ShieldCheck aria-hidden="true" />审批人</dt>
            <dd><span>{{ person(e.decided_by) }}</span><span v-if="e.decided_at" class="asv-sub">处理于 {{ dayTime(e.decided_at) }}</span></dd>
          </template>
          <template v-if="e.executed_at">
            <dt><Calendar aria-hidden="true" />执行时间</dt>
            <dd><time :datetime="e.executed_at">{{ dayTime(e.executed_at) }}</time></dd>
          </template>
        </dl>
      </section>

      <section v-if="needsApproval" class="ac-block" :aria-labelledby="`${uid}-impact`">
        <h3 :id="`${uid}-impact`">操作影响</h3>
        <div class="ac-impact">
          <component :is="toolIcon(e.tool_name)" aria-hidden="true" class="ac-impact-icon" />
          <div class="ac-impact-body">
            <strong>{{ toolEffect(e.tool_name) ?? `调用工具 ${e.tool_name}` }}</strong>
            <span class="asv-sub">工具 <code>{{ e.tool_name }}</code></span>
          </div>
          <RouterLink v-if="link" class="asv-link ac-impact-link" :to="link.to" :aria-label="`${link.label}：${e.summary}`">{{ link.label }}<ArrowRight aria-hidden="true" /></RouterLink>
          <p class="ac-impact-text">{{ pending ? '批准后按本次提交的参数执行一次，用实例登记的执行身份，不借审批人的权限。' : executed ? '已按提交的参数、用实例登记的执行身份执行（只执行这一次）。' : '没有执行。' }}</p>
        </div>
      </section>

      <section class="ac-block" :aria-labelledby="`${uid}-args`">
        <div class="ac-block-head"><h3 :id="`${uid}-args`">提交参数</h3><span class="ac-tag"><Lock aria-hidden="true" />固定参数</span></div>
        <ul v-if="fields && fields.length" class="ac-args" aria-label="提交参数">
          <li v-for="f in fields" :key="f.key"><span class="ac-arg-key"><span>{{ f.label }}</span><code>{{ f.key }}</code></span><span class="ac-arg-value preserve-text">{{ f.value }}</span></li>
        </ul>
        <p v-else-if="fields" class="form-help">这次调用没有参数。</p>
        <p v-else class="form-help">这个工具没有专门的展示方式，下面按原样显示服务端保存的参数（已脱敏）。</p>
        <details v-if="fields" class="ac-raw"><summary>查看原始参数</summary><pre class="ai-arguments">{{ json }}</pre></details>
        <pre v-else class="ai-arguments">{{ json }}</pre>
        <p class="ac-digest">参数摘要（SHA-256）<code :title="e.arguments_hash">{{ shortId(e.arguments_hash, 12, 8) }}</code></p>
        <p class="asv-sub">摘要由服务端计算，用来识别同一请求，页面没有重新校验；参数只读，不能修改。</p>
      </section>

      <section class="ac-block" :aria-labelledby="`${uid}-flow`">
        <h3 :id="`${uid}-flow`">审批流程</h3>
        <ol class="ac-steps">
          <li v-for="s in steps" :key="s.label" :class="`is-${s.state}`" :aria-current="s.state === 'current' ? 'step' : undefined">
            <span class="ac-step-dot" aria-hidden="true" /><span class="ac-step-label">{{ s.label }}</span><span v-if="s.note" class="ac-step-note">{{ s.note }}</span>
          </li>
        </ol>
      </section>

      <section v-if="!pending" class="ac-block" :aria-labelledby="`${uid}-result`">
        <h3 :id="`${uid}-result`">执行结果</h3>
        <div class="ac-result" :class="`tone-${RESULT[e.status].tone}`">
          <strong>{{ RESULT[e.status].title }}</strong>
          <p v-if="e.status === 'executing'">已批准，工作台正在执行；结果出来后这里会更新。不要重复批准。</p>
          <p v-else-if="e.status === 'unknown'">请求可能已经执行。请到对应页面核对；工作台不会自动重试，确认之前不要让智能体重新发起。</p>
          <p v-else-if="e.status === 'expired'">15 分钟内没人处理，已自动失效，没有执行。需要的话请让智能体重新发起。</p>
          <p v-else-if="e.status === 'denied'">实例的权限范围或执行身份的角色不够，工作台直接拒绝，没有执行。</p>
          <p v-else-if="e.status === 'rejected'">没有执行。</p>
          <p v-if="e.result_summary" class="preserve-text">{{ e.result_summary }}</p>
          <p v-if="e.decision_reason" class="preserve-text">拒绝原因：{{ e.decision_reason }}</p>
          <p v-if="e.error_code" class="asv-sub">错误码 <code>{{ e.error_code }}</code></p>
        </div>
      </section>

      <p v-if="notice && !canDecide" role="status" class="ac-notice">{{ notice }}</p>
      <ApprovalActions v-if="canDecide" :key="e.id" :scope="scope" :execution="e" :expired="expired" @notice="notice = $event" />
      <p v-else-if="pending" class="form-help">等待 owner 或 admin 审批。</p>
    </template>
    <p v-else-if="record.error.value" role="alert" class="ac-error">{{ aiError(record.error.value) }}</p>
    <p v-else role="status" class="ws-state-line">正在加载…</p>
  </section>
</template>
