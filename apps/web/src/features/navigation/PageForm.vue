<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, errorMessage } from '../../services/api'
import { safeUrl } from '../../widgets/registry'
import { KIND_LABELS, type CustomPage, type Role } from '../../app/sidebar'
import ModalPanel from '../../components/ModalPanel.vue'
import NotePicker from './NotePicker.vue'
import { newId } from './ids'

// 添加 / 编辑一个零代码页面（T-0161）。只改草稿，点设置页底部的“保存侧栏布局”才提交。
// immediate（应用中心，T-0211）：确定后由调用方立即整份保存；busy / failure / problems 是保存的状态与出错说明。
// 校验与后端一致：外部链接、嵌入页只能是 http(s)；嵌入页的来源必须在部署设置 EMBED_ORIGINS 里；站内路径以一个 / 开头。
const props = defineProps<{ page?: CustomPage; groups: { key: string; title: string }[]; embedOrigins: string[]; immediate?: boolean; busy?: boolean; failure?: string; problems?: string[] }>()
const emit = defineEmits<{ close: []; save: [page: CustomPage] }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
type Kind = CustomPage['kind']
const ROLE_LABELS: Record<Role, string> = { viewer: '所有成员（含只读）', member: '成员及以上', admin: 'owner 与 admin', owner: '只有 owner' }
const PATH = /^\/(?!\/)[A-Za-z0-9\-._~/%?=&#:@+]*$/
const form = reactive({
  title: props.page?.title ?? '', kind: (props.page?.kind ?? 'link') as Kind, section: props.page?.section ?? 'apps', min_role: (props.page?.min_role ?? 'viewer') as Role,
  url: props.page?.url ?? '', path: props.page?.path ?? '', target_id: props.page?.target_id ?? null as string | null,
})
const error = ref('')
// 仪表盘只在选“仪表盘”类型时才读
const dashboards = useQuery({
  queryKey: ['dashboards', user, scope], enabled: computed(() => form.kind === 'dashboard'),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/dashboards', { params: { header: { 'X-Workspace-Id': scope } }, signal })),
})
function changeKind() { form.target_id = null }
const originOf = (url: string) => { try { return new URL(url).origin } catch { return '' } }

function check(): string {
  const title = form.title.trim()
  if (!title || title.length > 20) return '名称要 1–20 个字。'
  if (form.kind === 'link' && !safeUrl(form.url.trim())) return '外部链接要以 http:// 或 https:// 开头，不能带账号密码。'
  if (form.kind === 'page' && !PATH.test(form.path.trim())) return '站内路径要以一个 / 开头，例如 /projects；中文等字符请先编码。'
  if ((form.kind === 'note' || form.kind === 'dashboard') && !form.target_id) return form.kind === 'note' ? '请选一篇笔记。' : '请选一个仪表盘。'
  if (form.kind === 'embed') {
    if (!props.embedOrigins.length) return '这个部署还不允许嵌入任何网站，见下面的说明。'
    if (!safeUrl(form.url.trim()) || !props.embedOrigins.includes(originOf(form.url.trim()))) return '嵌入页的地址必须来自允许嵌入的来源。'
  }
  return ''
}
function submit() {
  if (props.busy) return
  error.value = check()
  if (error.value) return
  const kind = form.kind
  emit('save', {
    id: props.page?.id ?? newId(), title: form.title.trim(), section: form.section, kind, min_role: form.min_role,
    url: kind === 'link' || kind === 'embed' ? form.url.trim() : null,
    path: kind === 'page' ? form.path.trim() : null,
    target_id: kind === 'note' || kind === 'dashboard' ? form.target_id : null,
  })
}
</script>

<template>
  <ModalPanel :title="page ? (immediate ? '编辑入口' : '编辑页面') : (immediate ? '添加入口' : '添加页面')" :busy="busy" @close="$emit('close')">
    <form class="page-form" @submit.prevent="submit">
      <label class="form-field"><span>名称</span><input v-model="form.title" maxlength="20" autocomplete="off" autofocus></label>
      <label class="form-field"><span>类型</span><select v-model="form.kind" @change="changeKind"><option v-for="(label, kind) in KIND_LABELS" :key="kind" :value="kind">{{ label }}</option></select></label>
      <label class="form-field"><span>分组</span><select v-model="form.section"><option v-for="group in groups" :key="group.key" :value="group.key">{{ group.title }}</option></select></label>
      <label class="form-field"><span>谁能看到</span><select v-model="form.min_role"><option v-for="(label, role) in ROLE_LABELS" :key="role" :value="role">{{ label }}</option></select></label>
      <label v-if="form.kind === 'link'" class="form-field"><span>链接地址</span><input v-model="form.url" type="url" maxlength="2048" placeholder="https://example.com" autocomplete="off"></label>
      <template v-else-if="form.kind === 'page'">
        <label class="form-field"><span>站内路径</span><input v-model="form.path" maxlength="512" placeholder="/projects" autocomplete="off"></label>
        <p class="form-help">工作台里的任意地址，例如某个项目页 /projects/…、任务时间轴 /tasks/timeline。</p>
      </template>
      <NotePicker v-else-if="form.kind === 'note'" v-model="form.target_id" />
      <template v-else-if="form.kind === 'dashboard'">
        <p v-if="dashboards.isPending.value" role="status">正在加载仪表盘…</p>
        <p v-else-if="dashboards.error.value" role="alert">{{ errorMessage(dashboards.error.value) }}</p>
        <label v-else class="form-field"><span>仪表盘</span><select v-model="form.target_id"><option :value="null" disabled>请选择</option><option v-for="item in dashboards.data.value?.items ?? []" :key="item.id" :value="item.id">{{ item.title }}{{ item.is_default ? '（首页）' : '' }}</option></select></label>
      </template>
      <template v-else-if="form.kind === 'embed'">
        <label class="form-field"><span>嵌入地址</span><input v-model="form.url" type="url" maxlength="2048" placeholder="https://grafana.example.com/d/…" autocomplete="off"></label>
        <div v-if="embedOrigins.length" class="form-help">允许嵌入的来源：<ul class="page-form-origins" aria-label="允许嵌入的来源"><li v-for="origin in embedOrigins" :key="origin"><code>{{ origin }}</code></li></ul>页面会放在带沙箱的框里显示，不带来源信息。</div>
        <p v-else class="agent-notice" role="note">这个部署还不允许嵌入任何网站：要在服务器的 .env 里设置 EMBED_ORIGINS（例如 https://grafana.example.com，多个用空格分隔），再重启 api 和 caddy，见部署指南（docs/deployment.md）。</p>
      </template>
      <p v-if="error || failure" role="alert">{{ error || failure }}<template v-if="!error && problems?.length"><br><template v-for="item in problems" :key="item">{{ item }}<br></template></template></p>
      <p v-if="immediate" class="form-help">确定后立即保存到侧栏：只改这一项，其余布局原样保留；别人先改过会提示你确认后再保存。“谁能看到”只决定入口是否显示，打开后仍按原来的权限检查。</p>
      <p v-else class="form-help">确定后点页面底部的“保存侧栏布局”才会生效。</p>
      <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy">{{ busy ? '正在保存…' : immediate ? '保存入口' : page ? '更新页面' : '加入页面' }}</button><button type="button" class="secondary-button" :disabled="busy" @click="$emit('close')">取消</button></div>
    </form>
  </ModalPanel>
</template>

<style scoped>
.page-form .form-field { margin: 12px 0; }
.page-form-origins { margin: 4px 0; padding-left: 18px; overflow-wrap: anywhere; }
</style>
