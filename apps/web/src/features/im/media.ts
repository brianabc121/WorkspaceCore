import { onScopeDispose, ref, watch, type Ref } from 'vue'
import { ApiError, type Schema } from '../../services/api'

// /api/v1/im/media needs X-Workspace-Id, so <img src> cannot load it directly. The shared API client reads
// bodies as text, which would corrupt images, so this uses fetch with the same-origin session cookie.
export async function fetchMedia(scope: string, mediaId: string, signal?: AbortSignal, expect: 'image' | 'any' = 'image'): Promise<Blob> {
  const response = await fetch(new Request(`${window.location.origin}/api/v1/im/media/${encodeURIComponent(mediaId)}`, { credentials: 'same-origin', headers: { 'X-Workspace-Id': scope }, signal }))
  if (!response.ok) throw new Error(`media ${response.status}`)
  const blob = await response.blob()
  if (expect === 'image' && !blob.type.startsWith('image/')) throw new Error('not an image')
  return blob
}

// 文件消息（T-0119）：取回后交给浏览器另存为；服务端一律按附件下载，这里只是带上工作空间头
export async function downloadMedia(scope: string, mediaId: string, name: string): Promise<void> {
  const blob = await fetchMedia(scope, mediaId, undefined, 'any')
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url; link.download = name; link.rel = 'noopener'
  document.body.appendChild(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// 视频消息（T-0119）：点“播放”时才取回，取回后用对象地址播放
export async function videoUrl(scope: string, mediaId: string): Promise<string> {
  return URL.createObjectURL(await fetchMedia(scope, mediaId, undefined, 'any'))
}

export function useMediaUrl(scope: string, mediaId: Ref<string | null>) {
  const url = ref<string | null>(null), failed = ref(false)
  let controller: AbortController | null = null
  const release = () => { controller?.abort(); controller = null; if (url.value) URL.revokeObjectURL(url.value); url.value = null }
  watch(mediaId, async (id) => {
    release(); failed.value = false
    if (!id) return
    const current = new AbortController(); controller = current
    try {
      const blob = await fetchMedia(scope, id, current.signal)
      if (controller === current) url.value = URL.createObjectURL(blob)
    } catch { if (controller === current) failed.value = true }
  }, { immediate: true })
  onScopeDispose(release)
  return { url, failed }
}

// Export a conversation as NDJSON (owner / admin; the server writes an audit entry). The shared client reads
// bodies as text and the file can be large, so fetch it and hand the bytes to the browser.
export async function downloadConversation(scope: string, conversationId: string): Promise<string> {
  let response: Response
  try {
    response = await fetch(new Request(`${window.location.origin}/api/v1/im/conversations/${encodeURIComponent(conversationId)}/export`, { credentials: 'same-origin', headers: { 'X-Workspace-Id': scope } }))
  } catch { throw new ApiError(0, 'NETWORK_ERROR', '请求未完成') }
  if (!response.ok) {
    let detail: Partial<Schema['ErrorBody']> = {}
    try { detail = ((await response.json()) as Schema['ErrorResponse']).error ?? {} } catch { /* gateway HTML */ }
    throw new ApiError(response.status, detail.code ?? 'HTTP_ERROR', detail.message ?? '请求失败', detail.request_id ?? null)
  }
  const blob = await response.blob()
  const name = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') ?? '')?.[1] ?? `${conversationId}.ndjson`
  const url = URL.createObjectURL(blob), link = document.createElement('a')
  link.href = url; link.download = name; document.body.append(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  return name
}
