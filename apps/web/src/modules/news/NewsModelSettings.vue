<script setup lang="ts">
import { useId } from 'vue'
import { Cpu } from '@lucide/vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import NewsSecretInput from './NewsSecretInput.vue'
import type { FormPatch, NewsForm } from './settingsForm'

// “模型与渠道”标签的大模型（T-0195）：TrendRadar 用的 LiteLLM 模型、接口地址、只写的 API Key、
// AI 分析 / 翻译与筛选方式（AI 筛选失败自动退回关键词）。这里改的不是智能体自己的模型。只发出对草稿的修改。
defineProps<{ form: NewsForm; keySet: boolean; disabled: boolean }>()
const emit = defineEmits<{ patch: [FormPatch] }>()
const id = useId()
const target = (event: Event) => event.target as HTMLInputElement
</script>

<template>
  <ManagementSection title="大模型（TrendRadar 用）" description="只给 TrendRadar 的 AI 分析、翻译与 AI 筛选用，不会改“智能体写早报”所用智能体自己的模型。">
    <template #title><span class="nws-title"><Cpu aria-hidden="true" />大模型（TrendRadar 用）</span></template>
    <fieldset class="nws-fieldset" :disabled="disabled">
      <p class="form-help">模型用 LiteLLM 的写法，例如 <code>deepseek/deepseek-chat</code>、<code>openai/gpt-4o-mini</code>、<code>ollama/qwen2.5</code>。Ollama 这类不需要 Key 的可以不填 Key。</p>
      <div class="nws-grid">
        <label class="form-field"><span>模型</span><input :value="form.ai_model" maxlength="100" placeholder="deepseek/deepseek-chat" autocapitalize="off" @input="emit('patch', { ai_model: target($event).value })" /></label>
        <label class="form-field"><span>接口地址（可不填）</span><input :value="form.ai_api_base" type="url" maxlength="500" placeholder="https://api.deepseek.com" @input="emit('patch', { ai_api_base: target($event).value })" /></label>
      </div>
      <NewsSecretInput :model-value="form.ai_api_key" label="API Key" :is-set="keySet" placeholder="sk-…" :disabled="disabled" @update:model-value="emit('patch', { ai_api_key: $event })" />
      <div class="nws-checks">
        <label class="nws-check"><input type="checkbox" :checked="form.ai_analysis" @change="emit('patch', { ai_analysis: target($event).checked })" />AI 分析（早报里加一段分析）</label>
        <label class="nws-check"><input type="checkbox" :checked="form.ai_translation" @change="emit('patch', { ai_translation: target($event).checked })" />翻译（把外文标题译成中文）</label>
      </div>
      <div class="form-field" role="radiogroup" :aria-labelledby="`${id}-filter`">
        <span :id="`${id}-filter`">筛选方式</span>
        <label class="check-field"><input type="radio" :name="`${id}-filter`" value="keyword" :checked="form.filter_method === 'keyword'" @change="emit('patch', { filter_method: 'keyword' })" />关键词：按兴趣主题的关键词匹配</label>
        <label class="check-field"><input type="radio" :name="`${id}-filter`" value="ai" :checked="form.filter_method === 'ai'" @change="emit('patch', { filter_method: 'ai' })" />AI：让大模型按主题描述筛选（失败时自动退回关键词）</label>
      </div>
    </fieldset>
  </ManagementSection>
</template>
