<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { ArrowRight, FolderGit2 } from '@lucide/vue'
import { errorMessage, type Schema } from '../../services/api'
import { formatTime } from '../time'
import GitHubForm from './GitHubForm.vue'
import { MAX_REPOSITORIES, TOKEN_URL } from './githubValidation'

// 右侧“GitHub 连接配置”（T-0192，效果图 03 右栏）：平时显示已保存的配置（Token 只显示是否已设置），
// 点“修改连接 / 新建 GitHub 连接 / 重新连接”后换成表单。表单编辑的是打开那一刻的连接版本，
// 后台刷新不会改掉正在填写的内容；取消或保存后表单卸载，Token 输入随之清空。
const props = defineProps<{
  integration?: Schema['IntegrationOut']
  editing: { base?: Schema['IntegrationOut'] } | null
  busy: boolean
  locked: boolean
  error: unknown
  /** 连接清单已经读到（还在读取时不说“还没有连接”） */
  loaded: boolean
}>()
const emit = defineEmits<{ save: [body: Schema['GitHubIntegrationCreate'] | Schema['IntegrationUpdate']]; cancel: [] }>()
const root = ref<HTMLElement | null>(null), form = ref<InstanceType<typeof GitHubForm> | null>(null)
const expiry = computed(() => {
  const value = props.integration?.token_expires_at ?? null
  if (!value) return { text: '未填写', flag: '' }
  const left = Date.parse(value) - Date.now()
  return { text: formatTime(value), flag: left <= 0 ? '已过期' : left < 14 * 86_400_000 ? '即将到期' : '' }
})
async function focus(field: 'name' | 'token') {
  await nextTick()
  root.value?.scrollIntoView?.({ block: 'nearest' })
  form.value?.focusField(field)
}
defineExpose({ focus })
</script>

<template>
  <section ref="root" class="ws-panel mg-section ig-config" aria-labelledby="ig-config-title">
    <div class="mg-section-head">
      <div class="mg-section-title"><h2 id="ig-config-title" class="ig-config-title"><FolderGit2 aria-hidden="true" />GitHub 连接配置</h2></div>
    </div>
    <template v-if="editing">
      <GitHubForm ref="form" :integration="editing.base" :busy="busy" :locked="locked" @save="emit('save', $event)" @cancel="emit('cancel')" />
      <p v-if="error" role="alert" class="field-error">{{ errorMessage(error) }}</p>
    </template>
    <template v-else-if="integration">
      <dl class="ig-facts">
        <div><dt>连接名称</dt><dd>{{ integration.name }}</dd></div>
        <div><dt>Token 状态</dt><dd><span class="ig-chip" :class="integration.token_set ? 'tone-green' : 'tone-amber'">{{ integration.token_set ? '已设置 · 不回显' : '未设置' }}</span></dd></div>
        <div>
          <dt>同步的仓库（{{ integration.repositories?.length ?? 0 }} / {{ MAX_REPOSITORIES }}）</dt>
          <dd><ul class="ig-repo-list"><li v-for="name in integration.repositories ?? []" :key="name">{{ name }}</li></ul></dd>
        </div>
        <div><dt>Token 到期时间</dt><dd>{{ expiry.text }}<span v-if="expiry.flag" class="ig-chip tone-amber">{{ expiry.flag }}</span></dd></div>
        <div><dt>启用连接</dt><dd>{{ integration.status === 'disabled' ? '已停用' : '已启用' }}</dd></div>
      </dl>
      <p class="form-help">更换 Token、修改仓库清单或启停连接后，旧的核验结果失效，需要重新测试连接。</p>
      <a class="asv-link ig-token-link" :href="TOKEN_URL" target="_blank" rel="noopener noreferrer">获取细粒度 Token<ArrowRight aria-hidden="true" /></a>
    </template>
    <p v-else-if="!loaded" class="form-help">正在读取连接配置…</p>
    <p v-else class="form-help">还没有 GitHub 连接。点页面右上角的“新建 GitHub 连接”，按步骤生成只读的细粒度 Token 后粘贴保存。</p>
  </section>
</template>
