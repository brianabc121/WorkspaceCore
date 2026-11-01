import { onScopeDispose, ref, watch, type Ref } from 'vue'

// /api/v1/im/media needs X-Workspace-Id, so <img src> cannot load it directly. The shared API client reads
// bodies as text, which would corrupt images, so this uses fetch with the same-origin session cookie.
export async function fetchMedia(scope: string, mediaId: string, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(new Request(`${window.location.origin}/api/v1/im/media/${encodeURIComponent(mediaId)}`, { credentials: 'same-origin', headers: { 'X-Workspace-Id': scope }, signal }))
  if (!response.ok) throw new Error(`media ${response.status}`)
  const blob = await response.blob()
  if (!blob.type.startsWith('image/')) throw new Error('not an image')
  return blob
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
