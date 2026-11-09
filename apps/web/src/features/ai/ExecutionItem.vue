<script setup lang="ts">
import { computed } from 'vue'
import { ArrowRight, Bot } from '@lucide/vue'
import { STATUS_LABELS, type Execution } from './queries'
import { STATUS_TONE, relatedLink, toolIcon } from './approvalFormat'

// 执行异常区的一行（T-0191，效果图 01 底部）：执行失败或结果未知的调用。说明与结果都是服务端记下的文字（只当文本）；
// 能对应到任务 / 日历 / 外部文档的给出核对入口，否则打开详情。不提供重试。
const props = defineProps<{ execution: Execution; selected?: boolean }>()
const emit = defineEmits<{ open: [id: string] }>()
const link = computed(() => relatedLink(props.execution))
</script>

<template>
  <li class="ac-exception" :class="{ 'is-selected': selected }">
    <component :is="toolIcon(execution.tool_name)" aria-hidden="true" class="ac-exception-icon" />
    <button type="button" class="ac-exception-title" @click="emit('open', execution.id)"><span class="preserve-text">{{ execution.summary }}</span></button>
    <span class="ac-agent" :class="{ 'is-gone': !execution.instance_id }"><Bot aria-hidden="true" /><span class="preserve-text">{{ execution.instance_name }}</span></span>
    <span class="asv-state ac-exception-state" :class="`tone-${STATUS_TONE[execution.status]}`">{{ STATUS_LABELS[execution.status] }}</span>
    <span class="ac-exception-text preserve-text">{{ execution.result_summary ?? '没有结果说明' }}</span>
    <RouterLink v-if="link" class="asv-link ac-exception-link" :to="link.to" :aria-label="`${link.label}：${execution.summary}`">{{ link.label }}<ArrowRight aria-hidden="true" /></RouterLink>
    <button v-else type="button" class="asv-link ac-exception-link" :aria-label="`查看详情：${execution.summary}`" @click="emit('open', execution.id)">查看详情<ArrowRight aria-hidden="true" /></button>
  </li>
</template>
