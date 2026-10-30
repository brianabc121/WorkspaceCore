<script setup lang="ts">
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { z } from 'zod'
import { ref } from 'vue'
import type { Schema } from '../../services/api'
import { sourceUrlError } from './validation'
import TextField from '../../components/TextField.vue'
const props = defineProps<{ source?: Schema['KnowledgeSourceOut']; busy: boolean; locked: boolean }>()
const emit = defineEmits<{ save: [body: Schema['KnowledgeSourceCreate']]; remove: [] }>()
const { handleSubmit } = useForm({ validationSchema: toTypedSchema(z.object({ name: z.string().trim().min(1, '请输入来源名称').max(64), base_url: z.string().trim().superRefine((v, ctx) => { const message = sourceUrlError(v); if (message) ctx.addIssue({ code: 'custom', message }) }) })), initialValues: { name: props.source?.name ?? 'AFFiNE', base_url: props.source?.base_url ?? '' } })
const message = ref('')
const submit = handleSubmit((v) => { if (!props.busy && !props.locked) emit('save', { provider: 'affine', ...v }) }, ({ errors }) => { message.value = Object.values(errors).join('；') })
</script>
<template><form aria-label="知识来源配置" novalidate @submit.prevent="submit"><fieldset :disabled="busy || locked"><TextField name="name" label="知识来源名称" :maxlength="64" /><TextField name="base_url" label="AFFiNE 外部地址" :maxlength="1024" /><p class="form-help">填写浏览器访问 AFFiNE 的完整地址，可包含部署子路径。工作台只保存链接与手工备注，不访问或抓取文档正文。</p><p v-if="source" class="form-help">核验版本 {{ source.tested_version }} · 修改地址会更新该来源全部收藏的链接。</p><p v-if="message" role="alert">{{ message }}</p><button class="primary-button">保存知识来源</button><button v-if="source" type="button" class="secondary-button" @click="$emit('remove')">删除知识来源</button></fieldset></form></template>
