<script setup lang="ts">
import { computed } from 'vue'
import { CircleAlert, Mail } from '@lucide/vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import DataState from '../../components/DataState.vue'
import { ACCOUNT_STATUS, ACCOUNT_TONE, type MailAccount } from './api'

// 邮件设置 → 邮箱账户表（T-0194，效果图 05）：每个已连接的 Gmail 邮箱一行（状态、已分配的智能体、署名是否开启）。
// 需要重新连接的邮箱照常列出并给“重新连接”；点行或“管理”在右侧打开这个邮箱（地址栏 ?account=）。
// 数量与“需要重新连接”的个数都按接口返回的完整列表计算。
const props = defineProps<{ accounts: MailAccount[] | undefined; loading: boolean; loadError: unknown; selectedId: string | null; busy: boolean }>()
const emit = defineEmits<{ select: [id: string]; reconnect: [account: MailAccount]; retry: [] }>()
const reconnectCount = computed(() => props.accounts?.filter(a => a.status === 'needs_reconnect').length ?? 0)
</script>

<template>
  <ManagementSection title="邮箱账户" class="ms-accounts">
    <template #title>
      邮箱账户<template v-if="accounts"> · {{ accounts.length }}</template>
      <span v-if="reconnectCount" class="ms-attention"><CircleAlert aria-hidden="true" />{{ reconnectCount }} 个邮箱需要重新连接</span>
    </template>
    <slot name="connect" />
    <DataState :loading="loading" :error="loadError" :empty="!!accounts && !accounts.length" empty-text="还没有连接邮箱。" :stale="!!accounts" @retry="emit('retry')" />
    <table v-if="accounts?.length" class="ws-table asv-table ms-table">
      <caption class="sr-only">已连接的 Gmail 邮箱</caption>
      <thead><tr><th scope="col">邮箱</th><th scope="col">状态</th><th scope="col">已分配智能体</th><th scope="col">智能体署名</th><th scope="col">操作</th></tr></thead>
      <tbody>
        <tr v-for="a in accounts" :key="a.id" :aria-label="`邮箱：${a.email}`" :aria-selected="a.id === selectedId" @click="emit('select', a.id)">
          <th scope="row">
            <span class="asv-name"><Mail aria-hidden="true" /><span class="ms-email">{{ a.email }}</span></span>
            <span v-if="a.display_name && a.display_name !== a.email" class="asv-sub">{{ a.display_name }}</span>
          </th>
          <td data-label="状态">
            <span class="asv-state" :class="ACCOUNT_TONE[a.status]">{{ ACCOUNT_STATUS[a.status] }}</span>
            <span v-if="a.status === 'active' && !a.can_send" class="asv-sub">只能查收</span>
          </td>
          <td data-label="已分配智能体" :class="{ 'ms-none': !a.agents.length }">{{ a.agents.length ? a.agents.map(x => x.name).join('、') : '未分配' }}</td>
          <td data-label="智能体署名"><span class="ms-flag" :class="{ 'is-on': a.agent_signature }"><span class="ms-flag-track" aria-hidden="true"></span>{{ a.agent_signature ? '开启' : '关闭' }}</span></td>
          <td data-label="操作">
            <span class="ms-row-actions">
              <button v-if="a.status === 'needs_reconnect'" type="button" class="asv-link" :disabled="busy" @click.stop="emit('reconnect', a)">重新连接</button>
              <button type="button" class="asv-link" :aria-current="a.id === selectedId ? 'true' : undefined" @click.stop="emit('select', a.id)">管理</button>
            </span>
          </td>
        </tr>
      </tbody>
    </table>
  </ManagementSection>
</template>
