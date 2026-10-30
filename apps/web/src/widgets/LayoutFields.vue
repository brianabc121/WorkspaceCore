<script setup lang="ts">
import type { Schema } from '../services/api'
import { layoutSchema } from './registry'
import { ref } from 'vue'
const props = defineProps<{ widget: Schema['WidgetOut']; disabled?: boolean }>()
const emit = defineEmits<{ change: [placement: Schema['WidgetPlacement']] }>()
const invalid = ref(false)
function update(section: 'desktop' | 'mobile', key: string, event: Event) {
  const value = Number((event.target as HTMLInputElement).value)
  const layout = JSON.parse(JSON.stringify(props.widget.layout)) as Schema['WidgetLayout']
  Object.assign(layout[section], { [key]: value })
  const parsed = layoutSchema.safeParse(layout)
  invalid.value = !parsed.success
  if (parsed.success) emit('change', { widget_id: props.widget.id, layout: parsed.data })
}
</script>
<template>
  <details class="layout-fields">
    <summary>键盘调整布局</summary><div class="layout-inputs">
      <label v-for="field in [{ key: 'x', label: '桌面列位置', min: 0, max: 11 }, { key: 'y', label: '桌面行位置', min: 0, max: 999 }, { key: 'w', label: '桌面宽度', min: 1, max: 12 }, { key: 'h', label: '桌面高度', min: 1, max: 24 }]" :key="field.key">{{ field.label }}<input type="number" :aria-label="field.label" :value="widget.layout.desktop[field.key as keyof Schema['DesktopPosition']]" :min="field.min" :max="field.max" :disabled="disabled" @input="update('desktop', field.key, $event)"></label>
      <label>移动端顺序<input type="number" aria-label="移动端顺序" :value="widget.layout.mobile.y" min="0" max="999" :disabled="disabled" @input="update('mobile', 'y', $event)"></label>
      <label>移动端高度<input type="number" aria-label="移动端高度" :value="widget.layout.mobile.h" min="1" max="24" :disabled="disabled" @input="update('mobile', 'h', $event)"></label>
    </div><p v-if="invalid" role="alert">位置或尺寸超出范围，桌面列位置加宽度必须不大于 12。</p>
  </details>
</template>
