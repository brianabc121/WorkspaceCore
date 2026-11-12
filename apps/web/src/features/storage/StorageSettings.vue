<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { Activity, CircleCheck, CircleX, Cloud, HardDrive, Info } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import ManagementPage from '../../components/management/ManagementPage.vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import DataState from '../../components/DataState.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import {
  STORAGE_NAMES, checkText, failedChecks, invalidField, isVersionConflict, ossHost, r2Host, storageError, useStorageSettings,
  type ReplicaName, type StorageCheck, type StorageName, type StorageSettings, type StorageSettingsBody,
} from './api'
import './storage-settings.css'

// 设置 → 图片存储（T-0223）：知识库图片与常驻环境快照存在哪里，每个工作空间单独设置。owner 修改，admin 只读。
// 保存前后端对主存储与副本各写、读、删一个探测对象，不通过就不保存。密钥只写不读：输入的密钥只在这个组件的内存里，
// 提交（不论成功与否）、取消修改后清空；接口只返回“是否已设置”。
const auth = useAuthStore(), scope = auth.workspaceId!
const { query, save } = useStorageSettings(scope)
const canEdit = computed(() => auth.isOwner)
const settings = computed(() => query.data.value)

const blank = () => ({
  primary: 'local' as StorageName, replica: 'none' as ReplicaName,
  r2: { account_id: '', bucket: '', jurisdiction: '', access_key_id: '' },
  oss: { region: '', bucket: '', access_key_id: '' },
})
const form = reactive(blank())
const r2Secret = ref(''), ossSecret = ref('')
// 清空某个云存储的连接信息（保存时密钥传空字符串）；还存着文件或被选中的存储不能清空，后端同样拦
const clearR2 = ref(false), clearOss = ref(false)
const loaded = ref<number | null>(null)
const busy = ref(false), error = ref(''), notice = ref('')
const checks = ref<Record<string, StorageCheck> | null>(null)

function fill(value: StorageSettings) {
  form.primary = value.primary; form.replica = value.replica
  form.r2 = { account_id: value.r2.account_id, bucket: value.r2.bucket, jurisdiction: value.r2.jurisdiction, access_key_id: value.r2.access_key_id }
  form.oss = { region: value.oss.region, bucket: value.oss.bucket, access_key_id: value.oss.access_key_id }
  r2Secret.value = ''; ossSecret.value = ''; clearR2.value = false; clearOss.value = false
  loaded.value = value.version
}
// 只在第一次读到、或版本变了（自己保存后、冲突后重新读）时填表，不覆盖正在填写的内容
watch(settings, value => { if (value && value.version !== loaded.value) fill(value) }, { immediate: true })

const dirty = computed(() => {
  const value = settings.value
  if (!value) return false
  return form.primary !== value.primary || form.replica !== value.replica || clearR2.value || clearOss.value || !!r2Secret.value || !!ossSecret.value
    || (Object.keys(form.r2) as (keyof typeof form.r2)[]).some(k => form.r2[k] !== value.r2[k])
    || (Object.keys(form.oss) as (keyof typeof form.oss)[]).some(k => form.oss[k] !== value.oss[k])
})
const chosen = (name: 'r2' | 'oss') => form.primary === name || form.replica === name
const inUse = (name: StorageName) => !!settings.value?.in_use.includes(name)
const hasSaved = (name: 'r2' | 'oss') => {
  const value = settings.value?.[name]
  return !!value && (value.secret_set || Object.entries(value).some(([k, v]) => k !== 'secret_set' && !!v))
}
// 已经填了一部分（例如先选了 R2、填了一半又换回本机）也算：不能让填过的内容从页面上消失
const typed = (name: 'r2' | 'oss') => name === 'r2' ? Object.values(form.r2).some(Boolean) || !!r2Secret.value : Object.values(form.oss).some(Boolean) || !!ossSecret.value
// 选中了、保存过、填过或还存着文件的云存储才显示它的连接信息
const showR2 = computed(() => chosen('r2') || hasSaved('r2') || inUse('r2') || typed('r2'))
const showOss = computed(() => chosen('oss') || hasSaved('oss') || inUse('oss') || typed('oss'))
const replicaOptions = computed(() => (['local', 'r2', 'oss'] as const).filter(name => name !== form.primary))
// 换主存储时副本不能和它相同：自动改成“不复制”
watch(() => form.primary, primary => { if (form.replica === primary) form.replica = 'none' })

const PRIMARY_CHOICES: { name: StorageName; hint: string }[] = [
  { name: 'local', hint: '工作台服务器上的卷 kb-files，随 make backup 备份' },
  { name: 'r2', hint: 'Cloudflare 的对象存储（S3 兼容）' },
  { name: 'oss', hint: '阿里云的对象存储（S3 兼容），国内访问快' },
]
const REPLICA_NAMES: Record<ReplicaName, string> = { none: '不复制', ...STORAGE_NAMES }
const usedText = computed(() => (settings.value?.in_use ?? []).map(name => STORAGE_NAMES[name]).join('、'))
const r2Preview = computed(() => `${r2Host(form.r2.account_id.trim().toLowerCase(), form.r2.jurisdiction.trim().toLowerCase())}/${form.r2.bucket.trim() || '<桶名>'}`)
const ossPreview = computed(() => ossHost(form.oss.bucket.trim(), form.oss.region.trim().toLowerCase()))
const when = (iso: string) => new Date(iso).toLocaleString('zh-CN', { hour12: false })

function secretOf(secret: string, clear: boolean): string | null {
  if (clear) return ''
  return secret.trim() ? secret.trim() : null
}
function clearStore(name: 'r2' | 'oss') {
  if (name === 'r2') { form.r2 = { account_id: '', bucket: '', jurisdiction: '', access_key_id: '' }; r2Secret.value = ''; clearR2.value = true }
  else { form.oss = { region: '', bucket: '', access_key_id: '' }; ossSecret.value = ''; clearOss.value = true }
}
function reset() {
  if (settings.value) fill(settings.value)
  error.value = ''; notice.value = ''; checks.value = null
}
async function focusField(loc: string[] | null) {
  if (!loc?.length) return
  await nextTick()
  const target = document.getElementById(`storage-${loc.join('-')}`) ?? document.getElementById(`storage-${loc[0]}`)
  target?.focus()
}
async function submit() {
  const value = settings.value
  if (busy.value || !value || !canEdit.value) return
  busy.value = true; error.value = ''; notice.value = ''; checks.value = null
  const body: StorageSettingsBody = {
    version: value.version, primary: form.primary, replica: form.replica,
    r2: { ...form.r2, secret_access_key: secretOf(r2Secret.value, clearR2.value) },
    oss: { ...form.oss, access_key_secret: secretOf(ossSecret.value, clearOss.value) },
  }
  try {
    const saved = await save(body)
    checks.value = saved.checks ?? null
    notice.value = '已保存：自检通过，新上传的图片会存到这里。'
  } catch (cause) {
    const failed = failedChecks(cause)
    if (failed) checks.value = failed
    error.value = storageError(cause)
    if (isVersionConflict(cause)) {
      error.value = '图片存储设置刚被其他人修改：已载入最新设置，请核对后再保存。'
      loaded.value = null
      void query.refetch()
    }
    void focusField(invalidField(cause))
  } finally {
    // 提交过就清空密钥输入，不论成功与否
    r2Secret.value = ''; ossSecret.value = ''; busy.value = false
  }
}
</script>

<template>
  <ManagementPage title="图片存储" description="知识库图片与常驻环境快照存在哪里：本机、Cloudflare R2 或阿里云 OSS。每个工作空间单独设置。">
    <template #actions>
      <RouterLink class="secondary-button" to="/operations"><Activity aria-hidden="true" />查看存储状态</RouterLink>
    </template>
    <DataState :loading="query.isPending.value" :error="query.error.value" :empty="false" empty-text="" :stale="!!settings" @retry="query.refetch()" />
    <form v-if="settings" class="st-form" aria-label="图片存储设置" novalidate @submit.prevent="submit">
      <p v-if="!canEdit" class="mg-hint"><Info aria-hidden="true" />只有 owner 能修改图片存储设置；你可以查看当前设置。</p>
      <fieldset :disabled="!canEdit || busy" class="st-fieldset">
        <ManagementSection title="存储位置" description="写主存储成功就算上传成功；配了副本时 Worker 每分钟把新图片复制一份过去，读主存储失败时读副本。">
          <div class="st-choices" role="radiogroup" aria-label="主存储">
            <label v-for="choice in PRIMARY_CHOICES" :key="choice.name" class="st-choice" :class="{ 'is-selected': form.primary === choice.name }">
              <input v-model="form.primary" type="radio" name="storage-primary" :value="choice.name" />
              <span class="st-choice-icon" aria-hidden="true"><HardDrive v-if="choice.name === 'local'" /><Cloud v-else /></span>
              <span class="st-choice-text"><strong>{{ STORAGE_NAMES[choice.name] }}</strong><small>{{ choice.hint }}</small></span>
            </label>
          </div>
          <label class="form-field st-replica">
            <span>副本</span>
            <select id="storage-replica" v-model="form.replica">
              <option value="none">{{ REPLICA_NAMES.none }}</option>
              <option v-for="name in replicaOptions" :key="name" :value="name">{{ REPLICA_NAMES[name] }}</option>
            </select>
          </label>
          <p v-if="form.replica === 'none'" class="form-help">没有副本：图片只存在主存储里。</p>
          <p v-else class="form-help">以后才加副本时，以前的图片也会补一份。换了主存储以后，旧图片仍从原来的存储读，新图片写到新的主存储。</p>
          <p v-if="usedText" class="form-help">还存着本工作空间图片或快照：{{ usedText }}。它们的连接信息不能清空，否则那些文件读不到。</p>
        </ManagementSection>

        <ManagementSection v-if="showR2" title="Cloudflare R2" label="Cloudflare R2 连接">
          <template v-if="canEdit && !clearR2 && hasSaved('r2')" #actions>
            <button type="button" class="secondary-button" :disabled="chosen('r2') || inUse('r2')" :title="chosen('r2') ? '正在使用 R2，先改选别的存储' : inUse('r2') ? 'R2 里还存着文件' : undefined" @click="clearStore('r2')">清空 R2 设置</button>
          </template>
          <p class="form-help">在 Cloudflare 的 R2 页面建一个私有桶，再创建 API 令牌：权限只给对象读写、只限这个桶，得到 Access Key ID 与 Secret Access Key。图片一律经工作台读取，浏览器不直连桶。</p>
          <p v-if="clearR2" class="mg-hint"><Info aria-hidden="true" />保存后清空 R2 的连接信息与密钥。<button type="button" class="link-button" @click="reset">撤销</button></p>
          <div class="st-grid">
            <label class="form-field"><span>账户 ID</span><input id="storage-r2-account_id" v-model="form.r2.account_id" autocomplete="off" spellcheck="false" placeholder="R2 概览页右侧的 Account ID" /></label>
            <label class="form-field"><span>桶名</span><input id="storage-r2-bucket" v-model="form.r2.bucket" autocomplete="off" spellcheck="false" placeholder="例如 workspace-images" /></label>
            <label class="form-field"><span>辖区（可不填）</span><input id="storage-r2-jurisdiction" v-model="form.r2.jurisdiction" autocomplete="off" spellcheck="false" placeholder="建桶时选了欧盟等辖区才填，例如 eu" /></label>
            <label class="form-field"><span>Access Key ID</span><input id="storage-r2-access_key_id" v-model="form.r2.access_key_id" autocomplete="off" spellcheck="false" /></label>
            <label class="form-field st-secret">
              <span>Secret Access Key</span>
              <input id="storage-r2-secret_access_key" v-model="r2Secret" type="password" autocomplete="new-password" spellcheck="false" :placeholder="settings.r2.secret_set && !clearR2 ? '已设置，不改就留空' : '创建令牌时显示的 Secret'" />
            </label>
          </div>
          <p class="st-preview">地址：<code>{{ r2Preview }}</code></p>
        </ManagementSection>

        <ManagementSection v-if="showOss" title="阿里云 OSS" label="阿里云 OSS 连接">
          <template v-if="canEdit && !clearOss && hasSaved('oss')" #actions>
            <button type="button" class="secondary-button" :disabled="chosen('oss') || inUse('oss')" :title="chosen('oss') ? '正在使用 OSS，先改选别的存储' : inUse('oss') ? 'OSS 里还存着文件' : undefined" @click="clearStore('oss')">清空 OSS 设置</button>
          </template>
          <p class="form-help">建一个私有桶；建议用 RAM 用户的 AccessKey，只授权这个桶的对象读写。地域填桶所在的地域，例如 cn-hangzhou。</p>
          <p v-if="clearOss" class="mg-hint"><Info aria-hidden="true" />保存后清空 OSS 的连接信息与密钥。<button type="button" class="link-button" @click="reset">撤销</button></p>
          <div class="st-grid">
            <label class="form-field"><span>地域</span><input id="storage-oss-region" v-model="form.oss.region" autocomplete="off" spellcheck="false" placeholder="例如 cn-hangzhou" /></label>
            <label class="form-field"><span>桶名</span><input id="storage-oss-bucket" v-model="form.oss.bucket" autocomplete="off" spellcheck="false" /></label>
            <label class="form-field"><span>AccessKey ID</span><input id="storage-oss-access_key_id" v-model="form.oss.access_key_id" autocomplete="off" spellcheck="false" /></label>
            <label class="form-field st-secret">
              <span>AccessKey Secret</span>
              <input id="storage-oss-access_key_secret" v-model="ossSecret" type="password" autocomplete="new-password" spellcheck="false" :placeholder="settings.oss.secret_set && !clearOss ? '已设置，不改就留空' : 'AccessKey Secret'" />
            </label>
          </div>
          <p class="st-preview">地址：<code>{{ ossPreview }}</code></p>
        </ManagementSection>
      </fieldset>

      <section v-if="checks && Object.keys(checks).length" class="ws-panel st-checks" aria-label="自检结果">
        <h2>自检结果</h2>
        <ul>
          <li v-for="(check, name) in checks" :key="name" :class="check.ok ? 'is-good' : 'is-bad'">
            <CircleCheck v-if="check.ok" aria-hidden="true" /><CircleX v-else aria-hidden="true" />
            <strong>{{ STORAGE_NAMES[name as StorageName] ?? name }}</strong>
            <span>{{ checkText(check) }}</span>
          </li>
        </ul>
      </section>
      <ActionFeedback :error="error" :notice="notice" />

      <div v-if="canEdit" class="st-actions">
        <p class="form-help">保存前会在主存储（和副本）里写入、读取并删除一个探测对象，确认能用；不通过就不保存。</p>
        <div class="form-actions">
          <button type="submit" class="primary-button" :disabled="busy || !dirty">{{ busy ? '正在检查…' : '检查并保存' }}</button>
          <button v-if="dirty && !busy" type="button" class="secondary-button" @click="reset">取消修改</button>
        </div>
      </div>
      <p v-if="settings.updated_at" class="form-help st-updated">上次修改：{{ when(settings.updated_at) }}</p>
    </form>
  </ManagementPage>
</template>
