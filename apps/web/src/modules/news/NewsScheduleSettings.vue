<script setup lang="ts">
import { computed, useId } from 'vue'
import { Bot, CalendarClock, Clock, Globe, Plus, X } from '@lucide/vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import type { Schema } from '../../services/api'
import { MAX_PUSH_TIMES, PUSH_TIME_OPTIONS, type FormPatch, type NewsForm } from './settingsForm'
import { INSTANCE_STATUS } from './settingsState'

// “基本与时间”标签的早报计划（T-0195，效果图 06）：启用开关、1~3 个半小时为单位的推送时间、只读的部署时区、
// 两种早报方式与执行智能体。只发出对草稿的修改（patch），不保存。
// 所选实例不在可选列表里（已停用或已删除）时照实显示，不偷偷换成别的实例。
const props = defineProps<{
  form: NewsForm; timezone: string; canEdit: boolean; disabled: boolean
  instances: Schema['AssigneeInstance'][] | undefined; instancesFailed: boolean
}>()
const emit = defineEmits<{ patch: [FormPatch] }>()
const id = useId()
const set = <K extends keyof NewsForm>(key: K, value: NewsForm[K]) => emit('patch', { [key]: value } as FormPatch)
const target = (event: Event) => event.target as HTMLInputElement | HTMLSelectElement

function setTime(index: number, value: string) { set('push_times', props.form.push_times.map((t, i) => (i === index ? value : t))) }
function removeTime(index: number) { if (props.form.push_times.length > 1) set('push_times', props.form.push_times.filter((_, i) => i !== index)) }
function addTime() {
  if (props.form.push_times.length >= MAX_PUSH_TIMES) return
  set('push_times', [...props.form.push_times, PUSH_TIME_OPTIONS.find(t => !props.form.push_times.includes(t)) ?? '07:30'])
}

const selected = computed(() => props.form.agent_instance_id)
// 列表读到了但里面没有：已停用（列表不含停用的）或已删除
const missing = computed(() => !!selected.value && !!props.instances && !props.instances.some(i => i.id === selected.value))
// 列表还没读到或读取失败：保留原来的选择，说明情况
const unknown = computed(() => !!selected.value && !props.instances)
</script>

<template>
  <ManagementSection title="早报计划">
    <template #title><span class="nws-title"><CalendarClock aria-hidden="true" />早报计划</span></template>
    <fieldset class="nws-fieldset nws-rows" :disabled="disabled">
      <div class="nws-row">
        <label class="nws-label" :for="`${id}-enabled`">启用新闻早报</label>
        <div class="nws-control">
          <span class="nws-switch"><input :id="`${id}-enabled`" type="checkbox" role="switch" :checked="form.enabled" @change="set('enabled', (target($event) as HTMLInputElement).checked)" /><span aria-hidden="true"></span></span>
          <p class="form-help">关闭时 TrendRadar 不抓取、不推送，早报页仍能看往期。它与应用中心里停用“新闻早报”功能是两回事。</p>
        </div>
      </div>

      <div class="nws-row">
        <span :id="`${id}-times`" class="nws-label">推送时间</span>
        <div class="nws-control">
          <div class="nws-times" role="group" :aria-labelledby="`${id}-times`">
            <span v-for="(time, index) in form.push_times" :key="index" class="nws-time">
              <Clock aria-hidden="true" />
              <select :value="time" :aria-label="`推送时间 ${index + 1}`" @change="setTime(index, target($event).value)">
                <option v-for="t in PUSH_TIME_OPTIONS" :key="t" :value="t">{{ t }}</option>
              </select>
              <button v-if="canEdit && form.push_times.length > 1" type="button" class="nws-icon-button" :aria-label="`删除推送时间 ${index + 1}`" @click="removeTime(index)"><X aria-hidden="true" /></button>
            </span>
            <button v-if="canEdit && form.push_times.length < MAX_PUSH_TIMES" type="button" class="secondary-button nws-add" @click="addTime"><Plus aria-hidden="true" />添加推送时间</button>
          </div>
          <p class="form-help">最多 {{ MAX_PUSH_TIMES }} 个，按半小时设置；按下面的时区计算。</p>
        </div>
      </div>

      <div class="nws-row">
        <span class="nws-label">时区</span>
        <div class="nws-control">
          <p class="nws-readonly"><Globe aria-hidden="true" /><strong>{{ timezone }}</strong><span>· 部署时区（只读）</span></p>
          <p class="form-help">部署时在 <code>.env</code> 的 <code>TRENDRADAR_TZ</code> 里设置，与 TrendRadar 容器一致；推送时间与“今天”都按它算。</p>
        </div>
      </div>

      <div class="nws-row">
        <span :id="`${id}-mode`" class="nws-label">早报方式</span>
        <div class="nws-control nws-modes" role="radiogroup" :aria-labelledby="`${id}-mode`">
          <label class="nws-mode" :class="{ 'is-selected': form.briefing_mode === 'trendradar' }">
            <input type="radio" :name="`${id}-mode`" value="trendradar" :checked="form.briefing_mode === 'trendradar'" @change="set('briefing_mode', 'trendradar')" />
            <span><strong>TrendRadar 汇总</strong><small>到推送时间由 TrendRadar 汇总多个来源的热点、AI 分析后推送。</small></span>
          </label>
          <label class="nws-mode" :class="{ 'is-selected': form.briefing_mode === 'agent' }">
            <input type="radio" :name="`${id}-mode`" value="agent" :checked="form.briefing_mode === 'agent'" @change="set('briefing_mode', 'agent')" />
            <span><strong>智能体写早报</strong><small>到推送时间由所选智能体用新闻工具写早报，工作台再推送。</small></span>
          </label>
        </div>
      </div>

      <div v-if="form.briefing_mode === 'agent'" class="nws-row">
        <label class="nws-label" :for="`${id}-agent`">执行智能体</label>
        <div class="nws-control">
          <span class="nws-select-icon"><Bot aria-hidden="true" />
            <select :id="`${id}-agent`" :value="form.agent_instance_id" @change="set('agent_instance_id', target($event).value)">
              <option value="">选择一个实例</option>
              <option v-for="i in instances ?? []" :key="i.id" :value="i.id">{{ i.name }}（{{ INSTANCE_STATUS[i.status] ?? i.status }}）</option>
              <option v-if="missing" :value="form.agent_instance_id">已停用或已删除的实例（{{ form.agent_instance_id.slice(0, 8) }}）</option>
              <option v-else-if="unknown" :value="form.agent_instance_id">{{ instancesFailed ? '实例列表读取失败' : '正在读取实例…' }}</option>
            </select>
          </span>
          <p v-if="missing" role="alert" class="nws-inline tone-red">所选实例已停用或已删除：到推送时间写不了早报。这里不会自动换成别的实例，请改选一个后保存。</p>
          <p v-else-if="instancesFailed" role="alert" class="nws-inline tone-amber">实例列表读取失败：保留原来的选择，稍后点“刷新状态”再试。</p>
          <p class="form-help">到推送时间在“每日早报”项目里建任务交给这个实例，写好后由工作台推送；90 分钟没写完记为失败并通知 owner。智能体用它自己的模型写早报，“模型与渠道”里的大模型只给 TrendRadar 用。</p>
        </div>
      </div>
    </fieldset>
  </ManagementSection>
</template>
