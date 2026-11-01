<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import ModalPanel from '../../components/ModalPanel.vue'
import { SECRET_KIND, agentError, unique, useInstances, useSecretAccesses, useSecrets } from './queries'
import { useAgentAction } from './action'

// Values are write-only: the page never shows a stored value, only names, versions and who fetched them.
const auth = useAuthStore(), scope = auth.workspaceId!
const secrets = useSecrets(scope), instances = useInstances(scope)
const filter = reactive<{ instance_id: string; result: '' | Schema['SecretAccessOut']['result'] }>({ instance_id: '', result: '' })
const accesses = useSecretAccesses(scope, () => ({ ...(filter.instance_id ? { instance_id: filter.instance_id } : {}), ...(filter.result ? { result: filter.result } : {}) }))
const { busy, error, notice, act, header } = useAgentAction(scope)
const items = computed(() => unique(secrets.data.value?.pages)), instanceItems = computed(() => unique(instances.data.value?.pages))
const accessItems = computed(() => unique(accesses.data.value?.pages))
type Dialog = { mode: 'new' } | { mode: 'edit' | 'rotate'; secret: Schema['AgentSecretOut'] }
const dialog = ref<Dialog | null>(null)
const form = reactive({ name: '', kind: 'model_api_key' as Schema['AgentSecretOut']['kind'], description: '', value: '' })
const SECRET_NAME = /^[A-Za-z0-9][A-Za-z0-9 ._-]*$/
const RESULT = { ok: '成功', not_found: '未授权或不存在', rate_limited: '过于频繁' } as const

function open(next: Dialog) {
  dialog.value = next
  const s = next.mode === 'new' ? null : next.secret
  Object.assign(form, { name: s?.name ?? '', kind: s?.kind ?? 'model_api_key', description: s?.description ?? '', value: '' })
}
async function submit() {
  const d = dialog.value
  if (!d) return
  const ok = await act(() => {
    if (d.mode === 'new') return auth.api.POST('/api/v1/agents/secrets', { params: { header }, body: { name: form.name.trim(), kind: form.kind, description: form.description.trim(), value: form.value } })
    const path = { secret_id: d.secret.id }
    if (d.mode === 'rotate') return auth.api.POST('/api/v1/agents/secrets/{secret_id}/rotate', { params: { header, path }, body: { value: form.value, version: d.secret.version } })
    return auth.api.PATCH('/api/v1/agents/secrets/{secret_id}', { params: { header, path }, body: { name: form.name.trim(), description: form.description.trim(), version: d.secret.version } })
  }, { new: '密钥已保存', edit: '密钥信息已保存', rotate: '密钥已轮换，实例下次取用时拿到新值' }[d.mode])
  form.value = ''
  if (ok) dialog.value = null
}
function remove(secret: Schema['AgentSecretOut']) {
  const used = secret.grants.length ? `它还授权给了 ${secret.grants.map(g => g.instance_name).join('、')}，删除后这些实例取不到它。` : ''
  if (!window.confirm(`删除密钥「${secret.name}」？${used}`)) return
  void act(() => auth.api.DELETE('/api/v1/agents/secrets/{secret_id}', { params: { header, path: { secret_id: secret.id }, query: { version: secret.version } } }), '密钥已删除')
}
const valid = computed(() => {
  const d = dialog.value
  if (!d) return false
  if (d.mode !== 'rotate' && !SECRET_NAME.test(form.name.trim())) return false
  return d.mode === 'edit' || form.value.length > 0
})
</script>

<template>
  <div class="section-heading"><p class="form-help">密钥只写不读：保存后页面上看不到明文，只有名称、版本与取用记录。授权在“OpenClaw 实例”页设置。</p><button type="button" class="primary-button" :disabled="busy" @click="open({ mode: 'new' })">添加密钥</button></div>
  <p v-if="error && !dialog" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
  <p v-if="secrets.isPending.value" role="status">正在加载密钥…</p>
  <p v-else-if="secrets.error.value" role="alert">{{ agentError(secrets.error.value) }}</p>
  <p v-else-if="!items.length" class="empty-state">还没有密钥。</p>
  <div class="agent-list">
    <article v-for="s in items" :key="s.id" class="panel agent-card" :aria-label="`密钥：${s.name}`">
      <div class="section-heading">
        <h2>{{ s.name }} <span class="badge">{{ SECRET_KIND[s.kind] }}</span> <span class="badge">第 {{ s.value_version }} 版</span></h2>
        <div class="form-actions"><button type="button" class="secondary-button" :disabled="busy" @click="open({ mode: 'edit', secret: s })">编辑</button><button type="button" class="secondary-button" :disabled="busy" @click="open({ mode: 'rotate', secret: s })">轮换</button><button type="button" class="secondary-button im-danger" :disabled="busy" @click="remove(s)">删除</button></div>
      </div>
      <p v-if="s.description" class="preserve-text">{{ s.description }}</p>
      <p class="form-help">最近轮换：{{ formatTime(s.last_rotated_at) }}</p>
      <p v-if="s.grants.length">授权给：<span v-for="g in s.grants" :key="g.instance_id + g.ref_id" class="badge">{{ g.instance_name }} · {{ g.ref_id }}</span></p>
      <p v-else class="form-help">还没有授权给任何实例。</p>
    </article>
  </div>
  <button v-if="secrets.hasNextPage.value" type="button" class="secondary-button" @click="secrets.fetchNextPage()">更多密钥</button>
  <section class="panel agent-switch" aria-label="取用记录">
    <h2>取用记录</h2>
    <div class="im-group-add">
      <label class="form-field"><span>实例</span><select v-model="filter.instance_id"><option value="">全部实例</option><option v-for="i in instanceItems" :key="i.id" :value="i.id">{{ i.name }}</option></select></label>
      <label class="form-field"><span>结果</span><select v-model="filter.result"><option value="">全部结果</option><option v-for="(label, value) in RESULT" :key="value" :value="value">{{ label }}</option></select></label>
    </div>
    <p v-if="accesses.isPending.value" role="status">正在加载记录…</p>
    <p v-else-if="accesses.error.value" role="alert">{{ agentError(accesses.error.value) }}</p>
    <p v-else-if="!accessItems.length" class="empty-state">没有取用记录。</p>
    <div v-else class="table-scroll">
      <table class="agent-table">
        <caption class="form-help">实例取密钥的记录（明文不记录）</caption>
        <thead><tr><th scope="col">时间</th><th scope="col">实例</th><th scope="col">ref_id</th><th scope="col">结果</th><th scope="col">版本</th><th scope="col">来源</th></tr></thead>
        <tbody><tr v-for="a in accessItems" :key="a.id"><td>{{ formatTime(a.at) }}</td><td>{{ a.instance_name }}</td><td><code>{{ a.ref_id }}</code></td><td>{{ RESULT[a.result] }}</td><td>{{ a.value_version ?? '—' }}</td><td>{{ a.source_ip ?? '—' }}</td></tr></tbody>
      </table>
    </div>
    <button v-if="accesses.hasNextPage.value" type="button" class="secondary-button" @click="accesses.fetchNextPage()">更多记录</button>
  </section>
  <ModalPanel v-if="dialog" :title="dialog.mode === 'new' ? '添加密钥' : dialog.mode === 'rotate' ? `轮换：${dialog.secret.name}` : `编辑：${dialog.secret.name}`" :busy="busy" @close="dialog = null">
    <form class="im-group-form" autocomplete="off" @submit.prevent="submit">
      <template v-if="dialog.mode !== 'rotate'">
        <label class="form-field"><span>名称 *（字母、数字、空格、点、下划线、连字符）</span><input v-model="form.name" required maxlength="64" autofocus /></label>
        <label v-if="dialog.mode === 'new'" class="form-field"><span>类型</span><select v-model="form.kind"><option v-for="(label, value) in SECRET_KIND" :key="value" :value="value">{{ label }}</option></select></label>
        <label class="form-field"><span>说明</span><textarea v-model="form.description" rows="2" maxlength="500" /></label>
      </template>
      <label v-if="dialog.mode !== 'edit'" class="form-field"><span>{{ dialog.mode === 'rotate' ? '新值 *' : '值 *' }}</span><input v-model="form.value" type="password" required autocomplete="new-password" spellcheck="false" /></label>
      <p v-if="error" role="alert">{{ error }}</p>
      <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy || !valid">保存</button><button type="button" class="secondary-button" :disabled="busy" @click="dialog = null">取消</button></div>
    </form>
  </ModalPanel>
</template>
