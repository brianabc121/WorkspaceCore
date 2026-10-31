<script setup lang="ts">
import { useForm } from 'vee-validate'
import { toTypedSchema } from '../../services/forms'
import { z } from 'zod'
import { ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import type { Schema } from '../../services/api'
import { bookmarkUrlError, type BookmarkFields } from './validation'
import TextField from '../../components/TextField.vue'
import ResourceLinks from './ResourceLinks.vue'
const props = defineProps<{ source: Schema['KnowledgeSourceOut']; bookmark?: Schema['KnowledgeResourceOut']; linkedId?: string; busy: boolean; locked: boolean }>()
const emit = defineEmits<{ save: [body: BookmarkFields] }>()
const auth = useAuthStore(), owns = !props.bookmark || props.bookmark.owner_id === auth.user?.id
const selected = ref(props.bookmark?.links.map((item) => item.resource_id) ?? (props.linkedId ? [props.linkedId] : []))
const { handleSubmit, defineField } = useForm({ validationSchema: toTypedSchema(z.object({ title: z.string().trim().min(1, '请输入收藏标题').max(200), url: z.string().trim().superRefine((v, ctx) => { const message = bookmarkUrlError(v, props.source.base_url); if (message) ctx.addIssue({ code: 'custom', message }) }), note: z.string().max(2000), visibility: z.enum(['workspace', 'private']) })), initialValues: { title: props.bookmark?.title ?? '', url: props.bookmark?.url ?? '', note: props.bookmark?.note ?? '', visibility: props.bookmark?.visibility ?? 'workspace' } })
const [note] = defineField('note'), [visibility] = defineField('visibility'), message = ref('')
const submit = handleSubmit((v) => {
  if (props.busy || props.locked) return
  if (selected.value.length > 20) { message.value = '最多关联 20 个项目或任务。'; return }
  const body: BookmarkFields = { title: v.title, url: v.url, note: v.note, linked_resource_ids: selected.value }
  if (owns) body.visibility = v.visibility
  emit('save', body)
}, ({ errors }) => { message.value = Object.values(errors).join('；') })
</script>
<template><form aria-label="收藏编辑" novalidate @submit.prevent="submit"><fieldset :disabled="busy || locked"><TextField name="title" label="收藏标题" :maxlength="200" /><TextField name="url" label="文档链接" :maxlength="2048" /><p class="form-help">来源：{{ source.base_url }}。只保存链接、标题与备注，不读取 AFFiNE 正文。</p><label class="form-field">收藏备注<textarea v-model="note" maxlength="2000" rows="3"></textarea></label><label class="form-field">收藏可见性<select v-model="visibility" :disabled="!owns"><option value="workspace">工作空间成员可见</option><option value="private">仅自己可见</option></select></label><p v-if="!owns" class="form-help">只有创建者可以修改可见性。</p><ResourceLinks v-model="selected" :existing="bookmark?.links ?? []" :disabled="busy || locked" /><p v-if="message" role="alert">{{ message }}</p><button class="primary-button">保存收藏</button></fieldset></form></template>
