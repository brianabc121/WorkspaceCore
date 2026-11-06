<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { NETWORK, ROLE, SCOPE, STATUS } from './queries'
import SshDeploy from './SshDeploy.vue'
import HostStatus from './HostStatus.vue'
import OpsPanel from './OpsPanel.vue'
import OutboxPanel from './OutboxPanel.vue'
import WakeupPanel from './WakeupPanel.vue'
import StopPanel from './StopPanel.vue'

const props = defineProps<{ instance: Schema['AgentInstanceOut']; secrets: Schema['AgentSecretOut'][]; busy: boolean; host?: Schema['HostSummaryItem']; names: Map<string, string> }>()
const emit = defineEmits<{ edit: []; toggle: []; bundle: [previous: Schema['BundleRequest']['previous']]; revoke: [credential: Schema['AgentCredentialOut']]; grants: [grants: Schema['SecretGrantIn'][]]; projectAdmin: [enabled: boolean]; imMedia: [enabled: boolean]; stopOthers: [enabled: boolean] }>()
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
      <div v-if="instance.im_token_expires_at"><dt>IM 令牌</dt><dd>{{ formatTime(instance.im_token_expires_at) }} 到期（OpenClaw 每次启动时领取新的）</dd></div>
      <div><dt>接入包</dt><dd>{{ instance.bundle_issued_at ? `最近生成于 ${formatTime(instance.bundle_issued_at)}` : '还没有生成' }}</dd></div>
    </dl>
    <p v-if="instance.bundle_outdated" class="form-help agent-notice" role="note">这个实例还在用旧的接入包，或者插件没有打上工作台的补丁：重新生成接入包部署（或用下面的 SSH 自动部署重新部署）后，它的自动回复被拒收时才会收到提醒，主动发消息也只走 im_send。</p>
    <HostStatus :instance="instance" :host="host" />
    <section class="agent-section" aria-label="项目管理权限">
      <div class="section-heading">
        <h3>项目管理权限：{{ instance.project_admin.enabled ? '已打开' : '关闭' }}</h3>
        <button type="button" class="secondary-button" :disabled="busy || (disabled && !instance.project_admin.enabled)" @click="$emit('projectAdmin', !instance.project_admin.enabled)">{{ instance.project_admin.enabled ? '关闭项目管理权限' : '允许它新建项目、拉智能体进项目' }}</button>
      </div>
      <p class="form-help">打开后它可以用 create_project 新建项目、用 add_to_project 把其他智能体拉进它建的项目，直接生效、不审批，每次都会通知管理员；它建的项目群的群主是打开权限的人。关闭后它已经建的项目和成员保留。<template v-if="instance.project_admin.enabled && instance.project_admin.granted_at"> 打开于 {{ formatTime(instance.project_admin.granted_at) }}。</template></p>
    </section>
    <section class="agent-section" aria-label="附件权限">
      <div class="section-heading">
        <h3>附件权限：{{ instance.im_media.enabled ? '已打开' : '关闭' }}</h3>
        <button type="button" class="secondary-button" :disabled="busy || (disabled && !instance.im_media.enabled)" @click="$emit('imMedia', !instance.im_media.enabled)">{{ instance.im_media.enabled ? '关闭附件权限' : '允许它发图片、文件、视频' }}</button>
      </div>
      <p class="form-help">打开后它可以用 im_send 发图片（10 MB）、文件（20 MB）、视频（MP4 / MOV，50 MB）：工作台识别类型、挡住可执行与网页类文件、检查密钥，每小时最多 30 个、每天 200 MB。文字不受影响。<template v-if="instance.im_media.enabled && instance.im_media.granted_at"> 打开于 {{ formatTime(instance.im_media.granted_at) }}。</template></p>
    </section>
    <section class="agent-section" aria-label="终止其他智能体任务的权限">
      <div class="section-heading">
        <h3>终止其他智能体任务的权限：{{ instance.stop_others.enabled ? '已打开' : '关闭' }}</h3>
        <button type="button" class="secondary-button" :disabled="busy || (disabled && !instance.stop_others.enabled)" @click="$emit('stopOthers', !instance.stop_others.enabled)">{{ instance.stop_others.enabled ? '关闭这个权限' : '允许它叫停别的智能体' }}</button>
      </div>
      <p class="form-help">打开后它可以用 list_agent_runs 查看本工作空间的智能体在做什么、用 stop_agent_run 叫停别的智能体正在执行的任务（适合做协调的智能体），直接生效、不审批；不能叫停自己，每小时最多 10 次，每次都通知管理员。<template v-if="instance.stop_others.enabled && instance.stop_others.granted_at"> 打开于 {{ formatTime(instance.stop_others.granted_at) }}。</template></p>
    </section>
    <SshDeploy :instance="instance" />
    <OpsPanel :instance="instance" />
    <OutboxPanel :instance="instance" />
    <WakeupPanel :instance="instance" />
    <StopPanel :instance="instance" :names="names" />
    <section class="agent-section" aria-label="接入包">
      <h3>接入包</h3>
      <p class="form-help">每次生成都会签发新凭证，凭证只出现在这一次下载的 zip 里。{{ instance.network_mode === 'public' ? '外网模式同时签发 90 天的客户端证书；用上面的 SSH 自动部署时会自动生成，不用下载。' : '' }}</p>
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
