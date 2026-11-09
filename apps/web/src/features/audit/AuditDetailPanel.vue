<script setup lang="ts">
import { computed, toRef } from 'vue'
import { ArrowRight, ChevronDown, Info, X } from '@lucide/vue'
import { ApiError } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { actorText, breakable, eventFacts, eventIcon, eventTitle, fullTime, isKnownEvent, isSecretEvent, rawMetadata, relatedLink } from './display'
import { auditError, isUuid, useAuditDetail, type AuditEntry } from './queries'

// 事件详情（T-0198，效果图 08 右栏；窄屏放进右侧抽屉）：总是按 ID 读 GET /audit-logs/{id}，不在已加载列表、
// 不在当前筛选里的记录也能直接打开（地址里的 ?id=）。不存在、不在当前工作空间或已无权时不留旧内容。
// 只显示记录本身：事件、操作者、对象、记录里的事件信息与已脱敏的原始元数据；相关页面只给链接，不额外读数据。
const props = defineProps<{ scope: string; user: string; entryId: string; aside?: boolean; loaded: (id: string) => AuditEntry | undefined }>()
const emit = defineEmits<{ close: [] }>()
const auth = useAuthStore()
const detail = useAuditDetail(props.scope, props.user, toRef(props, 'entryId'), props.loaded)
const entry = computed(() => detail.data.value ?? null)
const invalid = computed(() => !isUuid(props.entryId))
const gone = computed(() => invalid.value || (detail.error.value instanceof ApiError && [403, 404, 422].includes(detail.error.value.status)))
const actor = computed(() => (entry.value ? actorText(entry.value) : null))
const facts = computed(() => (entry.value ? eventFacts(entry.value) : []))
const link = computed(() => (entry.value ? relatedLink(entry.value, auth.isOwner) : null))
</script>

<template>
  <div class="au-detail" :class="{ 'is-aside': aside }">
    <!-- 抽屉里由抽屉显示标题与关闭按钮 -->
    <div v-if="aside" class="asv-side-head au-detail-head">
      <h2>事件详情</h2>
      <button type="button" class="icon-button" aria-label="关闭事件详情" @click="emit('close')"><X aria-hidden="true" /></button>
    </div>

    <div v-if="gone" class="ws-state-line au-state" role="alert">
      <p>这条审计记录不存在，或你没有权限查看。</p>
      <button type="button" class="secondary-button" @click="emit('close')">关闭</button>
    </div>
    <div v-else-if="detail.error.value && !entry" class="ws-state-line au-state">
      <p role="alert">{{ auditError(detail.error.value) }}</p>
      <button type="button" class="secondary-button" @click="detail.refetch()">重试</button>
    </div>
    <p v-else-if="!entry" role="status" class="ws-state-line">正在加载事件…</p>
    <template v-else>
      <div class="au-detail-title">
        <span class="au-detail-icon" aria-hidden="true"><component :is="eventIcon(entry.action)" /></span>
        <div>
          <h3 class="au-detail-name">{{ eventTitle(entry) }}</h3>
          <code v-if="isKnownEvent(entry)" class="asv-sub au-action"><template v-for="(part, i) in breakable(entry.action)" :key="i">{{ part }}<wbr /></template></code>
          <span v-else class="asv-sub">未收录的事件，按原名显示</span>
        </div>
      </div>

      <dl class="au-facts">
        <div><dt>时间</dt><dd><time :datetime="entry.created_at">{{ fullTime(entry.created_at) }}</time></dd></div>
        <div>
          <dt>操作者</dt>
          <dd>
            <template v-if="actor?.resolved">
              <strong>{{ actor.name }}</strong>
              <span v-if="actor.id" class="asv-sub">actor UUID：<span class="au-id">{{ actor.id }}</span></span>
              <span v-else class="asv-sub">没有操作者账号的事件</span>
            </template>
            <template v-else>
              <span class="au-id">{{ entry.actor_id }}</span>
              <span class="asv-sub">已不是本工作空间的成员，按 ID 显示</span>
            </template>
          </dd>
        </div>
        <div><dt>资源类型</dt><dd><code v-if="entry.resource_type">{{ entry.resource_type }}</code><span v-else class="au-none">无</span></dd></div>
        <div><dt>资源ID</dt><dd><span v-if="entry.resource_id" class="au-id">{{ entry.resource_id }}</span><span v-else class="au-none">无</span></dd></div>
        <div>
          <dt>对象</dt>
          <dd>
            <span v-if="entry.resource_current_name" class="au-inline">
              <span>{{ entry.resource_current_name }}</span><span class="au-tag" title="对象现在的名称，不是记录当时的名称">当前名称</span>
            </span>
            <span v-else-if="entry.resource_type" class="au-none">没有可显示的当前名称（对象已删除，或你无权读取）</span>
            <span v-else class="au-none">无关联对象</span>
          </dd>
        </div>
      </dl>
      <RouterLink v-if="link" :to="link.to" class="asv-link au-related">{{ link.label }}<ArrowRight aria-hidden="true" /></RouterLink>

      <section class="au-block" aria-label="事件信息">
        <h3>事件信息</h3>
        <dl v-if="facts.length" class="au-facts au-info">
          <div v-for="fact in facts" :key="fact.key"><dt>{{ fact.label }}</dt><dd>{{ fact.value }}</dd></div>
        </dl>
        <p v-else class="form-help">这条记录没有附加的事件信息。</p>
        <p v-if="isSecretEvent(entry)" class="mg-hint"><Info aria-hidden="true" />记录不包含密钥原文。</p>
      </section>

      <details class="au-raw-block" open>
        <summary><ChevronDown aria-hidden="true" />原始元数据</summary>
        <pre class="au-json">{{ rawMetadata(entry) }}</pre>
      </details>
    </template>
  </div>
</template>
