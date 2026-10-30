<script setup lang="ts">
import { useId } from 'vue'
import { useField } from 'vee-validate'
const props = withDefaults(defineProps<{ name: string; label: string; type?: string; autocomplete?: string; maxlength?: number; readonly?: boolean }>(), { type: 'text', autocomplete: 'off', readonly: false, maxlength: undefined })
const id = useId()
const { value, errorMessage, handleBlur } = useField<string>(() => props.name)
</script>
<template>
  <div class="form-field">
    <label :for="id">{{ label }}</label>
    <input :id="id" v-model="value" :name="name" :type="type" :autocomplete="autocomplete" :maxlength="maxlength" :readonly="readonly" :aria-invalid="!!errorMessage" :aria-describedby="errorMessage ? `${id}-error` : undefined" @blur="handleBlur">
    <small v-if="errorMessage" :id="`${id}-error`" class="field-error">{{ errorMessage }}</small>
  </div>
</template>
