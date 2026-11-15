<script setup lang="ts">
import { ref } from 'vue'
import { Paperclip, X } from '@lucide/vue'
import { IMAGE_ACCEPT, fileSize } from './chatApi'
import type { LocalFiles } from './useLocalFiles'

// 留在页面里、发送时才上传的附件：添加按钮、已选的列表、拖进来时的提示（useLocalFiles）。
const props = defineProps<{ draft: LocalFiles; disabled?: boolean }>()
const picker = ref<HTMLInputElement | null>(null)
function pick(event: Event) {
  const input = event.target as HTMLInputElement
  props.draft.add(Array.from(input.files ?? []))
  input.value = ''
}
</script>

<template>
  <div class="lc-local-files">
    <input ref="picker" type="file" multiple hidden :accept="draft.imagesOnly.value ? IMAGE_ACCEPT : undefined" aria-label="选择要带上的图片或文件" @change="pick" />
    <p v-if="draft.dragging.value > 0" class="lc-note" role="status">松开鼠标，把{{ draft.imagesOnly.value ? '图片' : '图片或文件' }}加到这条消息里。</p>
    <ul v-if="draft.files.value.length" class="lc-attached" aria-label="要带上的附件">
      <li v-for="(f, i) in draft.files.value" :key="`${i}:${f.name}`">
        <span class="lc-file-name">{{ f.name }}</span><span class="lc-file-size">{{ fileSize(f.size) }}</span>
        <button type="button" class="lc-icon" :aria-label="`去掉附件：${f.name}`" :disabled="disabled" @click="draft.remove(i)"><X aria-hidden="true" /></button>
      </li>
    </ul>
    <button type="button" class="secondary-button lc-attach" :disabled="disabled" @click="picker?.click()">
      <Paperclip aria-hidden="true" />{{ draft.imagesOnly.value ? '添加图片' : '添加图片或文件' }}
    </button>
    <span class="lc-hint">也可以直接粘贴，或者拖进来。{{ draft.imagesOnly.value ? '对话会话只能带图片。' : '文件会放进会话的工作目录。' }}</span>
    <p v-if="draft.error.value" class="lc-note is-red" role="alert">{{ draft.error.value }}</p>
  </div>
</template>
