<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { Info, Plus, RefreshCw, X } from '@lucide/vue'
import { SCOPE_LABEL } from './api'

// 模型范围（契约第 1 节“模型记录”）：指定模型 = 手工添加一个或多个明确的模型 ID，或从最近一次完整同步的列表里勾选；
// 服务商全部可用模型 = 最近一次完整同步的明确 ID 集合（要先同步一次），不是通配符。品牌名称（GPT / Claude）不是模型 ID。
// 只管表单里的值（v-model），保存由外层表单决定。
const props = defineProps<{
  scope: 'selected' | 'all_available'
  models: string[]
  /** 最近一次完整同步到的模型 ID（没有同步过为空） */
  discovered: string[]
  /** 已保存的连接才能同步；新建时为 false */
  canSync: boolean
  syncing?: boolean
  disabled?: boolean
  maxModels?: number
}>()
const emit = defineEmits<{ 'update:scope': [value: 'selected' | 'all_available']; 'update:models': [value: string[]]; sync: [] }>()
const ids = { group: useId(), input: useId(), filter: useId() }
const draft = ref(''), problem = ref(''), filter = ref(''), picking = ref(false)
const MODEL_ID = /^[^\s\p{Cc}]{1,128}$/u
const SHOWN = 30
const max = computed(() => props.maxModels ?? 1000)

function add() {
  const values = draft.value.split(/[\s,，]+/).map(v => v.trim()).filter(Boolean)
  if (!values.length) return
  const bad = values.find(v => !MODEL_ID.test(v))
  if (bad) { problem.value = `“${bad.slice(0, 40)}”不是有效的模型 ID：1–128 个字符，不能含空白或控制字符。`; return }
  const next = [...new Set([...props.models, ...values])]
  if (next.length > max.value) { problem.value = `每条连接最多 ${max.value} 个模型。`; return }
  problem.value = ''
  draft.value = ''
  emit('update:models', next)
}
function remove(id: string) { emit('update:models', props.models.filter(m => m !== id)) }
function toggle(id: string, on: boolean) {
  emit('update:models', on ? [...new Set([...props.models, id])] : props.models.filter(m => m !== id))
}
const chosen = computed(() => new Set(props.models))
const matching = computed(() => {
  const q = filter.value.trim().toLowerCase()
  return (q ? props.discovered.filter(m => m.toLowerCase().includes(q)) : props.discovered).slice(0, 100)
})
const preview = computed(() => (props.scope === 'all_available' ? props.discovered : props.models).slice(0, SHOWN))
const rest = computed(() => (props.scope === 'all_available' ? props.discovered.length : props.models.length) - preview.value.length)
</script>

<template>
  <fieldset class="mc-scope" :disabled="disabled">
    <div class="mc-scope-head">
      <legend :id="ids.group">模型范围</legend>
      <button v-if="canSync" type="button" class="secondary-button mc-inline" :disabled="disabled || syncing" @click="emit('sync')"><RefreshCw aria-hidden="true" />{{ syncing ? '正在同步…' : '同步模型' }}</button>
    </div>
    <div class="mc-radios" role="radiogroup" :aria-labelledby="ids.group">
      <label v-for="(label, value) in SCOPE_LABEL" :key="value" class="mc-radio"><input type="radio" :name="ids.group" :value="value" :checked="scope === value" @change="emit('update:scope', value)" />{{ label }}</label>
    </div>

    <template v-if="scope === 'selected'">
      <ul v-if="models.length" class="mc-chips" aria-label="已选模型">
        <li v-for="m in preview" :key="m" class="mc-chip"><code>{{ m }}</code><button type="button" class="mc-chip-x" :aria-label="`移除模型 ${m}`" @click="remove(m)"><X aria-hidden="true" /></button></li>
        <li v-if="rest > 0" class="mc-chip is-more">还有 {{ rest }} 个</li>
      </ul>
      <p v-else class="form-help">还没有模型：手工添加模型 ID，或同步后从列表里勾选。</p>
      <div class="mc-add">
        <label :for="ids.input" class="sr-only">添加模型 ID</label>
        <input :id="ids.input" v-model="draft" placeholder="模型 ID，例如 gpt-4.1-mini（多个用空格或逗号分隔）" spellcheck="false" autocomplete="off" maxlength="2000" @keydown.enter.prevent="add" />
        <button type="button" class="secondary-button mc-inline" :disabled="!draft.trim()" @click="add"><Plus aria-hidden="true" />添加</button>
      </div>
      <p v-if="problem" class="feedback" role="alert">{{ problem }}</p>
      <template v-if="discovered.length">
        <button type="button" class="link-button mc-link" :aria-expanded="picking" @click="picking = !picking">{{ picking ? '收起同步列表' : `从同步列表勾选（${discovered.length} 个）` }}</button>
        <div v-if="picking" class="mc-pick">
          <label :for="ids.filter" class="sr-only">筛选同步到的模型</label>
          <input :id="ids.filter" v-model="filter" type="search" placeholder="按模型 ID 筛选" spellcheck="false" />
          <ul class="mc-pick-list" aria-label="同步到的模型">
            <li v-for="m in matching" :key="m"><label><input type="checkbox" :checked="chosen.has(m)" @change="toggle(m, ($event.target as HTMLInputElement).checked)" /><code>{{ m }}</code></label></li>
          </ul>
          <p v-if="matching.length < discovered.length" class="form-help">显示前 {{ matching.length }} 个匹配项，输入更多字符缩小范围。</p>
        </div>
      </template>
    </template>
    <template v-else>
      <ul v-if="discovered.length" class="mc-chips" aria-label="同步到的模型">
        <li v-for="m in preview" :key="m" class="mc-chip"><code>{{ m }}</code></li>
        <li v-if="rest > 0" class="mc-chip is-more">还有 {{ rest }} 个</li>
      </ul>
      <p class="form-help mc-hint"><Info aria-hidden="true" /><span v-if="discovered.length">以 API Key 实际可用的模型为准：共同步到 {{ discovered.length }} 个明确的模型 ID，列出的模型不代表都能推理。</span><span v-else-if="canSync">还没有完整同步过：点“同步模型”取得服务商给这个 Key 的模型列表。服务商不支持列模型时请改用“指定模型”。</span><span v-else>新建时先保存为草稿，再点“同步模型”取得服务商的模型列表，然后启用。</span></p>
    </template>
  </fieldset>
</template>
