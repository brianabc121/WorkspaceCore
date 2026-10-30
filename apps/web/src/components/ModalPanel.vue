<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, useId } from 'vue'
defineProps<{ title: string; busy?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const panel = ref<HTMLElement | null>(null)
const id = useId()
const previous = document.activeElement as HTMLElement | null
function keys(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.stopPropagation(); emit('close') }
  if (event.key !== 'Tab') return
  const items = [...panel.value!.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href]')]
  const first = items[0], last = items.at(-1)
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
onMounted(async () => { await nextTick(); (panel.value?.querySelector<HTMLElement>('[autofocus]') ?? panel.value?.querySelector<HTMLElement>('input,textarea,select,button'))?.focus() })
onUnmounted(() => previous?.focus())
</script>
<template>
  <div class="dialog-backdrop" @keydown="keys"><section ref="panel" class="panel modal-panel" role="dialog" aria-modal="true" :aria-labelledby="id"><div class="section-heading"><h2 :id="id">{{ title }}</h2><button class="icon-button" :disabled="busy" aria-label="关闭对话框" @click="$emit('close')">关闭</button></div><slot /></section></div>
</template>
