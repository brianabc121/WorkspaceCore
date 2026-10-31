<script setup lang="ts">
import { ref } from 'vue'
import type { Schema } from '../../services/api'
import { registry, widgetSchema, type WidgetValue, type WidgetType } from '../../widgets/registry'
import ModalPanel from '../../components/ModalPanel.vue'
const props = defineProps<{ widget?: Schema['WidgetOut']; bottom: number; busy: boolean; error: string }>()
defineEmits<{ close: []; save: [value: WidgetValue] }>()
const type = ref<WidgetType>((props.widget?.widget_type as WidgetType) ?? 'note')
const config = ref(JSON.stringify(props.widget?.config ?? { text: '' }, null, 2))
const invalid = ref(false)
function changeType() { config.value = JSON.stringify(registry[type.value].configSchema.parse({}), null, 2) }
function parse(): WidgetValue | null {
  try {
    const size = registry[type.value].defaultSize
    const y = Math.min(props.bottom, 999)
    const result = widgetSchema.safeParse({ widget_type: type.value, config: JSON.parse(config.value), layout: props.widget?.layout ?? { desktop: { x: 0, y, ...size }, mobile: { y, h: size.h } } })
    invalid.value = !result.success
    return result.success ? result.data : null
  } catch { invalid.value = true; return null }
}
</script>
<template>
  <ModalPanel :title="widget ? '配置组件' : '添加组件'" :busy="busy" @close="!busy && $emit('close')">
    <form @submit.prevent="((value) => value && $emit('save', value))(parse())">
      <label class="form-field">组件类型<select v-model="type" :disabled="!!widget || busy" @change="changeType"><option v-for="definition in registry" :key="definition.type" :value="definition.type">{{ definition.title }}</option></select></label>
      <p class="form-help">文本便签填写 text；常用链接填写 links（title / http(s) url）；今日任务与最近文档填写 limit（1–20）；近期日程填写 days（1–14）与 limit（1–20）；GitHub 动态填写 kind（runs / pulls / issues）与 limit（1–20）；服务状态填写 limit（1–20）；API 延迟填写 range（1h / 6h / 24h）与 quantile（p50 / p95 / p99）；通知填写 limit（1–20）与 unread_only（true / false）。</p>
      <label class="form-field">组件配置 JSON<textarea v-model="config" rows="9" :disabled="busy" spellcheck="false"></textarea></label>
      <p v-if="invalid" role="alert">组件配置或布局无效，请按所选类型的规则填写。</p><p v-if="error" role="alert">{{ error }}</p>
      <button class="primary-button" :disabled="busy">{{ busy ? '正在提交…' : '保存组件' }}</button>
    </form>
  </ModalPanel>
</template>
