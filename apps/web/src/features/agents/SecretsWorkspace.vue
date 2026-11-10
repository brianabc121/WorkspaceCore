<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQuery } from 'vue-router'
import { useQueryClient } from '@tanstack/vue-query'
import { Info, Plus, RefreshCw, Search } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'
import { useMediaQuery } from '../../composables/useMediaQuery'
import ManagementPage from '../../components/management/ManagementPage.vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import DrawerDetail from '../../components/DrawerDetail.vue'
import ModalPanel from '../../components/ModalPanel.vue'
import { VAULT_KIND, unique, useSecrets, type SecretFilters } from './queries'
import { useAgentAction } from './action'
import { secretError } from './secrets'
import SecretCatalogTable from './SecretCatalogTable.vue'
import SecretDetailPanel from './SecretDetailPanel.vue'
import SecretAccessLog from './SecretAccessLog.vue'
import './secrets.css'

// 密钥库（T-0196，效果图 07；规范第 10 节、契约第 4 节）：密钥列表与取用记录两个标签，选中的密钥在右侧详情（1440 以下放进右侧抽屉）。
// 地址里只有 ?tab=accesses 与 ?secret=<id>：刷新、通知链接、后退都能回到同一处。名称 / 说明搜索与类型筛选在服务端、跨所有分页，
// 标题里的数是服务端 total；换条件就是另一条查询，旧条件的页不会拼进来。只有元数据：值只写不读，新值只在输入框里。
// 只有 owner 能进这个页面（路由规则），服务端同样只给 owner。
// 中转站管理（T-0206）把它嵌进“兼容凭据”页签（embedded），内层标签用 ?view=（外层页签已占用 ?tab=）。
// 只放非模型凭据：模型服务的 Key 只在中转站管理的连接里保存，服务端拒绝在这里新建 model_api_key（T-0209）。
const props = withDefaults(defineProps<{ embedded?: boolean; title?: string; description?: string; tabParam?: string }>(), {
  embedded: false, title: '密钥库', description: '管理智能体可取用的密钥、授权对象与取用记录。只有 owner 可以管理。', tabParam: 'tab',
})
type Tab = 'list' | 'accesses'
const TABS: { key: Tab; name: string }[] = [{ key: 'list', name: '密钥列表' }, { key: 'accesses', name: '取用记录' }]
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const route = useRoute(), router = useRouter(), cache = useQueryClient()
const wide = typeof window.matchMedia === 'function' ? useMediaQuery('(min-width: 1440px)') : ref(false)

const tab = computed<Tab>(() => (route.query[props.tabParam] === 'accesses' ? 'accesses' : 'list'))
const selected = computed(() => (typeof route.query.secret === 'string' && route.query.secret ? route.query.secret : null))
function update(patch: Record<string, string | undefined>, push = false) {
  const query: LocationQuery = { ...route.query }
  for (const [k, v] of Object.entries(patch)) { if (v === undefined || v === '') delete query[k]; else query[k] = v }
  void (push ? router.push({ query }) : router.replace({ query }))
}

// 搜索停顿 300 毫秒再查；空白不发 q（服务端要求 1–100 个字符）
const search = ref(''), q = ref(''), kind = ref<'' | Schema['AgentSecretOut']['kind']>('')
let timer: ReturnType<typeof setTimeout> | undefined
watch(search, value => { clearTimeout(timer); timer = setTimeout(() => { q.value = value.trim().slice(0, 100) }, 300) })
onBeforeUnmount(() => clearTimeout(timer))
const filters = computed<SecretFilters>(() => ({ ...(q.value ? { q: q.value } : {}), ...(kind.value ? { kind: kind.value } : {}) }))
const filtered = computed(() => !!(filters.value.q || filters.value.kind))
const secrets = useSecrets(scope, () => filters.value)
const items = computed(() => unique(secrets.data.value?.pages))
const total = computed(() => secrets.data.value?.pages.at(-1)?.total)

// 详情：宽屏右侧一栏，窄屏抽屉；关掉后焦点回到打开它的那一行
let opener: HTMLElement | null = null
function select(id: string, row: HTMLElement | null) { opener = row; if (id !== selected.value) update({ secret: id }, true) }
async function close() {
  const id = selected.value
  update({ secret: undefined })
  const back = opener
  opener = null
  await nextTick()
  if (back?.isConnected) back.querySelector<HTMLElement>('button')?.focus()
  else if (id) document.querySelector<HTMLElement>(`tr[data-secret="${CSS.escape(id)}"] button`)?.focus()
}
const { busy, error, notice, act, header } = useAgentAction(scope, secretError)
function deleted(name: string) {
  update({ secret: undefined })
  error.value = ''
  notice.value = `密钥「${name}」已删除`
}
// 详情里的“查看全部取用记录”：切到取用记录标签并按这个密钥筛选；窄屏收起抽屉，免得挡住列表
const accessSecret = ref<{ id: string; name: string } | null>(null)
function showAccesses(secret: { id: string; name: string }) {
  accessSecret.value = secret
  update({ [props.tabParam]: 'accesses', secret: wide.value ? selected.value ?? undefined : undefined })
}

const refreshing = computed(() => secrets.isFetching.value)
function refresh() {
  void cache.invalidateQueries({ predicate: query => query.queryKey[0] === 'agents' && query.queryKey[1] === user && query.queryKey[2] === scope && ['secrets', 'secret', 'accesses', 'instances'].includes(String(query.queryKey[3])) })
}

// 添加密钥：值只在这个对话框的输入框里，提交或关闭就清空
const creating = ref(false)
const initialKind: Schema['AgentSecretOut']['kind'] = 'api_token'
const form = reactive({ name: '', kind: initialKind, description: '', value: '' })
const SECRET_NAME = /^[A-Za-z0-9][A-Za-z0-9 ._-]*$/
const valid = computed(() => SECRET_NAME.test(form.name.trim()) && form.value.length > 0)
function openCreate() { Object.assign(form, { name: '', kind: initialKind, description: '', value: '' }); error.value = ''; notice.value = ''; creating.value = true }
function closeCreate() { creating.value = false; form.value = '' }
async function create() {
  if (!valid.value || busy.value) return
  const body = { name: form.name.trim(), kind: form.kind, description: form.description.trim(), value: form.value }
  form.value = ''
  let id = ''
  const ok = await act(async () => { id = dataOf(await auth.api.POST('/api/v1/agents/secrets', { params: { header }, body })).id }, '密钥已保存')
  if (!ok) return
  creating.value = false
  if (id) update({ [props.tabParam]: undefined, secret: id }, true)
}
onBeforeUnmount(() => { form.value = '' })
</script>

<template>
  <ManagementPage :title="title" :description="description" :embedded="embedded">
    <template #actions>
      <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh"><RefreshCw aria-hidden="true" />刷新</button>
      <button type="button" class="primary-button" :disabled="busy" @click="openCreate"><Plus aria-hidden="true" />添加密钥</button>
    </template>

    <div class="asv-layout sk-layout" :class="{ 'has-side': !!selected && wide }">
      <div class="asv-main">
        <p class="sk-banner"><Info aria-hidden="true" />密钥保存后不可回显，支持轮换为新值。</p>
        <ActionFeedback :error="creating ? '' : error" :notice="notice" />
        <div class="sk-tabs" role="tablist" :aria-label="title">
          <button v-for="t in TABS" :key="t.key" type="button" role="tab" :aria-selected="tab === t.key" @click="update({ [tabParam]: t.key === 'list' ? undefined : t.key })">{{ t.name }}</button>
        </div>
        <div role="tabpanel" :aria-label="TABS.find(t => t.key === tab)?.name">
          <ManagementSection v-if="tab === 'list'" title="密钥列表" description="名称、版本与授权可查看，密钥原文不可读取。">
            <template #title>密钥列表<template v-if="typeof total === 'number'"> · {{ total }}</template></template>
            <div class="ws-toolbar">
              <label class="ws-search"><Search aria-hidden="true" /><span class="sr-only">按名称或说明搜索</span><input v-model="search" type="search" placeholder="名称或说明" maxlength="100" /></label>
              <select v-model="kind" aria-label="按类型筛选"><option value="">全部类型</option><option v-for="(label, value) in VAULT_KIND" :key="value" :value="value">{{ label }}</option></select>
            </div>
            <p v-if="secrets.isPending.value" role="status" class="ws-state-line">正在加载密钥…</p>
            <div v-else-if="secrets.error.value && !items.length" class="ws-state-line"><p role="alert">{{ secretError(secrets.error.value) }}</p><button type="button" class="secondary-button" @click="secrets.refetch()">重试</button></div>
            <p v-else-if="!items.length" class="ws-state-line">{{ filtered ? '没有符合条件的密钥。' : '还没有密钥。点右上角“添加密钥”保存第一个。' }}</p>
            <SecretCatalogTable v-else :items="items" :selected="selected" @select="select" />
            <footer v-if="items.length" class="asv-foot ag-foot">
              <span>已显示 {{ items.length }} 个<template v-if="typeof total === 'number'">，共 {{ total }} 个</template><template v-if="filtered">（符合当前条件）</template><template v-if="secrets.error.value"> · 刷新失败，显示的是上次取回的数据</template></span>
              <button v-if="secrets.hasNextPage.value" type="button" class="secondary-button" :disabled="secrets.isFetchingNextPage.value" @click="secrets.fetchNextPage()">加载更多密钥</button>
            </footer>
          </ManagementSection>
          <SecretAccessLog v-else :secret="accessSecret" @clear-secret="accessSecret = null" />
        </div>
      </div>
      <div v-if="selected && wide" class="ws-panel asv-side sk-side">
        <SecretDetailPanel :key="selected" :secret-id="selected" aside @close="close" @deleted="deleted" @accesses="showAccesses" />
      </div>
    </div>
    <DrawerDetail v-if="selected && !wide" title="密钥详情" @close="close">
      <SecretDetailPanel :key="selected" :secret-id="selected" @close="close" @deleted="deleted" @accesses="showAccesses" />
    </DrawerDetail>

    <ModalPanel v-if="creating" title="添加密钥" :busy="busy" @close="closeCreate">
      <form class="im-group-form" autocomplete="off" @submit.prevent="create">
        <label class="form-field"><span>名称 *（字母、数字、空格、点、下划线、连字符）</span><input v-model="form.name" required maxlength="64" autofocus /></label>
        <label class="form-field"><span>类型（创建后不能改）</span><select v-model="form.kind"><option v-for="(label, value) in VAULT_KIND" :key="value" :value="value">{{ label }}</option></select></label>
        <p class="form-help">模型服务的 API Key 请在中转站管理里“添加连接”保存，这里只放其他凭据。</p>
        <label class="form-field"><span>说明</span><textarea v-model="form.description" rows="2" maxlength="500" /></label>
        <label class="form-field"><span>值 *</span><input v-model="form.value" type="password" required autocomplete="new-password" spellcheck="false" /></label>
        <p class="form-help">保存后页面上不再显示这个值，只能轮换为新值。授权在智能体详情里设置。</p>
        <p v-if="error" role="alert">{{ error }}</p>
        <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy || !valid">保存</button><button type="button" class="secondary-button" :disabled="busy" @click="closeCreate">取消</button></div>
      </form>
    </ModalPanel>
  </ManagementPage>
</template>
