<script setup lang="ts">
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { z } from 'zod'
import { ref } from 'vue'
import type { Schema } from '../../services/api'
import { addDays, localDateTime, validZone, zonedInstant } from './time'
import TextField from '../../components/TextField.vue'
const props = defineProps<{ event?: Schema['CalendarEventOut']; date: string; zone: string; writable: boolean; busy: boolean; locked: boolean }>()
const emit = defineEmits<{ save: [body: Schema['CalendarEventCreate']]; remove: [] }>()
const old = props.event, zone = old?.timezone ?? props.zone
const { handleSubmit, defineField } = useForm({ validationSchema: toTypedSchema(z.object({ title: z.string().trim().min(1, '请输入日程标题').max(200), description: z.string().max(5000), location: z.string().max(200), timezone: z.string().trim().max(64).refine(validZone, '请输入有效的 IANA 时区，例如 Asia/Shanghai'), all_day: z.boolean(), start: z.string(), end: z.string(), start_date: z.string(), end_date: z.string() })), initialValues: { title: old?.title ?? '', description: old?.description ?? '', location: old?.location ?? '', timezone: zone, all_day: old?.all_day ?? false, start: old ? localDateTime(old.start_at, zone) : `${props.date}T09:00`, end: old ? localDateTime(old.end_at, zone) : `${props.date}T10:00`, start_date: old?.start_date ?? props.date, end_date: old?.end_date ?? addDays(props.date, 1) } })
const [description] = defineField('description'), [location] = defineField('location'), [allDay] = defineField('all_day'), [start] = defineField('start'), [end] = defineField('end'), [startDate] = defineField('start_date'), [endDate] = defineField('end_date')
const message = ref('')
const submit = handleSubmit((v) => {
  if (!props.writable || props.busy || props.locked) return
  try {
    const body: Schema['CalendarEventCreate'] = { title: v.title, description: v.description, location: v.location, timezone: v.timezone, all_day: v.all_day }
    let from: number, to: number
    if (v.all_day) {
      from = Date.parse(`${addDays(v.start_date, 0)}T00:00:00Z`); to = Date.parse(`${addDays(v.end_date, 0)}T00:00:00Z`)
      body.start_date = v.start_date; body.end_date = v.end_date
    } else {
      body.start_at = old && !old.all_day && v.timezone === old.timezone && v.start === localDateTime(old.start_at, old.timezone) ? old.start_at : zonedInstant(v.start, v.timezone)
      body.end_at = old && !old.all_day && v.timezone === old.timezone && v.end === localDateTime(old.end_at, old.timezone) ? old.end_at : zonedInstant(v.end, v.timezone)
      from = Date.parse(body.start_at); to = Date.parse(body.end_at)
    }
    if (to <= from) throw new Error('结束时间或日期必须晚于开始。')
    if (to - from > 92 * 86400000) throw new Error('日程持续时间不能超过 92 天。')
    message.value = ''; emit('save', body)
  } catch (cause) { message.value = cause instanceof Error ? cause.message : '日期或时区无效。' }
}, ({ errors }) => { message.value = Object.values(errors).join('；') })
</script>
<template><form aria-label="日程编辑" novalidate @submit.prevent="submit"><fieldset :disabled="!writable || busy || locked"><TextField name="title" label="日程标题" :maxlength="200" /><TextField name="timezone" label="事件时区" :maxlength="64" /><p class="form-help">时间输入按此 IANA 时区解释。夏令时不存在的当地时间会被拒绝；重复的当地时间采用较早的一次。</p><label class="check-field"><input v-model="allDay" type="checkbox">全天事件</label><template v-if="allDay"><label class="form-field">开始日期<input v-model="startDate" type="date"></label><label class="form-field">结束日期（不含）<input v-model="endDate" type="date"></label><p class="form-help">一天的全天事件，结束日期填写下一天。</p></template><template v-else><label class="form-field">开始时间<input v-model="start" type="datetime-local"></label><label class="form-field">结束时间<input v-model="end" type="datetime-local"></label></template><label class="form-field">日程地点<input v-model="location" maxlength="200"></label><label class="form-field">日程说明<textarea v-model="description" maxlength="5000" rows="4"></textarea></label><p v-if="message" role="alert">{{ message }}</p><div v-if="writable" class="form-actions"><button class="primary-button">保存日程</button><button v-if="event" type="button" class="secondary-button" @click="$emit('remove')">删除日程</button></div></fieldset><p v-if="!writable" class="form-help">当前角色只读。</p></form></template>
