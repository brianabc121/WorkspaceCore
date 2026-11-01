<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { NETWORK, ROLE, SCOPE, STATUS } from './queries'

const props = defineProps<{ instance: Schema['AgentInstanceOut']; secrets: Schema['AgentSecretOut'][]; busy: boolean }>()
const emit = defineEmits<{ edit: []; toggle: []; bundle: [previous: Schema['BundleRequest']['previous']]; revoke: [credential: Schema['AgentCredentialOut']]; grants: [grants: Schema['SecretGrantIn'][]] }>()
const previous = ref<'keep_24h' | 'revoke_now'>('keep_24h'), editingGrants = ref(false)
const rows = ref<{ secret_id: string; ref_id: string }[]>([])
const active = computed(() => props.instance.credentials.filter(c => !c.revoked_at))
const disabled = computed(() => props.instance.status === 'disabled')
const validRef = (ref: string) => /^[a-z][a-z0-9_]{0,63}$/.test(ref)
const grantsValid = computed(() => rows.value.every(r => r.secret_id && validRef(r.ref_id)) && new Set(rows.value.map(r => r.ref_id)).size === rows.value.length)
function editGrants() { rows.value = props.instance.secret_grants.map(g => ({ secret_id: g.secret_id, ref_id: g.ref_id })); editingGrants.value = true }
function saveGrants() { if (grantsValid.value) { emit('grants', rows.value.map(r => ({ ...r }))); editingGrants.value = false } }
</script>

<template>
  <article class="panel agent-card" :aria-label="`实例：${instance.name}`">
    <div class="section-heading">
      <h2>{{ instance.name }} <span class="badge">{{ STATUS[instance.status] }}</span> <span class="badge">{{ NETWORK[instance.network_mode] }}</span><span v-if="!instance.im_ready" class="badge">IM 准备中</span></h2>
      <div class="form-actions"><button type="button" class="secondary-button" :disabled="busy" @click="$emit('edit')">编辑</button><button type="button" class="secondary-button" :disabled="busy" @click="$emit('toggle')">{{ disabled ? '启用' : '停用' }}</button></div>
    </div>
    <p v-if="instance.description" class="preserve-text">{{ instance.description }}</p>
    <dl class="agent-facts">
      <div><dt>工具权限</dt><dd>{{ SCOPE[instance.scope] }}</dd></div>
      <div><dt>最高角色</dt><dd>{{ ROLE[instance.max_role] }}</dd></div>
      <div><dt>IM 账号</dt><dd>{{ instance.im_nickname }}（{{ instance.im_user_id }}）</dd></div>
      <div><dt>最近在线</dt><dd>{{ instance.last_seen_at ? formatTime(instance.last_seen_at) : '从未连接' }}</dd></div>
      <div><dt>上报版本</dt><dd>{{ instance.reported_version ?? '—' }} / 插件 {{ instance.plugin_version ?? '—' }}</dd></div>
      <div><dt>接入包</dt><dd>{{ instance.bundle_issued_at ? `最近生成于 ${formatTime(instance.bundle_issued_at)}` : '还没有生成' }}</dd></div>
    </dl>
    <section class="agent-section" aria-label="接入包">
      <h3>接入包</h3>
      <p class="form-help">每次生成都会签发新凭证，凭证只出现在这一次下载的 zip 里。{{ instance.network_mode === 'public' ? '外网模式同时签发 90 天的客户端证书。' : '' }}</p>
      <div class="im-group-add">
        <label class="form-field"><span>旧凭证</span><select v-model="previous"><option value="keep_24h">再保留 24 小时</option><option value="revoke_now">立即吊销</option></select></label>
        <button type="button" class="primary-button" :disabled="busy || disabled" @click="$emit('bundle', previous)">生成并下载接入包</button>
      </div>
    </section>
    <section class="agent-section" aria-label="凭证">
      <h3>有效凭证（{{ active.length }}）</h3>
      <p v-if="!active.length" class="empty-state">没有有效凭证。生成接入包后出现。</p>
      <ul class="im-contact-list">
        <li v-for="c in active" :key="c.id">
          <span>{{ c.kind === 'token' ? `令牌 ${c.token_prefix ?? ''}…` : `客户端证书 ${c.cert_fingerprint?.slice(0, 16) ?? ''}…` }} · {{ NETWORK[c.ingress] }}<template v-if="c.expires_at"> · {{ formatTime(c.expires_at) }} 到期</template><template v-if="c.last_used_at"> · 最近使用 {{ formatTime(c.last_used_at) }}</template></span>
          <button type="button" class="secondary-button" :disabled="busy" @click="$emit('revoke', c)">立即吊销</button>
        </li>
      </ul>
    </section>
    <section class="agent-section" aria-label="密钥授权">
      <div class="section-heading"><h3>密钥授权（{{ instance.secret_grants.length }}）</h3><button v-if="!editingGrants" type="button" class="secondary-button" :disabled="busy" @click="editGrants">编辑授权</button></div>
      <ul v-if="!editingGrants" class="im-contact-list"><li v-for="g in instance.secret_grants" :key="g.ref_id"><span><code>{{ g.ref_id }}</code> → {{ g.secret_name }}</span></li></ul>
      <form v-else class="im-group-form" @submit.prevent="saveGrants">
        <p class="form-help">ref_id 是 OpenClaw 配置里 SecretRef 的 id（小写字母开头，只含小写字母、数字、下划线），例如 openai_api_key。</p>
        <div v-for="(row, index) in rows" :key="index" class="im-group-add">
          <label class="form-field"><span>ref_id</span><input v-model.trim="row.ref_id" maxlength="64" :aria-invalid="!validRef(row.ref_id)" /></label>
          <label class="form-field"><span>密钥</span><select v-model="row.secret_id"><option value="">选择密钥</option><option v-for="s in secrets" :key="s.id" :value="s.id">{{ s.name }}</option></select></label>
          <button type="button" class="secondary-button" @click="rows.splice(index, 1)">删除这一行</button>
        </div>
        <div class="form-actions"><button type="button" class="secondary-button" :disabled="!secrets.length" @click="rows.push({ secret_id: '', ref_id: '' })">添加授权</button><button type="submit" class="primary-button" :disabled="busy || !grantsValid">保存授权</button><button type="button" class="secondary-button" @click="editingGrants = false">取消</button></div>
        <p v-if="!secrets.length" class="feedback">密钥库里还没有密钥，先到“密钥库”页添加。</p>
      </form>
    </section>
  </article>
</template>
