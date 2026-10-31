<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, useId } from 'vue'
const props = defineProps<{ title: string; busy?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const panel = ref<HTMLElement | null>(null)
const id = useId()
const previous = document.activeElement as HTMLElement | null
function keys(event: KeyboardEvent) {
  if ([...document.querySelectorAll('[role="dialog"][aria-modal="true"]')].at(-1) !== panel.value) return
  if (event.key === 'Escape') { event.stopPropagation(); if (!props.busy) emit('close') }
  if (event.key !== 'Tab') return
  const items = [...panel.value!.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href]')]
  const first = items[0], last = items.at(-1)
  if (!panel.value?.contains(document.activeElement)) { event.preventDefault(); first?.focus() }
  else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
onMounted(async () => { document.addEventListener('keydown', keys); await nextTick(); (panel.value?.querySelector<HTMLElement>('[autofocus]') ?? panel.value?.querySelector<HTMLElement>('input,textarea,select,button'))?.focus() })
onUnmounted(() => { document.removeEventListener('keydown', keys); previous?.focus() })
</script>
<template>
  <div class="dialog-backdrop"><section ref="panel" class="panel modal-panel" role="dialog" aria-modal="true" :aria-labelledby="id"><div class="section-heading"><h2 :id="id">{{ title }}</h2><button class="icon-button" :disabled="busy" aria-label="关闭对话框" @click="$emit('close')">关闭</button></div><slot /></section></div>
</template>
