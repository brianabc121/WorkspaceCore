<script setup lang="ts">
import { computed, useId } from 'vue'
import type { SecretDraft } from './settingsForm'

// 只写不读的密钥输入：已设置时只显示“已设置”，从不回填原值；留空 = 不改，填了 = 换成新值，点“清除” = 保存时删掉。
// 值只在设置页的草稿里（组件内存），保存、取消或离开页面就清掉。
const props = defineProps<{ label: string; isSet: boolean; placeholder?: string; disabled?: boolean }>()
const model = defineModel<SecretDraft>({ required: true })
const id = useId()
const placeholderText = computed(() => (model.value.clear ? '保存时清除' : props.isSet ? '已设置（不回显）；不填表示不改' : props.placeholder ?? '未设置'))
</script>

<template>
  <div class="form-field nws-secret-field">
    <label :for="id">{{ label }}</label>
    <div class="news-secret">
      <input :id="id" :value="model.value" type="password" autocomplete="new-password" maxlength="1000" :disabled="disabled || model.clear" :placeholder="placeholderText" @input="model = { value: ($event.target as HTMLInputElement).value, clear: false }" />
      <span v-if="isSet && !model.clear" class="asv-state tone-green">已设置</span>
      <span v-if="model.clear" class="asv-state tone-amber">保存后清除</span>
      <button v-if="isSet && !model.clear" type="button" class="secondary-button" :disabled="disabled" :aria-label="`清除${label}`" @click="model = { value: '', clear: true }">清除</button>
      <button v-else-if="model.clear" type="button" class="secondary-button" :disabled="disabled" :aria-label="`撤销清除${label}`" @click="model = { value: '', clear: false }">撤销清除</button>
    </div>
  </div>
</template>
