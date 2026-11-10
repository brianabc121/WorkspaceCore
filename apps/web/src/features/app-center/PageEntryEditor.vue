<script setup lang="ts">
import { computed, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { useNavigationStore } from '../../stores/navigation'
import { errorMessage } from '../../services/api'
import { allSections, type CustomPage, type NavigationLayout } from '../../app/sidebar'
import ModalPanel from '../../components/ModalPanel.vue'
import PageForm from '../navigation/PageForm.vue'
import { applyPageChange, pageProblems, savePageChange, type PageChange } from '../navigation/layout'

// 应用中心里添加 / 编辑 / 移除一个零代码入口（T-0211，方案第 3.3 节）：确定后立即保存一次完整布局（带读到的 version）。
// 打开时重新读取当前角色的完整布局作为快照（不沿用可能已过期或按更低角色读到的缓存）。
// 409：保留草稿，读取最新布局，由用户确认后把同一个修改套到最新布局上再保存，绝不静默覆盖。
// 移除入口只改侧栏，不删除它指向的笔记、仪表盘或外部网站。
const props = defineProps<{ target: CustomPage | 'new' | null; removing: CustomPage | null }>()
const emit = defineEmits<{ close: []; done: [message: string] }>()
const auth = useAuthStore(), store = useNavigationStore(), scope = auth.workspaceId!
const base = ref<NavigationLayout | null>(store.layout)
const loading = ref(true), busy = ref(false), failure = ref(''), problems = ref<string[]>([])
const conflict = ref<PageChange | null>(null)
void store.refresh().then(value => { if (value) base.value = value; loading.value = false })

const groups = computed(() => allSections(base.value).map(section => ({ key: section.key, title: section.title.trim() || '（未命名）' })))
const editing = computed(() => (props.target && props.target !== 'new' ? props.target : undefined))

async function save(change: PageChange) {
  if (!base.value || busy.value) return
  busy.value = true; failure.value = ''; problems.value = []
  try {
    const result = await savePageChange(auth.api, scope, base.value, change)
    if (result.ok) {
      store.apply(result.layout)
      emit('done', change.kind === 'remove' ? '入口已从侧栏移除（指向的内容没有删除）。' : '入口已保存。')
      return
    }
    if (result.conflict) {
      const latest = await store.refresh()
      if (latest) base.value = latest
      conflict.value = change
      return
    }
    problems.value = pageProblems(result.error, applyPageChange(base.value, change), change.kind === 'upsert' ? change.page.id : change.id)
    failure.value = problems.value.length ? '有内容不符合要求：' : errorMessage(result.error)
  } finally { busy.value = false }
}
function merge() {
  const change = conflict.value
  conflict.value = null
  if (change) void save(change)
}
</script>

<template>
  <ModalPanel v-if="conflict" title="侧栏布局已被别人修改" :busy="busy" @close="conflict = null">
    <div class="apc-dialog">
      <p>在你编辑期间，侧栏布局被别人（或另一个标签页）保存过。已读取最新布局，你的修改还在，没有提交。</p>
      <p>确认后只把这一项{{ conflict.kind === 'remove' ? '移除' : '修改' }}合并到最新布局再保存，其余内容按最新布局保留。</p>
      <div class="form-actions"><button type="button" class="primary-button" :disabled="busy" @click="merge">合并后保存</button><button type="button" class="secondary-button" :disabled="busy" @click="conflict = null">返回</button></div>
    </div>
  </ModalPanel>
  <ModalPanel v-else-if="removing" title="移除入口" :busy="busy" @close="emit('close')">
    <div class="apc-dialog">
      <p>从侧栏移除「{{ removing.title }}」？只移除这个入口，不会删除它指向的笔记、仪表盘或外部网站。</p>
      <p v-if="failure" class="feedback" role="alert">{{ failure }}</p>
      <div class="form-actions"><button type="button" class="primary-button danger-button" :disabled="busy || loading" @click="save({ kind: 'remove', id: removing.id })">{{ busy ? '正在移除…' : '移除入口' }}</button><button type="button" class="secondary-button" :disabled="busy" @click="emit('close')">取消</button></div>
    </div>
  </ModalPanel>
  <PageForm v-else-if="target" :page="editing" :groups="groups" :embed-origins="base?.embed_origins ?? []" immediate :busy="busy || loading" :failure="failure" :problems="problems" @close="emit('close')" @save="page => save({ kind: 'upsert', page })" />
</template>
