<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { GridStack } from 'gridstack'
import 'gridstack/dist/gridstack.min.css'
import type { Schema } from '../services/api'
import { layoutSchema } from './registry'
const props = defineProps<{ widgets: Schema['WidgetOut'][]; editing: boolean }>()
const emit = defineEmits<{ change: [placements: Schema['WidgetPlacement'][]] }>()
const element = ref<HTMLElement | null>(null)
let grid: GridStack | null = null
let syncing = false
const position = (widget: Schema['WidgetOut'], index: number) => layoutSchema.safeParse(widget.layout).success ? widget.layout.desktop : { x: 0, y: index * 3, w: 6, h: 3 }
function publish() {
  if (!props.editing || syncing || !grid) return
  const placements = grid.engine.nodes.flatMap((node) => {
    const widget = props.widgets.find((widget) => widget.id === node.id)
    if (!widget) return []
    return [{ widget_id: widget.id, layout: { desktop: { x: node.x ?? 0, y: node.y ?? 0, w: node.w ?? 1, h: node.h ?? 1 }, mobile: widget.layout.mobile } }]
  })
  if (placements.some((p) => JSON.stringify(p.layout.desktop) !== JSON.stringify(props.widgets.find((w) => w.id === p.widget_id)?.layout.desktop))) emit('change', placements)
}
onMounted(() => {
  grid = GridStack.init({ column: 12, cellHeight: 72, margin: 8, mode: 'float', handle: '.widget-handle', staticGrid: !props.editing, resizable: { handles: 'se' }, animate: false }, element.value!)
  grid?.on('change', publish)
})
watch(() => props.editing, (editing) => grid?.setStatic(!editing))
watch(() => JSON.stringify(props.widgets.map((w) => w.layout.desktop)), () => {
  if (!grid) return
  syncing = true
  grid.load(props.widgets.map((widget, index) => ({ id: widget.id, ...position(widget, index) })), false)
  syncing = false
  publish()
})
onUnmounted(() => grid?.destroy(false))
</script>
<template>
  <div ref="element" class="grid-stack" aria-label="桌面组件网格">
    <div v-for="(widget, index) in widgets" :key="widget.id" class="grid-stack-item" :gs-id="widget.id" :gs-x="position(widget, index).x" :gs-y="position(widget, index).y" :gs-w="position(widget, index).w" :gs-h="position(widget, index).h" gs-max-h="24">
      <div class="grid-stack-item-content"><slot :widget="widget" /></div>
    </div>
  </div>
</template>
