<script setup lang="ts">
import { computed, ref, toRef, watch } from 'vue'
import { Copy, Download, File, FileText, Folder, Home, LayoutGrid, Link2, List, RefreshCw, Search, Settings, X } from '@lucide/vue'
import { answerHtml } from './chat/markdown'
import { bridgeError, platformName, type EnvSession, type Environment, type LlmBridgeApi, type WorkEntry } from './api'
import { isMarkdown, join, sizeText, timeText, typeName } from './workFiles'

// 开发环境的工作目录（T-0289，效果图“开发环境”）：中间是选中的环境、它的哪个会话、当前文件夹里的文件；右边预览选中的文件。
// 文件来自那个会话在 MCP 管理器里的工作目录（人类 2026-11-17 选的做法）；只读。
// 预览的文字已经脱敏（看起来像密钥、令牌的换成 ***）；Markdown 可以切换“预览 / 源码”，预览按知识库的安全渲染排版。
// 下载给原文件（人类决定允许 owner / admin 下载原文件），工作台记一条审计。
const props = defineProps<{ api: LlmBridgeApi; bridgeId: string; env: Environment; sessions: EnvSession[]; sessionsLoading: boolean; session: string | null; path: string; canWrite: boolean }>()
const emit = defineEmits<{ 'update:session': [id: string]; 'update:path': [path: string]; settings: [] }>()

const sessionNow = computed(() => props.sessions.find(s => s.session_id === props.session) ?? null)
const browsable = computed(() => !!sessionNow.value?.browsable)
const listing = props.api.useWorkFiles(toRef(() => props.bridgeId), toRef(() => (browsable.value ? props.session : null)), toRef(() => props.path))
const entries = computed(() => listing.data.value?.entries ?? [])

// 按名字找（只在当前文件夹里）、列表 / 网格
const query = ref('')
watch(() => props.path, () => { query.value = '' })
const shown = computed(() => { const q = query.value.trim().toLowerCase(); return q ? entries.value.filter(e => e.name.toLowerCase().includes(q)) : entries.value })
const grid = ref(false)
const counts = computed(() => ({ dirs: entries.value.filter(e => e.kind === 'dir').length, files: entries.value.filter(e => e.kind !== 'dir').length }))

// 路径：工作目录 / a / b
const crumbs = computed(() => { const parts = props.path ? props.path.split('/') : []; return parts.map((name, i) => ({ name, path: parts.slice(0, i + 1).join('/') })) })
const copied = ref(false)
async function copyPath() {
  try { await navigator.clipboard.writeText(props.path || '.'); copied.value = true; setTimeout(() => { copied.value = false }, 1500) } catch { /* 浏览器不让写剪贴板：不提示 */ }
}

// 选中的文件和它的预览
const picked = ref<string | null>(null)
watch(() => [props.session, props.path] as const, () => { picked.value = null })
function open(entry: WorkEntry) {
  if (entry.kind === 'dir') emit('update:path', join(props.path, entry.name))
  else if (entry.kind === 'file') picked.value = join(props.path, entry.name)
}
const file = props.api.useWorkFile(toRef(() => props.bridgeId), toRef(() => (browsable.value ? props.session : null)), picked)
const view = ref<'preview' | 'source'>('preview')
watch(picked, () => { view.value = 'preview' })
const markdown = computed(() => !!file.data.value && isMarkdown(file.data.value.name) && !file.data.value.binary)
const html = computed(() => (markdown.value ? answerHtml(file.data.value!.text) : ''))
const pickedName = computed(() => picked.value?.split('/').pop() ?? '')

const downloading = ref(false), downloadError = ref('')
async function download() {
  if (!picked.value || !props.session || downloading.value) return
  downloading.value = true; downloadError.value = ''
  try {
    const blob = await props.api.workFileBlob(props.bridgeId, props.session, picked.value)
    const url = URL.createObjectURL(blob), link = document.createElement('a')
    link.href = url; link.download = pickedName.value || 'file'; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (e) { downloadError.value = bridgeError(e) } finally { downloading.value = false }
}
const icon = (entry: WorkEntry) => (entry.kind === 'dir' ? Folder : entry.kind === 'link' ? Link2 : /\.(md|markdown|txt)$/i.test(entry.name) ? FileText : File)
const sessionLabel = (s: EnvSession) => `${s.title || '（没有标题）'} · ${timeText(s.updated_at)}${s.browsable ? '' : ' · 工作目录已不在'}`
</script>

<template>
  <section class="lb-files" :aria-label="`工作目录：${env.name}`">
    <header class="lb-files-head">
      <h3>{{ env.name }}</h3>
      <span class="lb-files-plat">{{ platformName(env.platform) }}</span>
      <span class="asv-state" :class="env.enabled ? 'tone-green' : 'tone-muted'">{{ env.enabled ? '已启用' : '已停用' }}</span>
      <button v-if="canWrite" type="button" class="secondary-button lb-head-btn lb-files-settings" :aria-label="`环境设置：${env.name}`" @click="emit('settings')"><Settings aria-hidden="true" />环境设置</button>
    </header>

    <p v-if="sessionsLoading" role="status" class="data-state">正在读取这个环境的会话…</p>
    <p v-else-if="!sessions.length" class="lb-files-none">这个环境还没有会话。用它新建一个开发会话后，这里能看到那个会话的工作目录（仓库克隆在里面，智能体在里面改文件）。</p>
    <template v-else>
      <div class="lb-files-session">
        <label for="lb-files-session">看哪个会话的工作目录</label>
        <select id="lb-files-session" :value="session ?? ''" @change="emit('update:session', ($event.target as HTMLSelectElement).value)">
          <option v-for="s in sessions" :key="s.session_id" :value="s.session_id">{{ sessionLabel(s) }}</option>
        </select>
      </div>
      <p v-if="!browsable" class="lb-files-none">这个会话的工作目录已经不在了（会话删除或清理过），或者它是官方程序里同步来的会话，不能浏览。换一个会话看看。</p>
      <template v-else>
        <nav class="lb-crumbs" aria-label="当前位置">
          <Home aria-hidden="true" />
          <button type="button" class="asv-link" :aria-current="path ? undefined : 'location'" @click="emit('update:path', '')">工作目录</button>
          <template v-for="c in crumbs" :key="c.path"><span aria-hidden="true">/</span><button type="button" class="asv-link" :aria-current="c.path === path ? 'location' : undefined" @click="emit('update:path', c.path)">{{ c.name }}</button></template>
          <button type="button" class="lb-icon-btn lb-crumbs-copy" :title="copied ? '已复制' : '复制路径'" :aria-label="copied ? '已复制路径' : '复制路径'" @click="copyPath"><Copy aria-hidden="true" /></button>
        </nav>
        <div class="lb-files-tools">
          <div class="lb-find">
            <Search aria-hidden="true" />
            <input v-model="query" type="search" maxlength="80" aria-label="在当前文件夹里找" placeholder="搜索当前目录" />
          </div>
          <button type="button" class="lb-icon-btn" title="刷新" aria-label="刷新文件列表" :disabled="listing.isFetching.value" @click="listing.refetch()"><RefreshCw aria-hidden="true" /></button>
          <div class="lb-view-toggle" role="group" aria-label="显示方式">
            <button type="button" class="lb-icon-btn" :aria-pressed="!grid" title="列表" aria-label="列表" @click="grid = false"><List aria-hidden="true" /></button>
            <button type="button" class="lb-icon-btn" :aria-pressed="grid" title="网格" aria-label="网格" @click="grid = true"><LayoutGrid aria-hidden="true" /></button>
          </div>
        </div>

        <p v-if="listing.isPending.value" role="status" class="data-state">正在读取文件…</p>
        <div v-else-if="listing.error.value" class="data-state"><p role="alert">{{ bridgeError(listing.error.value) }}</p><button type="button" class="secondary-button" @click="listing.refetch()">重试</button></div>
        <p v-else-if="!shown.length" class="lb-files-none">{{ query ? '当前文件夹里没有名字带这几个字的文件。' : '这个文件夹是空的。' }}</p>
        <ul v-else-if="grid" class="lb-files-grid" aria-label="文件">
          <li v-for="e in shown" :key="e.name">
            <button type="button" class="lb-file-tile" :aria-current="picked === join(path, e.name) ? 'true' : undefined" :disabled="e.kind === 'link'" :title="e.name" @click="open(e)"><component :is="icon(e)" aria-hidden="true" /><span>{{ e.name }}</span></button>
          </li>
        </ul>
        <table v-else class="lb-files-table" aria-label="文件">
          <thead><tr><th scope="col">名称</th><th scope="col">类型</th><th scope="col">大小</th><th scope="col">修改时间</th></tr></thead>
          <tbody>
            <tr v-for="e in shown" :key="e.name" :class="{ 'is-picked': picked === join(path, e.name) }">
              <th scope="row"><button type="button" class="lb-file-name" :data-kind="e.kind" :disabled="e.kind === 'link'" :title="e.kind === 'link' ? '指向工作目录以外，打不开' : e.name" @click="open(e)"><component :is="icon(e)" aria-hidden="true" /><span>{{ e.name }}</span></button></th>
              <td>{{ typeName(e) }}</td>
              <td class="lb-nowrap">{{ e.kind === 'file' ? sizeText(e.size) : '—' }}</td>
              <td class="lb-nowrap">{{ timeText(e.modified) }}</td>
            </tr>
          </tbody>
        </table>
        <p class="lb-files-foot"><span>{{ counts.dirs }} 个文件夹 · {{ counts.files }} 个文件<template v-if="listing.data.value && listing.data.value.total > entries.length">（只列出前 {{ entries.length }} 项，共 {{ listing.data.value.total }} 项）</template></span><span v-if="picked">已选中 {{ pickedName }}</span></p>
      </template>
    </template>
  </section>

  <aside v-if="picked" class="lb-preview" :aria-label="`文件预览：${pickedName}`">
    <header class="lb-preview-head">
      <span class="lb-preview-icon" aria-hidden="true"><FileText /></span>
      <div class="lb-preview-title"><strong :title="picked">{{ pickedName }}</strong><span v-if="file.data.value">{{ typeName({ name: file.data.value.name, kind: 'file' }) }} · {{ sizeText(file.data.value.size) }}</span></div>
      <button type="button" class="lb-icon-btn" :disabled="downloading || !file.data.value?.downloadable" :title="file.data.value && !file.data.value.downloadable ? '文件太大，不能下载（最多 50 MB）' : '下载原文件'" :aria-label="`下载${pickedName}`" @click="download"><Download aria-hidden="true" /></button>
      <button type="button" class="lb-icon-btn" aria-label="关闭预览" title="关闭预览" @click="picked = null"><X aria-hidden="true" /></button>
    </header>
    <p v-if="downloadError" role="alert" class="lb-warn is-red">{{ downloadError }}</p>
    <p v-if="file.isPending.value" role="status" class="data-state">正在读取…</p>
    <p v-else-if="file.error.value" role="alert" class="lb-warn is-red">{{ bridgeError(file.error.value) }}</p>
    <template v-else-if="file.data.value">
      <div v-if="markdown" class="lb-preview-tabs" role="tablist" aria-label="预览方式">
        <button type="button" role="tab" :aria-selected="view === 'preview'" @click="view = 'preview'">预览</button>
        <button type="button" role="tab" :aria-selected="view === 'source'" @click="view = 'source'">源码</button>
      </div>
      <p v-if="file.data.value.binary" class="lb-files-none">这是二进制文件，不能预览{{ file.data.value.downloadable ? '；可以下载原文件。' : '。' }}</p>
      <!-- eslint-disable-next-line vue/no-v-html -- 知识库的安全渲染：不允许原始 HTML，结果经 DOMPurify 消毒 -->
      <div v-else-if="markdown && view === 'preview'" class="lb-preview-md" v-html="html" />
      <pre v-else class="lb-preview-src">{{ file.data.value.text }}</pre>
      <p v-if="file.data.value.truncated" class="form-help">文件比较长，只显示了开头 512 KB；完整内容请下载。</p>
      <p class="lb-preview-foot">{{ file.data.value.binary ? '二进制' : 'UTF-8' }} · {{ typeName({ name: file.data.value.name, kind: 'file' }) }} · 内容已脱敏（看起来像密钥的换成了 ***）</p>
    </template>
  </aside>
</template>
