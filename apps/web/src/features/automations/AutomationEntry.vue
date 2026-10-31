<script setup lang="ts">
import { computed, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { useAutomationAccess } from './queries'
import type { Schema } from '../../services/api'
import { runLabels } from './validation'
import { formatTime } from '../time'
import TriggerForm from './TriggerForm.vue'
import RunList from './RunList.vue'
const props = defineProps<{ item: Schema['AutomationOut']; connection: Schema['SyncState']; locked: boolean }>()
defineEmits<{ edit: []; remove: [] }>()
const auth = useAuthStore(), access = useAutomationAccess(), records = ref(false), triggering = ref(false)
const permitted = computed(() => !props.locked && !access.denied && props.item.enabled && props.item.workflow_active === true && props.connection.status === 'ok' && auth.currentWorkspace?.role !== 'viewer')
</script>
<template>
  <section class="panel automation-entry" role="region" :aria-label="item.name">
    <div class="section-heading"><div><h2>{{ item.name }}</h2><p class="preserve-text">{{ item.description }}</p></div><span class="status-badge">{{ !item.enabled ? '已停用' : item.workflow_active === true ? '已激活' : item.workflow_active === false ? '尚未激活' : '激活状态未知' }}</span></div>
    <p v-if="item.last_run">最近执行：{{ runLabels[item.last_run.status] }} · {{ formatTime(item.last_run.created_at) }}</p><p v-else>尚无执行记录。</p><p v-if="item.last_run?.error_summary" class="feedback preserve-text">{{ item.last_run.error_summary }}</p>
    <div class="form-actions"><button v-if="auth.currentWorkspace?.role !== 'viewer'" class="primary-button" :aria-label="'触发：' + item.name" :disabled="!permitted" @click="triggering = true">触发</button><button class="secondary-button" @click="records = !records">{{ records ? '收起运行记录' : '查看运行记录' }}</button><template v-if="auth.canManage"><button class="secondary-button" :disabled="locked" @click="$emit('edit')">编辑工作流</button><button class="secondary-button" :disabled="locked" @click="$emit('remove')">移出白名单</button></template></div>
    <RunList v-if="records" :id="item.id" :name="item.name" /><TriggerForm v-if="triggering" :automation="item" :permitted="permitted" @close="triggering = false" @records="records = true" />
  </section>
</template>
