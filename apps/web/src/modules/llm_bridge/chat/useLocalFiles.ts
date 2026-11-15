import { ref, type Ref } from 'vue'
import { ATTACHMENTS, IMAGE_ACCEPT, type LlmChatApi } from './chatApi'

// 还没有会话可传的地方（新建会话、多模型同问）：附件先留在页面里，发送时再传到会话上。
// 规则和会话里的输入框一致：只收图片的地方别的文件不收，单个 20 MB，一条消息 8 个。
// 添加的方式也一样：点按钮选、粘贴、拖进来。
export function useLocalFiles(imagesOnly: Ref<boolean>) {
  const files = ref<File[]>([]), error = ref(''), dragging = ref(0)
  const isImage = (file: File) => IMAGE_ACCEPT.split(',').includes(file.type)

  function add(chosen: File[]) {
    if (!chosen.length) return
    error.value = ''
    for (const file of chosen) {
      if (imagesOnly.value && !isImage(file)) { error.value = `「${file.name}」不是图片：有对话会话时只能带图片，别的文件要在开发会话里发。`; continue }
      if (files.value.length >= ATTACHMENTS.max) { error.value = `一条消息最多带 ${ATTACHMENTS.max} 个附件。`; break }
      if (file.size > ATTACHMENTS.bytes) { error.value = `「${file.name}」太大：单个最多 20 MB。`; continue }
      files.value = [...files.value, file]
    }
  }
  const remove = (index: number) => { files.value = files.value.filter((_, i) => i !== index) }
  const clear = () => { files.value = []; error.value = '' }
  /** 只收图片的规则变严了（比如加进来一个对话会话）：把已经选的别的文件去掉，并说明 */
  function recheck() {
    if (!imagesOnly.value || files.value.every(isImage)) return
    files.value = files.value.filter(isImage)
    error.value = '有对话会话时只能带图片：已去掉别的文件。'
  }

  // 粘贴：剪贴板里有文件就当附件加上；只有文字时照常粘贴
  function onPaste(event: ClipboardEvent) {
    const pasted = Array.from(event.clipboardData?.files ?? [])
    if (!pasted.length) return
    event.preventDefault()
    add(pasted)
  }
  // 拖拽：只理会拖进来的是文件的情况；进出子元素会各触发一次，用计数判断是不是真的离开了
  const hasFiles = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes('Files')
  const drop = {
    dragenter: (event: DragEvent) => { if (hasFiles(event)) { event.preventDefault(); dragging.value += 1 } },
    dragover: (event: DragEvent) => { if (hasFiles(event)) event.preventDefault() },
    dragleave: (event: DragEvent) => { if (hasFiles(event)) dragging.value = Math.max(0, dragging.value - 1) },
    drop: (event: DragEvent) => {
      if (!hasFiles(event)) return
      event.preventDefault()
      dragging.value = 0
      add(Array.from(event.dataTransfer?.files ?? []))
    },
  }
  return { files, error, dragging, imagesOnly, add, remove, clear, recheck, onPaste, drop }
}
export type LocalFiles = ReturnType<typeof useLocalFiles>

/** 把留在页面里的附件传到一个会话上，返回它们在这个会话里的编号；有一个传不上去就整个失败 */
export async function uploadAll(chat: LlmChatApi, bridge: string, session: string, files: File[]) {
  const ids: string[] = []
  for (const file of files) ids.push((await chat.upload(bridge, session, file)).id)
  return ids
}
