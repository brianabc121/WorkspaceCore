<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowDown, ArrowUp } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { useModulesStore } from '../../stores/modules'
import { ApiError, dataOf, errorMessage, type Schema } from '../../services/api'
import { canSee, migratePlacements, navigation } from '../../app/navigation'
import { KIND_LABELS, allSections, customKey, defaultSectionKey, sectionOrder, type CustomPage, type NavigationLayout, type Role } from '../../app/sidebar'
import { useNavigationStore } from '../../stores/navigation'
import { newId } from './ids'
import PageForm from './PageForm.vue'

// 设置 → 侧栏与页面（T-0161）：自定义分组、已有页面挪分组、零代码页面。owner 修改，admin 只读。
// 改动先放在草稿里，“保存侧栏布局”时整份提交（带 version，别人先改过返回 409）。
type Draft = { sections: Schema['NavSection'][]; pages: CustomPage[]; placements: Record<string, string>; order: string[] }
const MAX_SECTIONS = 12, MAX_PAGES = 50
const ROLE_LABELS: Record<Role, string> = { viewer: '所有成员', member: '成员及以上', admin: 'owner 与 admin', owner: '只有 owner' }
const FIELDS: Record<string, string> = { title: '名称', url: '地址', path: '站内路径', target_id: '目标', section: '分组', min_role: '谁能看到', kind: '类型' }

const auth = useAuthStore(), modules = useModulesStore(), store = useNavigationStore()
const draft = ref<Draft | null>(null), base = ref('')
const busy = ref(false), error = ref(''), problems = ref<string[]>([]), notice = ref('')
const editing = ref<CustomPage | 'new' | null>(null)
const newSection = ref('')
const canEdit = computed(() => !!store.layout?.can_manage)

function fromLayout(value: NavigationLayout): Draft {
  // 指向已不存在的分组的挪动不保留（侧栏里它们本来就回到了原来的分组）
  const known = new Set(allSections(value).map((section) => section.key))
  // 旧地址（例如 /agents/secrets）换成新地址：保存时一并改过来
  const placements = Object.fromEntries(Object.entries(migratePlacements(value.placements)).filter(([, key]) => known.has(key)))
  return { sections: (value.sections ?? []).map((section) => ({ ...section })), pages: (value.pages ?? []).map((page) => ({ ...page })), placements, order: sectionOrder(value) }
}
function reset(value: NavigationLayout) { draft.value = fromLayout(value); base.value = JSON.stringify(draft.value) }
const dirty = computed(() => !!draft.value && JSON.stringify(draft.value) !== base.value)
// 没有未保存的修改时跟着最新的布局走（别人保存、实时事件刷新）
watch(() => store.layout, (value) => { if (value && !dirty.value) reset(value) }, { immediate: true })
// 打开设置页时读一次最新的
void store.refresh()

const titles = computed(() => new Map(allSections(draft.value).map((section) => [section.key, section.title])))
const groups = computed(() => (draft.value?.order ?? []).map((key) => ({ key, title: titles.value.get(key) ?? key, section: draft.value!.sections.find((item) => customKey(item.id) === key) ?? null })))
const groupOptions = computed(() => groups.value.map(({ key, title }) => ({ key, title: title.trim() || '（未命名）' })))
// 当前用户在侧栏里能看到的已有页面（与侧栏同样按角色、模块启用过滤）
const entries = computed(() => [
  ...navigation.filter((entry) => canSee(entry, auth) && modules.isEnabled(entry.module)),
  ...(auth.canManage ? [{ path: '/settings/audit', title: '审计日志', section: '管理' }] : []),
])

function move<T>(list: T[], index: number, delta: number) {
  const target = index + delta
  if (target < 0 || target >= list.length) return
  const [item] = list.splice(index, 1)
  list.splice(target, 0, item!)
}
function addSection() {
  const title = newSection.value.trim()
  if (!draft.value || !title) return
  if (title.length > 20) { error.value = '分组名称最多 20 个字。'; return }
  const id = newId(), key = customKey(id), admin = draft.value.order.indexOf('admin')
  draft.value.sections.push({ id, title })
  draft.value.order.splice(admin >= 0 ? admin : draft.value.order.length, 0, key)
  newSection.value = ''; error.value = ''
}
function removeSection(key: string) {
  if (!draft.value) return
  const title = titles.value.get(key) ?? ''
  const pages = draft.value.pages.filter((page) => page.section === key).length
  const moved = Object.values(draft.value.placements).filter((value) => value === key).length
  if ((pages || moved) && !window.confirm(`删除分组「${title}」？里面的 ${pages} 个自定义页面会移到“应用”${moved ? `，挪进来的 ${moved} 个页面回到原来的分组` : ''}。`)) return
  draft.value.pages.forEach((page) => { if (page.section === key) page.section = 'apps' })
  for (const [path, value] of Object.entries(draft.value.placements)) if (value === key) delete draft.value.placements[path]
  draft.value.sections = draft.value.sections.filter((section) => customKey(section.id) !== key)
  draft.value.order = draft.value.order.filter((item) => item !== key)
}
function place(path: string, section: string, key: string) {
  if (!draft.value) return
  if (!key || key === defaultSectionKey(section)) delete draft.value.placements[path]
  else draft.value.placements[path] = key
}
function savePage(page: CustomPage) {
  if (!draft.value) return
  const index = draft.value.pages.findIndex((item) => item.id === page.id)
  if (index >= 0) draft.value.pages.splice(index, 1, page)
  else draft.value.pages.push(page)
  editing.value = null
}
function removePage(page: CustomPage) {
  if (draft.value && window.confirm(`删除页面「${page.title}」？`)) draft.value.pages = draft.value.pages.filter((item) => item.id !== page.id)
}
function describe(page: CustomPage) {
  if (page.kind === 'link' || page.kind === 'embed') return page.url ?? ''
  if (page.kind === 'page') return page.path ?? ''
  return ''
}

// 422 的出错位置写成中文：["body", "pages", 0, "url"] → 页面「Grafana」 · 地址
function where(loc: (string | number)[]) {
  const [, area, index, field] = loc
  const suffix = typeof field === 'string' ? ` · ${FIELDS[field] ?? field}` : ''
  if (area === 'pages' && typeof index === 'number') return `页面「${draft.value?.pages[index]?.title ?? index + 1}」${suffix}`
  if (area === 'sections' && typeof index === 'number') return `分组「${draft.value?.sections[index]?.title ?? index + 1}」${suffix}`
  if (area === 'placements') return '页面位置'
  if (area === 'section_order') return '分组顺序'
  return '侧栏布局'
}
function explain(cause: ApiError) {
  const list = ((cause.details as { errors?: { loc?: (string | number)[]; msg?: string }[] } | undefined)?.errors ?? [])
  return list.map((item) => `${where(item.loc ?? [])}：${String(item.msg ?? '').replace(/^Value error, /, '')}`)
}

async function reload() {
  const value = await store.refresh()
  if (value) reset(value)
}
async function save() {
  if (!draft.value || !store.layout || busy.value || !canEdit.value) return
  const value = draft.value
  if (value.sections.some((section) => !section.title.trim() || section.title.trim().length > 20)) { error.value = '分组名称要 1–20 个字。'; problems.value = []; return }
  busy.value = true; error.value = ''; problems.value = []; notice.value = ''
  const body: Schema['NavigationIn'] = { version: store.layout.version, sections: value.sections.map((section) => ({ id: section.id, title: section.title.trim() })), pages: value.pages, placements: value.placements, section_order: value.order }
  try {
    const saved = dataOf(await auth.api.PUT('/api/v1/navigation', { params: { header: { 'X-Workspace-Id': auth.workspaceId! } }, body }))
    // 先用返回的布局更新侧栏，不等实时事件
    store.apply(saved)
    reset(saved)
    notice.value = '侧栏布局已保存。'
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 409) { await reload(); error.value = '侧栏布局已被别人修改，已重新加载。请再改一次。' }
    else if (cause instanceof ApiError && cause.status === 422) { problems.value = explain(cause); error.value = problems.value.length ? '有内容不符合要求：' : errorMessage(cause) }
    else error.value = errorMessage(cause)
  } finally { busy.value = false }
}
function discard() { if (store.layout) { reset(store.layout); error.value = ''; problems.value = []; notice.value = '' } }
</script>

<template>
  <p v-if="!store.loaded && !draft" role="status">正在加载…</p>
  <div v-else-if="!draft" class="data-state"><p role="alert">{{ errorMessage(store.error) }}</p><button type="button" class="secondary-button" @click="store.refresh()">重试</button></div>
  <template v-else>
    <p v-if="!canEdit" class="feedback" role="note">你可以查看；只有 owner 能修改侧栏。</p>
    <section class="panel form-panel nav-settings" aria-labelledby="nav-groups-title">
      <h2 id="nav-groups-title">分组</h2>
      <p class="form-help">侧栏按这里的顺序显示分组；没有页面的分组不显示。内置分组不能改名或删除。</p>
      <ul class="nav-settings-list">
        <li v-for="(group, index) in groups" :key="group.key" :aria-label="`分组：${group.title}`">
          <input v-if="group.section" v-model="group.section.title" class="nav-settings-name" aria-label="分组名称" maxlength="20" :disabled="!canEdit">
          <span v-else class="nav-settings-name">{{ group.title }} <span class="badge">内置</span></span>
          <div v-if="canEdit" class="nav-settings-actions">
            <button type="button" class="icon-button" aria-label="上移" title="上移" :disabled="index === 0" @click="move(draft.order, index, -1)"><ArrowUp class="nav-icon" aria-hidden="true" /></button>
            <button type="button" class="icon-button" aria-label="下移" title="下移" :disabled="index === groups.length - 1" @click="move(draft.order, index, 1)"><ArrowDown class="nav-icon" aria-hidden="true" /></button>
            <button v-if="group.section" type="button" class="icon-button im-danger" @click="removeSection(group.key)">删除</button>
          </div>
        </li>
      </ul>
      <form v-if="canEdit" class="nav-settings-add" @submit.prevent="addSection">
        <label class="form-field"><span>新分组名称</span><input v-model="newSection" maxlength="20" autocomplete="off" placeholder="例如 常用"></label>
        <button type="submit" class="secondary-button" :disabled="!newSection.trim() || draft.sections.length >= MAX_SECTIONS">添加分组</button>
      </form>
    </section>

    <section class="panel form-panel nav-settings" aria-labelledby="nav-places-title">
      <h2 id="nav-places-title">页面位置</h2>
      <p class="form-help">把已有页面挪到别的分组；选“默认”放回原来的分组。成员只会看到自己有权限的页面。</p>
      <ul class="nav-settings-list">
        <li v-for="entry in entries" :key="entry.path">
          <span class="nav-settings-name">{{ entry.title }} <small>{{ entry.path }}</small></span>
          <select :aria-label="`${entry.title}所在分组`" :value="draft.placements[entry.path] ?? ''" :disabled="!canEdit" @change="place(entry.path, entry.section, ($event.target as HTMLSelectElement).value)">
            <option value="">默认（{{ titles.get(defaultSectionKey(entry.section)) }}）</option>
            <option v-for="group in groupOptions" :key="group.key" :value="group.key">{{ group.title }}</option>
          </select>
        </li>
      </ul>
    </section>

    <section class="panel form-panel nav-settings" aria-labelledby="nav-pages-title">
      <h2 id="nav-pages-title">自定义页面</h2>
      <p class="form-help">外部链接、站内页面、知识库笔记、仪表盘和嵌入页，放在侧栏的任意分组里。</p>
      <p v-if="!draft.pages.length" class="form-help">还没有自定义页面。</p>
      <ul v-else class="nav-settings-list">
        <li v-for="(page, index) in draft.pages" :key="page.id" :aria-label="`页面：${page.title}`">
          <span class="nav-settings-name">{{ page.title }} <span class="badge">{{ KIND_LABELS[page.kind] }}</span><small>{{ titles.get(page.section) ?? page.section }} · {{ ROLE_LABELS[page.min_role] }}<template v-if="describe(page)"> · {{ describe(page) }}</template></small></span>
          <div v-if="canEdit" class="nav-settings-actions">
            <button type="button" class="icon-button" aria-label="上移" title="上移" :disabled="index === 0" @click="move(draft.pages, index, -1)"><ArrowUp class="nav-icon" aria-hidden="true" /></button>
            <button type="button" class="icon-button" aria-label="下移" title="下移" :disabled="index === draft.pages.length - 1" @click="move(draft.pages, index, 1)"><ArrowDown class="nav-icon" aria-hidden="true" /></button>
            <button type="button" class="icon-button" @click="editing = page">编辑</button>
            <button type="button" class="icon-button im-danger" @click="removePage(page)">删除</button>
          </div>
        </li>
      </ul>
      <button v-if="canEdit" type="button" class="secondary-button" :disabled="draft.pages.length >= MAX_PAGES" @click="editing = 'new'">添加页面</button>
    </section>

    <div v-if="canEdit" class="nav-settings-save">
      <p v-if="error" role="alert" class="feedback">{{ error }}<template v-if="problems.length"><br><template v-for="item in problems" :key="item">{{ item }}<br></template></template></p>
      <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
      <div class="form-actions">
        <button type="button" class="primary-button" :disabled="busy || !dirty" @click="save">{{ busy ? '正在保存…' : '保存侧栏布局' }}</button>
        <button type="button" class="secondary-button" :disabled="busy || !dirty" @click="discard">放弃修改</button>
        <span v-if="dirty" class="form-help">有未保存的修改</span>
      </div>
    </div>
    <PageForm v-if="editing" :page="editing === 'new' ? undefined : editing" :groups="groupOptions" :embed-origins="store.layout?.embed_origins ?? []" @close="editing = null" @save="savePage" />
  </template>
</template>

<style scoped>
.nav-settings { margin-bottom: 16px; min-width: 0; overflow-wrap: anywhere; }
.nav-settings-list { list-style: none; margin: 8px 0; padding: 0; display: grid; }
.nav-settings-list li { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 12px; padding: 10px 0; border-top: 1px solid var(--line); min-width: 0; }
.nav-settings-name { flex: 1 1 180px; min-width: 0; display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; }
.nav-settings-name small { flex-basis: 100%; color: var(--muted); font-size: .78rem; overflow-wrap: anywhere; }
input.nav-settings-name { flex: 1 1 160px; }
.nav-settings-list select { flex: 0 1 220px; min-width: 0; }
.nav-settings-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.nav-settings-actions .icon-button { display: inline-flex; align-items: center; justify-content: center; min-width: 36px; }
.nav-settings-add { display: flex; flex-wrap: wrap; align-items: end; gap: 8px; }
.nav-settings-add .form-field { flex: 1 1 200px; margin: 12px 0 0; }
.nav-settings-save { position: sticky; bottom: 0; padding: 12px 0; background: var(--bg); z-index: 1; }
@media (max-width: 760px) { .nav-settings-list select { flex-basis: 100%; } .nav-settings-save { bottom: calc(56px + env(safe-area-inset-bottom)); } }
</style>
