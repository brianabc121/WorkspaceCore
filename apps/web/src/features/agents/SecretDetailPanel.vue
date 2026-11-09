<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref } from 'vue'
import { ArrowRight, Bot, ChevronDown, FileText, Info, Layers, Lock, Pencil, RotateCw, Trash2, X } from '@lucide/vue'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { useAuthStore } from '../../stores/auth'
import { formatTime } from '../time'
import { SECRET_KIND, useSecret, useSecretAccesses } from './queries'
import { useAgentAction } from './action'
import { grantedInstances, isConflict, isGone, secretError } from './secrets'
import SecretAccessTable from './SecretAccessTable.vue'

// 密钥详情（T-0196，效果图 07 右侧一栏；1440 以下放进右侧抽屉）：按完整 ID 读 GET /agents/secrets/{id}，
// 所以不在当前页、当前筛选里的密钥也能打开；不存在或已无权访问时不留旧内容。只有元数据：值不回显、不能复制、没有有效期。
// 轮换与编辑各自是草稿：打开表单时记下记录的 version（不是值版本），后台刷新不改草稿；409 说明没保存并重新读取。
// 新值只在输入框里，提交、取消、关闭、换密钥都清空，不进地址、缓存或浏览器存储。
// 授权在智能体详情里改（PUT 整个实例的授权），这里只链接过去，不从这里重写别的密钥的授权。
const props = defineProps<{ secretId: string; aside?: boolean }>()
const emit = defineEmits<{ close: []; deleted: [name: string]; accesses: [secret: { id: string; name: string }] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const detail = useSecret(scope, () => props.secretId)
const gone = computed(() => isGone(detail.error.value))
const secret = computed(() => (gone.value || detail.data.value?.id !== props.secretId ? null : detail.data.value))
const instances = computed(() => (secret.value ? grantedInstances(secret.value) : []))
// 详情里只读最近 10 条，标明是“已加载”的，不冒充全部历史
const RECENT = 10
const accesses = useSecretAccesses(scope, () => ({ secret_id: props.secretId, limit: RECENT }))
const recent = computed(() => accesses.data.value?.pages[0]?.items ?? [])
const hasOlder = computed(() => !!accesses.data.value?.pages[0]?.next_cursor)
const { busy, error, notice, failure, act, refresh, header } = useAgentAction(scope, secretError)

const mode = ref<'rotate' | 'edit' | null>(null)
const value = ref('')
const draft = reactive({ name: '', description: '' })
let base = 0
const SECRET_NAME = /^[A-Za-z0-9][A-Za-z0-9 ._-]*$/
const editValid = computed(() => SECRET_NAME.test(draft.name.trim()))
const formRef = ref<HTMLFormElement | null>(null)

async function open(next: 'rotate' | 'edit') {
  const s = secret.value
  if (!s) return
  value.value = ''; error.value = ''; notice.value = ''
  base = s.version
  if (next === 'edit') Object.assign(draft, { name: s.name, description: s.description })
  mode.value = next
  await nextTick()
  formRef.value?.querySelector<HTMLElement>('input')?.focus()
}
function cancel() { mode.value = null; value.value = '' }
onBeforeUnmount(() => { value.value = '' })

async function rotate() {
  const s = secret.value
  if (!s || mode.value !== 'rotate' || !value.value || busy.value) return
  const body = { value: value.value, version: base }
  value.value = ''
  const ok = await act(() => auth.api.POST('/api/v1/agents/secrets/{secret_id}/rotate', { params: { header, path: { secret_id: s.id } }, body }), '密钥已轮换：实例下次取用将获得新版本。')
  mode.value = null
  if (ok) return
  if (isConflict(failure.value)) error.value = '密钥已被其他操作修改（可能刚被轮换、改名），这次没有轮换。已重新读取最新信息，请核对后重新轮换。'
  void refresh()
}
async function save() {
  const s = secret.value
  if (!s || mode.value !== 'edit' || !editValid.value || busy.value) return
  const ok = await act(() => auth.api.PATCH('/api/v1/agents/secrets/{secret_id}', { params: { header, path: { secret_id: s.id } }, body: { name: draft.name.trim(), description: draft.description.trim(), version: base } }), '密钥信息已保存。')
  if (ok) { mode.value = null; return }
  if (!isConflict(failure.value)) return
  // 冲突：草稿留着，读回最新的名称与说明给用户核对；再点保存才按新读到的版本提交
  error.value = '密钥已被其他操作修改，这次没有保存。上方已是最新的名称与说明，核对后可以再次保存你的修改。'
  await detail.refetch()
  if (secret.value) base = secret.value.version
}
async function remove() {
  const s = secret.value
  if (!s || busy.value) return
  const used = instances.value.length
    ? `它授权给了 ${s.grants.map(g => `${g.instance_name}（引用名 ${g.ref_id}）`).join('、')}，删除后这些智能体取不到它。`
    : '它没有授权给任何智能体。'
  if (!window.confirm(`删除密钥「${s.name}」？${used}删除后不能恢复。`)) return
  mode.value = null; value.value = ''
  const ok = await act(() => auth.api.DELETE('/api/v1/agents/secrets/{secret_id}', { params: { header, path: { secret_id: s.id }, query: { version: s.version } } }))
  if (ok) emit('deleted', s.name)
  else void refresh()
}
const instanceLink = computed(() => (instances.value.length === 1 ? { path: '/agents', query: { instance: instances.value[0]!.id } } : '/agents'))
</script>

<template>
  <section class="sk-detail" :class="{ 'is-aside': aside }" aria-label="密钥详情">
    <div v-if="aside" class="sk-detail-bar">
      <span class="sk-eyebrow">密钥详情</span>
      <button type="button" class="icon-button" aria-label="关闭密钥详情" @click="emit('close')"><X aria-hidden="true" /></button>
    </div>

    <p v-if="detail.isPending.value" role="status" class="ws-state-line">正在加载密钥…</p>
    <div v-else-if="gone" class="ws-state-line"><p role="alert">这个密钥不存在，或你已无权访问。</p><button type="button" class="secondary-button" @click="emit('close')">关闭</button></div>
    <div v-else-if="detail.error.value && !secret" class="ws-state-line"><p role="alert">{{ secretError(detail.error.value) }}</p><button type="button" class="secondary-button" @click="detail.refetch()">重试</button></div>
    <template v-else-if="secret">
      <div class="sk-identity">
        <h2 class="sk-name">{{ secret.name }}</h2>
        <span class="mg-tag"><Layers aria-hidden="true" />{{ SECRET_KIND[secret.kind] ?? secret.kind }} · v{{ secret.value_version }}</span>
        <p class="sk-desc"><FileText aria-hidden="true" /><span v-if="secret.description" class="preserve-text">{{ secret.description }}</span><span v-else class="asv-sub">没有说明</span></p>
        <p class="asv-sub">最近轮换 <time :datetime="secret.last_rotated_at">{{ formatTime(secret.last_rotated_at) }}</time> · 创建于 <time :datetime="secret.created_at">{{ formatTime(secret.created_at) }}</time></p>
      </div>

      <div class="sk-block">
        <h3>密钥内容</h3>
        <p class="sk-value"><Lock aria-hidden="true" />不支持回显</p>
      </div>

      <div class="sk-actions">
        <button type="button" class="primary-button" :disabled="busy" :aria-expanded="mode === 'rotate'" @click="mode === 'rotate' ? cancel() : open('rotate')"><RotateCw aria-hidden="true" />轮换密钥</button>
        <button type="button" class="secondary-button" :disabled="busy" :aria-expanded="mode === 'edit'" @click="mode === 'edit' ? cancel() : open('edit')"><Pencil aria-hidden="true" />编辑信息</button>
        <DropdownMenu>
          <DropdownMenuTrigger class="secondary-button" :disabled="busy" aria-label="更多操作">更多<ChevronDown aria-hidden="true" /></DropdownMenuTrigger>
          <DropdownMenuContent align="end" :collision-padding="8">
            <DropdownMenuItem variant="destructive" @select="remove"><Trash2 aria-hidden="true" />删除密钥</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <form v-if="mode === 'rotate'" ref="formRef" class="sk-form" aria-label="轮换密钥" autocomplete="off" @submit.prevent="rotate">
        <label class="form-field"><span>新值</span><input v-model="value" type="password" autocomplete="new-password" spellcheck="false" required /></label>
        <p class="form-help">提交后旧值不再下发；新值只在这次提交里发送，页面不保存、不显示。</p>
        <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy || !value">确认轮换</button><button type="button" class="secondary-button" :disabled="busy" @click="cancel">取消</button></div>
      </form>
      <form v-else-if="mode === 'edit'" ref="formRef" class="sk-form" aria-label="编辑密钥信息" autocomplete="off" @submit.prevent="save">
        <label class="form-field"><span>名称</span><input v-model="draft.name" required maxlength="64" :aria-invalid="!editValid" /></label>
        <p class="form-help">字母、数字、空格、点、下划线、连字符；类型创建后不能改。</p>
        <label class="form-field"><span>说明</span><textarea v-model="draft.description" rows="2" maxlength="500" /></label>
        <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy || !editValid">保存</button><button type="button" class="secondary-button" :disabled="busy" @click="cancel">取消</button></div>
      </form>
      <p v-if="error" class="feedback" role="alert">{{ error }}</p>
      <p v-if="notice" class="feedback" role="status">{{ notice }}</p>

      <section class="sk-block" aria-label="授权对象">
        <h3>授权对象</h3>
        <ul v-if="secret.grants.length" class="sk-grants">
          <li v-for="g in secret.grants" :key="g.instance_id + g.ref_id">
            <Bot aria-hidden="true" />
            <RouterLink class="sk-grant-name" :to="{ path: '/agents', query: { instance: g.instance_id } }" :title="`在智能体详情里管理「${g.instance_name}」的密钥授权`">{{ g.instance_name }}</RouterLink>
            <span class="sk-ref">引用名：<code>{{ g.ref_id }}</code></span>
          </li>
        </ul>
        <p v-else class="form-help">还没有授权给任何智能体。</p>
        <RouterLink class="asv-link" :to="instanceLink">前往智能体管理授权<ArrowRight aria-hidden="true" /></RouterLink>
      </section>

      <section class="sk-block sk-recent" aria-label="当前已加载的取用记录">
        <h3>当前已加载的取用记录</h3>
        <p v-if="accesses.isPending.value" role="status" class="form-help">正在加载取用记录…</p>
        <p v-else-if="accesses.error.value" role="alert" class="form-help">{{ secretError(accesses.error.value) }}</p>
        <p v-else-if="!recent.length" class="form-help">还没有智能体取用过这个密钥。</p>
        <template v-else>
          <SecretAccessTable :items="recent" compact />
          <p class="form-help">最近 {{ recent.length }} 条<template v-if="hasOlder">，更早的在“取用记录”里查看</template>。</p>
        </template>
        <button type="button" class="asv-link" @click="emit('accesses', { id: secret.id, name: secret.name })">查看全部取用记录<ArrowRight aria-hidden="true" /></button>
      </section>

      <p class="sk-note"><Info aria-hidden="true" />轮换后实例下次取用将获得新版本。</p>
      <p v-if="detail.error.value" class="form-help">刷新失败，显示的是上次取回的内容。</p>
    </template>
  </section>
</template>
