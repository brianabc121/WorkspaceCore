<script setup lang="ts">
import { onBeforeUnmount, reactive, ref, watch } from 'vue'
import { Download, FileText } from '@lucide/vue'
import { chatError, fileSize, isImage, type ChatAttachment, type LlmChatApi } from './chatApi'

// 一条消息上的图片和文件（T-0236 第二步）。内容存在 MCP 管理器里，经工作台取回：
// 图片取回后显示成缩略图，点一下另存；别的文件只显示名字和大小，点了才取。不执行、不内联别的类型。
const props = defineProps<{ chat: LlmChatApi; bridgeId: string; sessionId: string; items: ChatAttachment[] }>()

const urls = reactive<Record<string, string>>({}), failed = reactive<Record<string, boolean>>({})
const error = ref(''), saving = ref('')
const abort = new AbortController()
watch(() => props.items.map(a => a.id).join(), () => {
  for (const item of props.items) {
    if (!isImage(item) || urls[item.id] || failed[item.id]) continue
    props.chat.attachmentBlob(props.bridgeId, props.sessionId, item.id, abort.signal)
      .then(blob => { if (!abort.signal.aborted) urls[item.id] = URL.createObjectURL(blob) })
      .catch(() => { if (!abort.signal.aborted) failed[item.id] = true })
  }
}, { immediate: true })
onBeforeUnmount(() => { abort.abort(); for (const url of Object.values(urls)) URL.revokeObjectURL(url) })

async function save(item: ChatAttachment) {
  if (saving.value) return
  saving.value = item.id; error.value = ''
  try {
    const blob = await props.chat.attachmentBlob(props.bridgeId, props.sessionId, item.id)
    const url = URL.createObjectURL(blob), link = document.createElement('a')
    link.href = url; link.download = item.name || 'attachment'; link.rel = 'noopener'
    document.body.append(link); link.click(); link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (e) { error.value = `没能取回「${item.name}」：${chatError(e)}` } finally { saving.value = '' }
}
</script>

<template>
  <ul v-if="items.length" class="lc-files" aria-label="附件">
    <li v-for="item in items" :key="item.id" :class="{ 'is-image': isImage(item) }">
      <button v-if="isImage(item)" type="button" class="lc-thumb" :disabled="saving === item.id" :aria-label="`图片：${item.name}（点击保存）`" :title="`${item.name} · ${fileSize(item.size)}`" @click="save(item)">
        <img v-if="urls[item.id]" :src="urls[item.id]" :alt="item.name" />
        <span v-else>{{ failed[item.id] ? `图片读不到：${item.name}` : '正在读取图片…' }}</span>
      </button>
      <button v-else type="button" class="lc-file" :disabled="saving === item.id" :aria-label="`下载文件：${item.name}`" @click="save(item)">
        <FileText aria-hidden="true" /><span class="lc-file-name">{{ item.name }}</span><span class="lc-file-size">{{ fileSize(item.size) }}</span><Download aria-hidden="true" />
      </button>
    </li>
  </ul>
  <p v-if="error" class="lc-note is-red" role="alert">{{ error }}</p>
</template>
