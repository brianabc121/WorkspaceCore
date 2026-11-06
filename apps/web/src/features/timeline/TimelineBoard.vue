<script setup lang="ts">
// 任务时间轴看板（T-0105，docs/v2/TIMELINE_PLAN.md 第 2.1 节；效果图 https://claude.ai/artifact/CHuDA9DsafbAudwxB6A61y）。
// 每个执行者（或项目）一条轨道，每段执行一个片段，颜色按项目；最下面一条是 token 用量。地址里带项目、日期、刻度、分组与选中的任务。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AlertTriangle, ChevronLeft, ChevronRight, CircleStop, ExternalLink, MessageSquare, Pause, Play, Send, Square, X } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'
import { useProjects } from '../tasks/shared'
import { TERMINATED, askStopReason, useRunActions, useRuns, useTimeline, runError, type Group } from './api'
import { STATE_LABELS, SCALES, bars, clipOf, clock, compact, dayKey, duration, overdueBy, parseDay, projectColors, rangeOf, shift, startOfDay, ticks, tokenText, type Clip, type Scale } from './model'
import './timeline.css'

const auth = useAuthStore(), route = useRoute(), router = useRouter()
const scope = auth.workspaceId!
const header = { 'X-Workspace-Id': scope }
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const q = (name: string) => (typeof route.query[name] === 'string' ? (route.query[name] as string) : '')
const writable = computed(() => !!auth.currentWorkspace && auth.currentWorkspace.role !== 'viewer')

// ---------------------------------------------------------------- 地址里的状态
const scale = computed<Scale>(() => (['hour', 'day', 'week'].includes(q('scale')) ? q('scale') as Scale : 'day'))
const group = computed<Group>(() => (q('group') === 'project' ? 'project' : 'executor'))
const projectId = computed(() => (uuid.test(q('project')) ? q('project') : ''))
const anchor = computed(() => parseDay(q('date')) ?? startOfDay(new Date()))
const selectedTask = computed(() => (uuid.test(q('task')) ? q('task') : ''))
function go(query: Record<string, string | undefined>) { void router.replace({ query: { ...route.query, ...query } }) }

// 当前时间每 30 秒走一次；“小时”刻度的窗口以进入页面或点“今天”时的时间为中心（不跟着每次走动重新请求）
const now = ref(Date.now()), focus = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => { timer = setInterval(() => { now.value = Date.now() }, 30_000) })
onBeforeUnmount(() => clearInterval(timer))
const range = computed(() => rangeOf(scale.value, anchor.value, new Date(focus.value)))
const start = computed(() => range.value.start.getTime()), end = computed(() => range.value.end.getTime())
const live = computed(() => now.value >= start.value && now.value < end.value)
const follow = ref(true)
const params = computed(() => ({ from: range.value.start.toISOString(), to: range.value.end.toISOString(), group: group.value, ...(projectId.value ? { project_id: projectId.value } : {}) }))
const timeline = useTimeline(scope, params, computed(() => follow.value && live.value))
const data = computed(() => timeline.data.value)
const projects = useProjects(scope)
const projectItems = computed(() => projects.data.value?.pages.flatMap(p => p.items) ?? [])
const colors = computed(() => projectColors(projectItems.value.map(p => p.id)))
const tasks = computed(() => new Map((data.value?.tasks ?? []).map(t => [t.id, t])))

// ---------------------------------------------------------------- 筛选与片段
const onlyRunning = ref(false), onlyOverdue = ref(false)
const ROW = 60, PAD = 10
interface Lane { track: Schema['TimelineTrack']; clips: (Clip & { row: number; color: number })[]; rows: number; tokens: number; running: boolean }
const lanes = computed<Lane[]>(() => {
  const d = data.value
  if (!d) return []
  const byTrack = new Map<string, Lane>(d.tracks.map(track => [track.key, { track, clips: [], rows: 1, tokens: 0, running: false }]))
  for (const segment of d.segments) {
    const task = tasks.value.get(segment.task_id)
    const clip = clipOf(segment, task, start.value, end.value, now.value)
    if (!clip) continue
    if (onlyRunning.value && clip.state !== 'running') continue
    if (onlyOverdue.value && !clip.overdue && overdueBy(task, now.value) === null) continue
    const lane = byTrack.get(segment.track)
    if (!lane) continue
    lane.clips.push({ ...clip, row: 0, color: colors.value.get(task?.project_id ?? '') ?? 0 })
    if (clip.state === 'running') lane.running = true
    lane.tokens += segment.run?.tokens?.total ?? 0
  }
  for (const lane of byTrack.values()) {
    // 同一条轨道里重叠的片段错开到下一行
    const ends: number[] = []
    lane.clips.sort((a, b) => a.start - b.start)
    for (const clip of lane.clips) {
      const row = ends.findIndex(e => e <= clip.start)
      clip.row = row < 0 ? ends.length : row
      ends[clip.row] = clip.end
    }
    lane.rows = Math.max(1, ends.length)
  }
  const out = [...byTrack.values()]
  return onlyRunning.value || onlyOverdue.value ? out.filter(l => l.clips.length) : out
})
const allClips = computed(() => lanes.value.flatMap(l => l.clips))
const selectedClip = computed(() => {
  const id = selectedTask.value
  if (!id) return null
  const mine = allClips.value.filter(c => c.segment.task_id === id)
  return mine.find(c => c.state === 'running') ?? mine.at(-1) ?? null
})
const selectedInfo = computed(() => tasks.value.get(selectedTask.value) ?? null)
function select(clip: Clip) { go({ task: clip.segment.task_id }) }
function close() { go({ task: undefined }) }

// ---------------------------------------------------------------- 刻度、用量、现在
const tickList = computed(() => ticks(scale.value, range.value.start, range.value.end))
const usageBars = computed(() => (data.value ? bars(data.value.usage, start.value, end.value) : []))
const playhead = computed(() => (live.value ? ((now.value - start.value) / (end.value - start.value)) * 100 : null))
const canvasWidth = computed(() => (scale.value === 'hour' ? 960 : scale.value === 'week' ? 1120 : 1440))
const dateLabel = computed(() => {
  const fmt = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' })
  return scale.value === 'week' ? `${fmt.format(range.value.start)} 起一周` : fmt.format(anchor.value)
})
const scroller = ref<HTMLElement | null>(null)
async function toNow() {
  await nextTick()
  const el = scroller.value
  if (!el || playhead.value === null) return
  const x = (playhead.value / 100) * el.scrollWidth
  if (x < el.scrollLeft + 40 || x > el.scrollLeft + el.clientWidth - 40) el.scrollLeft = Math.max(0, x - el.clientWidth * 0.6)
}
watch([() => timeline.data.value, now, follow], () => { if (follow.value) void toNow() })
function today() { focus.value = Date.now(); now.value = Date.now(); go({ date: undefined }); follow.value = true }
function step(direction: 1 | -1) { go({ date: dayKey(shift(scale.value, anchor.value, direction)) }) }

// ---------------------------------------------------------------- 概况
const summary = computed(() => data.value?.summary)
const runningNames = computed(() => [...new Set(lanes.value.filter(l => l.running).map(l => l.track.name))].join('、'))
const runningClips = computed(() => allClips.value.filter(c => c.state === 'running'))
const isToday = computed(() => scale.value === 'day' && startOfDay(new Date(now.value)).getTime() === startOfDay(anchor.value).getTime())

// ---------------------------------------------------------------- 选中的任务：执行记录与操作
const runs = useRuns(scope, selectedTask)
const actions = useRunActions(scope)
const busy = ref(false), feedback = ref(''), failure = ref('')
const runList = computed(() => runs.data.value?.items ?? [])
const openRun = computed(() => runList.value.find(r => !r.ended_at) ?? null)
const selectedRun = computed(() => (selectedClip.value?.segment.run ? runList.value.find(r => r.id === selectedClip.value!.segment.run!.id) ?? selectedClip.value.segment.run : openRun.value ?? runList.value[0] ?? null))
const seriesBars = computed(() => {
  const points = selectedRun.value?.series ?? []
  const max = Math.max(1, ...points.map(p => p.tokens.total))
  return points.map(p => ({ key: p.start, height: Math.max(6, Math.round((p.tokens.total / max) * 100)), label: `${clock(p.start)} ${compact(p.tokens.total)} token` }))
})
const waited = computed(() => (selectedRun.value?.waits ?? []).reduce((sum, w) => sum + ((w.end ? Date.parse(w.end) : now.value) - Date.parse(w.start)), 0))
const isAgentTask = computed(() => !!selectedInfo.value?.assignee_instance_id)
async function act(run: () => Promise<unknown>, done: string) {
  if (busy.value) return
  busy.value = true; failure.value = ''; feedback.value = ''
  try { await run(); feedback.value = done } catch (error) { failure.value = runError(error) } finally { busy.value = false }
}
const startHuman = () => act(() => actions.start(selectedTask.value, false), '已开始')
const dispatch = () => act(() => actions.start(selectedTask.value, true), '已通过 IM 交给它执行')
const stopRun = (reason: Schema['TaskRunStop']['reason']) => openRun.value && act(() => actions.stop(selectedTask.value, openRun.value!.id, reason), reason === 'paused' ? '已暂停' : '已结束这段')
function terminate() {
  const instanceId = openRun.value?.instance_id ?? selectedInfo.value?.assignee_instance_id, reason = instanceId ? askStopReason() : null
  if (instanceId && reason) void act(() => actions.terminate(selectedTask.value, instanceId, reason), TERMINATED)
}
async function openChat() {
  const instance = selectedInfo.value?.assignee_instance_id ?? selectedRun.value?.instance_id
  if (!instance) return
  await act(async () => {
    const conversation = dataOf(await auth.api.POST('/api/v1/im/conversations', { params: { header }, body: { instance_id: instance } }))
    await router.push({ path: '/im', query: { c: conversation.conversation_id } })
  }, '')
}
watch(selectedTask, () => { feedback.value = ''; failure.value = '' })

// ---------------------------------------------------------------- 拖动计划中的片段：改计划开始与截止（member 及以上）
const drag = ref<{ key: string; x0: number; dx: number; width: number } | null>(null)
function dragStart(event: PointerEvent, clip: Clip) {
  if (!writable.value || clip.state !== 'planned' || event.button !== 0) return
  const canvas = (event.currentTarget as HTMLElement).closest('.tl-canvas') as HTMLElement | null
  drag.value = { key: clip.key, x0: event.clientX, dx: 0, width: canvas?.clientWidth ?? 1 }
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
}
function dragMove(event: PointerEvent) { if (drag.value) drag.value.dx = event.clientX - drag.value.x0 }
async function dragEnd(clip: Clip) {
  const d = drag.value
  drag.value = null
  if (!d || Math.abs(d.dx) < 6) { select(clip); return }
  const step15 = 15 * 60_000
  const delta = Math.round(((d.dx / d.width) * (end.value - start.value)) / step15) * step15
  if (!delta) return
  // 选中拖动的任务：结果（“计划时间已更新”或错误）显示在右侧详情里
  select(clip)
  await act(async () => {
    const id = clip.segment.task_id
    const current = dataOf(await auth.api.GET('/api/v1/tasks/{task_id}', { params: { header, path: { task_id: id } } }))
    const shiftIso = (v: string | null) => (v ? new Date(Date.parse(v) + delta).toISOString() : null)
    const body: Schema['TaskUpdate'] = { version: current.version, planned_start_at: shiftIso(current.planned_start_at) }
    if (current.due_at) body.due_at = shiftIso(current.due_at)
    dataOf(await auth.api.PATCH('/api/v1/tasks/{task_id}', { params: { header: { ...header, 'Idempotency-Key': crypto.randomUUID() }, path: { task_id: id } }, body }))
    await actions.refresh()
  }, '计划时间已更新')
}
const dragShift = (clip: Clip) => (drag.value?.key === clip.key ? `translateX(${drag.value.dx}px)` : undefined)

// 键盘：左右键在片段之间移动
function moveFocus(event: KeyboardEvent) {
  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
  const buttons = [...(scroller.value?.querySelectorAll<HTMLButtonElement>('.tl-clip') ?? [])]
  const index = buttons.indexOf(event.target as HTMLButtonElement)
  if (index < 0) return
  event.preventDefault()
  buttons[Math.min(buttons.length - 1, Math.max(0, index + (event.key === 'ArrowRight' ? 1 : -1)))]?.focus()
}

// ---------------------------------------------------------------- 悬停提示、图例、表格视图、全天概览
const tip = ref<{ clip: Clip; x: number; y: number } | null>(null)
function showTip(event: MouseEvent | FocusEvent, clip: Clip) {
  // 鼠标点过之后的聚焦不弹提示（会挡住下面的轨道）；键盘聚焦才弹
  if (event.type === 'focus' && !(event.currentTarget as HTMLElement).matches(':focus-visible')) return
  const box = (event.currentTarget as HTMLElement).getBoundingClientRect()
  tip.value = { clip, x: Math.min(box.left, window.innerWidth - 300), y: box.bottom + 8 }
}
const hideTip = () => { tip.value = null }
const tipTask = computed(() => (tip.value ? tasks.value.get(tip.value.clip.segment.task_id) : undefined))
const tipWait = computed(() => (tip.value?.clip.segment.run?.waits ?? []).reduce((sum, w) => sum + ((w.end ? Date.parse(w.end) : now.value) - Date.parse(w.start)), 0))
const legend = computed(() => {
  const used = new Map<string, { title: string; color: number }>()
  for (const clip of allClips.value) if (clip.task) used.set(clip.task.project_id, { title: clip.task.project_title, color: clip.color })
  return [...used.values()].sort((a, b) => (a.color || 99) - (b.color || 99))
})
const table = ref(false)
const rows = computed(() => [...allClips.value].sort((a, b) => a.start - b.start))
// 小时刻度：上方是全天概览，方框是现在看的 6 小时，点一下移到那里
const dayRange = computed(() => rangeOf('day', anchor.value))
const dayParams = computed(() => ({ from: dayRange.value.start.toISOString(), to: dayRange.value.end.toISOString(), group: group.value, ...(projectId.value ? { project_id: projectId.value } : {}) }))
const dayTimeline = useTimeline(scope, dayParams, false, computed(() => scale.value === 'hour'))
const overview = computed(() => {
  const d = dayTimeline.data.value, s = dayRange.value.start.getTime(), e = dayRange.value.end.getTime()
  if (!d) return []
  return d.segments.flatMap(seg => {
    const clip = clipOf(seg, d.tasks.find(t => t.id === seg.task_id), s, e, now.value)
    return clip ? [{ key: clip.key, left: clip.left, width: clip.width, color: colors.value.get(clip.task?.project_id ?? '') ?? 0, planned: clip.state === 'planned' || clip.state === 'deadline' }] : []
  })
})
const windowBox = computed(() => {
  const s = dayRange.value.start.getTime(), span = dayRange.value.end.getTime() - s
  return { left: ((start.value - s) / span) * 100, width: ((end.value - start.value) / span) * 100 }
})
function jump(event: MouseEvent) {
  const box = (event.currentTarget as HTMLElement).getBoundingClientRect()
  const s = dayRange.value.start.getTime(), span = dayRange.value.end.getTime() - s
  focus.value = s + ((event.clientX - box.left) / box.width) * span
  follow.value = false
}

// ---------------------------------------------------------------- 显示
const trackKind: Record<Schema['TimelineTrack']['kind'], string> = { instance: '智能体', user: '成员', unassigned: '还没有执行者', project: '项目', former: '已不在工作空间' }
const statusLabel: Record<string, string> = { online: '在线', offline: '离线', pending: '还没连接', disabled: '已停用' }
const initial = (name: string) => [...name.trim()][0]?.toUpperCase() ?? '?'
function clipMeta(clip: Clip) {
  if (clip.state === 'planned') return `计划 ${clock(clip.start)}–${clock(clip.end)}`
  if (clip.state === 'deadline') return `截止 ${clock(clip.end)}`
  const run = clip.segment.run
  const parts = [tokenText(run), duration(clip.end - clip.start)]
  if (clip.state !== 'running' && clip.state !== 'done') parts.unshift(STATE_LABELS[clip.state])
  return parts.filter(Boolean).join(' · ')
}
function clipLabel(clip: Clip) {
  return `${clip.task?.title ?? '任务'}，${STATE_LABELS[clip.state]}，${clip.state === 'deadline' ? `截止 ${clock(clip.end)}` : `${clock(clip.start)} 开始`}`
}
const clipStyle = (clip: Clip & { row: number }) => ({ left: `${clip.left}%`, width: `${clip.width}%`, top: `${PAD + clip.row * ROW}px`, transform: dragShift(clip) })
const laneHeight = (lane: Lane) => `${lane.rows * ROW + PAD + 2}px`
const overdueText = computed(() => { const ms = overdueBy(selectedInfo.value ?? undefined, now.value); return ms === null ? '' : `超时 ${duration(ms)}` })
const toTasks = computed(() => ({ path: '/tasks', query: projectId.value ? { project_id: projectId.value } : {} }))
</script>

<template>
  <section class="tl" aria-label="任务时间轴">
    <div class="tl-toolbar" role="toolbar" aria-label="时间轴设置">
      <div class="segmented" role="group" aria-label="显示方式">
        <RouterLink v-slot="{ navigate }" :to="toTasks" custom><button type="button" aria-pressed="false" @click="navigate">列表</button></RouterLink>
        <RouterLink v-slot="{ navigate }" :to="{ ...toTasks, query: { ...toTasks.query, board: '1' } }" custom><button type="button" aria-pressed="false" @click="navigate">看板</button></RouterLink>
        <button type="button" aria-pressed="true">时间轴</button>
      </div>
      <span class="tl-sep" aria-hidden="true" />
      <button type="button" class="secondary-button" @click="today">今天</button>
      <button type="button" class="shell-icon" :aria-label="scale === 'week' ? '前一周' : '前一天'" @click="step(-1)"><ChevronLeft aria-hidden="true" /></button>
      <button type="button" class="shell-icon" :aria-label="scale === 'week' ? '后一周' : '后一天'" @click="step(1)"><ChevronRight aria-hidden="true" /></button>
      <strong class="tl-date">{{ dateLabel }}</strong>
      <div class="segmented" role="group" aria-label="时间刻度">
        <button v-for="s in SCALES" :key="s.id" type="button" :aria-pressed="scale === s.id" :title="s.hint" @click="go({ scale: s.id === 'day' ? undefined : s.id })">{{ s.label }}</button>
      </div>
      <span class="tl-sep" aria-hidden="true" />
      <label class="inline-select"><span class="sr-only">项目</span><select :value="projectId" aria-label="项目" @change="go({ project: ($event.target as HTMLSelectElement).value || undefined, task: undefined })"><option value="">全部项目</option><option v-for="p in projectItems" :key="p.id" :value="p.id">{{ p.title }}</option></select></label>
      <label class="inline-select"><span class="sr-only">分组</span><select :value="group" aria-label="分组" @change="go({ group: ($event.target as HTMLSelectElement).value === 'project' ? 'project' : undefined })"><option value="executor">按执行者</option><option value="project">按项目</option></select></label>
      <button type="button" class="tl-chip" :aria-pressed="onlyRunning" @click="onlyRunning = !onlyRunning"><span class="tl-dot" aria-hidden="true" />只看执行中</button>
      <button type="button" class="tl-chip" :aria-pressed="onlyOverdue" @click="onlyOverdue = !onlyOverdue"><AlertTriangle :size="14" aria-hidden="true" />超时</button>
      <label class="tl-follow"><input v-model="follow" type="checkbox">跟随当前时间</label>
    </div>

    <p v-if="timeline.isPending.value" class="form-help" role="status">正在加载…</p>
    <p v-else-if="timeline.error.value && !data" class="feedback" role="alert">{{ runError(timeline.error.value) }}</p>
    <template v-else-if="data">
      <section class="tl-summary" :aria-label="isToday ? '今天的概况' : '这段时间的概况'">
        <article class="tl-card"><h2><span class="tl-dot" aria-hidden="true" />正在执行</h2><strong>{{ summary?.running }}</strong><p>{{ runningNames || '现在没有' }}</p></article>
        <article class="tl-card"><h2>{{ isToday ? '今天完成' : '这段时间完成' }}</h2><strong>{{ summary?.done }}</strong><p>{{ projectId ? '这个项目' : '全部项目' }}</p></article>
        <article class="tl-card"><h2>{{ isToday ? '今天消耗的 token' : '这段时间的 token' }}</h2><strong>{{ data.usage.reported ? compact(summary?.tokens ?? 0) : '未上报' }}</strong><p>{{ data.usage.scope === 'project' ? '按时间归属到这个项目的执行段' : '所有智能体，含不在任务里的对话' }}</p></article>
        <article class="tl-card"><h2><AlertTriangle :size="14" aria-hidden="true" />超过截止时间</h2><strong>{{ summary?.overdue }}</strong><p>还没完成的任务</p></article>
      </section>

      <section v-if="runningClips.length" class="tl-mobile-running" aria-label="正在执行">
        <h2><span class="tl-dot" aria-hidden="true" />正在执行 {{ runningClips.length }}<template v-if="data.usage.reported"> · {{ compact(summary?.tokens ?? 0) }} token</template></h2>
        <div class="tl-cards">
          <button v-for="clip in runningClips" :key="clip.key" type="button" class="tl-run-card" :aria-pressed="selectedTask === clip.segment.task_id" @click="select(clip)">
            <strong>{{ clip.task?.title }}</strong>
            <span>{{ clip.segment.run?.executor_name }} · {{ clock(clip.start) }} 开始</span>
            <span class="num">{{ tokenText(clip.segment.run) }}<template v-if="overdueBy(clip.task, now) !== null"> · <span class="tl-warn-text">超时 {{ duration(overdueBy(clip.task, now)!) }}</span></template></span>
          </button>
        </div>
      </section>

      <div v-if="scale === 'hour'" class="tl-overview" role="group" aria-label="全天概览">
        <span class="tl-note">全天</span>
        <button type="button" class="tl-overview-bar" aria-label="全天概览：点一下把 6 小时的窗口移到那里" @click="jump">
          <span v-for="o in overview" :key="o.key" class="tl-overview-seg" :class="[`c-${o.color}`, { 'is-planned': o.planned }]" :style="{ left: `${o.left}%`, width: `${o.width}%` }" />
          <span class="tl-overview-window" :style="{ left: `${windowBox.left}%`, width: `${windowBox.width}%` }" />
        </button>
      </div>
      <p v-if="data.truncated" class="form-help">任务太多，只显示了最近的一部分；可以只看一个项目或缩短时间范围。</p>
      <div class="tl-main">
        <section class="tl-board" aria-label="时间轴">
          <div class="tl-heads">
            <div class="tl-heads-top"><span>{{ group === 'project' ? '项目' : '执行者' }} · {{ lanes.length }} 条轨道</span><span class="num">{{ clock(now) }}</span></div>
            <div v-for="lane in lanes" :key="lane.track.key" class="tl-head" :style="{ height: laneHeight(lane) }">
              <span class="tl-avatar" :class="{ 'is-person': lane.track.kind === 'user' || lane.track.kind === 'unassigned' }" aria-hidden="true">{{ initial(lane.track.name) }}</span>
              <span v-if="lane.running" class="tl-dot" aria-hidden="true" />
              <span class="tl-head-text">
                <strong>{{ lane.track.name }}</strong>
                <span>{{ trackKind[lane.track.kind] }}<template v-if="lane.track.status"> · {{ statusLabel[lane.track.status] ?? lane.track.status }}</template><template v-if="lane.running"> · 执行中</template></span>
                <span v-if="lane.track.kind === 'instance' && lane.tokens" class="num">{{ compact(lane.tokens) }} token</span>
              </span>
            </div>
            <div class="tl-heads-usage"><strong>Token 用量</strong><span>每 {{ data.usage.bucket_minutes >= 60 ? `${data.usage.bucket_minutes / 60} 小时` : `${data.usage.bucket_minutes} 分钟` }}<template v-if="data.usage.scope === 'project'">，这个项目</template></span><span class="num">合计 {{ data.usage.reported ? compact(data.usage.total.total) : '未上报' }}</span></div>
          </div>
          <div ref="scroller" class="tl-scroll" @keydown="moveFocus" @pointermove="dragMove">
            <div class="tl-canvas" :style="{ minWidth: `${canvasWidth}px` }">
              <div class="tl-ruler" aria-hidden="true">
                <template v-for="tick in tickList" :key="tick.at">
                  <span class="tl-tick" :class="{ 'is-major': tick.major }" :style="{ left: `${tick.left}%` }" />
                  <span v-if="tick.label" class="tl-tick-label" :style="{ left: `${tick.left}%` }">{{ tick.label }}</span>
                </template>
              </div>
              <p v-if="!lanes.length" class="tl-empty">{{ onlyRunning || onlyOverdue ? '没有符合筛选条件的任务' : '这段时间没有执行记录，也没有计划中的任务' }}</p>
              <div v-for="lane in lanes" :key="lane.track.key" class="tl-lane" :style="{ height: laneHeight(lane) }" role="group" :aria-label="`${lane.track.name} 的轨道`">
                <span v-for="tick in tickList.filter(t => t.major)" :key="tick.at" class="tl-gridline" :style="{ left: `${tick.left}%` }" aria-hidden="true" />
                <template v-for="clip in lane.clips" :key="clip.key">
                  <span v-if="clip.ghost" class="tl-ghost" :style="{ left: `${clip.ghost.left}%`, width: `${clip.ghost.width}%`, top: `${PAD + clip.row * ROW + 8}px` }" aria-hidden="true">截止 {{ clock(clip.task?.due_at ?? 0) }}</span>
                  <button type="button" class="tl-clip" :class="[`is-${clip.state}`, `c-${clip.color}`, { 'can-drag': writable && clip.state === 'planned', 'is-dragging': drag?.key === clip.key }]" :style="clipStyle(clip)" :aria-label="clipLabel(clip)" :aria-pressed="selectedTask === clip.segment.task_id" @mouseenter="showTip($event, clip)" @mouseleave="hideTip" @focus="showTip($event, clip)" @blur="hideTip" @pointerdown="dragStart($event, clip)" @pointerup="clip.state === 'planned' && drag ? dragEnd(clip) : undefined" @click="clip.state !== 'planned' || !writable ? select(clip) : undefined" @keydown.enter.prevent="select(clip)">
                    <span v-for="(w, i) in clip.waits" :key="i" class="tl-wait" :style="{ left: `${w.left}%`, width: `${w.width}%` }" />
                    <span v-if="clip.overdue" class="tl-over" :style="{ left: `${clip.overdue.left}%` }" />
                    <span class="tl-clip-title">{{ clip.task?.title ?? '任务' }}</span>
                    <span class="tl-clip-meta num">{{ clipMeta(clip) }}</span>
                    <span v-if="clip.state === 'running'" class="tl-live" />
                    <span v-if="clip.state === 'interrupted'" class="tl-fail" aria-hidden="true"><X :size="10" :stroke-width="3.5" /></span>
                  </button>
                </template>
              </div>
              <div class="tl-usage" role="img" :aria-label="data.usage.reported ? `token 用量，合计 ${data.usage.total.total}` : 'token 未上报'">
                <span v-for="bar in usageBars" :key="bar.at" class="tl-bar" :style="{ left: `${bar.left}%`, width: `${bar.width}%`, height: `${bar.height * 0.6}px` }" :title="`${clock(bar.at)} ${compact(bar.total)} token`" />
                <span v-if="!data.usage.reported" class="tl-usage-empty">智能体还没有上报 token 用量（接入包的 usage-report 每分钟上报一次）</span>
              </div>
              <span v-if="playhead !== null" class="tl-future" :style="{ left: `${playhead}%` }" aria-hidden="true" />
              <span v-if="playhead !== null" class="tl-playhead" :style="{ left: `${playhead}%` }" aria-hidden="true"><span>{{ clock(now) }}</span></span>
            </div>
          </div>
        </section>

        <div v-if="tip" class="tl-tip" role="tooltip" :style="{ left: `${tip.x}px`, top: `${tip.y}px` }">
          <strong>{{ tipTask?.title ?? '任务' }}</strong>
          <span class="tl-note">{{ tip.clip.segment.run?.executor_name ?? '计划中' }} · {{ tipTask?.project_title }}</span>
          <span class="tl-tip-row"><span>状态</span><span>{{ STATE_LABELS[tip.clip.state] }}</span></span>
          <span class="tl-tip-row num"><span>时间</span><span>{{ clock(tip.clip.start) }} – {{ tip.clip.state === 'running' ? '现在' : clock(tip.clip.end) }}</span></span>
          <span v-if="tipTask?.due_at" class="tl-tip-row num"><span>截止</span><span :class="{ 'tl-warn-text': overdueBy(tipTask, now) !== null }">{{ clock(tipTask.due_at) }}<template v-if="overdueBy(tipTask, now) !== null">（超时 {{ duration(overdueBy(tipTask, now)!) }}）</template></span></span>
          <span v-if="tip.clip.segment.run" class="tl-tip-row num"><span>token</span><span>{{ tokenText(tip.clip.segment.run) }}</span></span>
          <span v-if="tipWait > 0" class="tl-tip-row num"><span>等待审批</span><span>{{ duration(tipWait) }}</span></span>
        </div>
        <aside class="tl-detail" :class="{ 'is-empty': !selectedInfo }" aria-label="选中的任务">
          <template v-if="selectedInfo">
            <h2><span class="tl-swatch" :class="`c-${colors.get(selectedInfo.project_id) ?? 0}`" aria-hidden="true" />{{ selectedInfo.title }}<button type="button" class="shell-icon tl-close" aria-label="关闭" @click="close"><X aria-hidden="true" /></button></h2>
            <p class="tl-note">{{ selectedInfo.project_title }}<template v-if="selectedClip"> · {{ STATE_LABELS[selectedClip.state] }}</template><template v-if="overdueText"> · <span class="tl-warn-text">{{ overdueText }}</span></template></p>
            <p class="tl-mobile-line num">{{ selectedRun?.executor_name ?? '还没开始' }}<template v-if="selectedRun"> · {{ clock(selectedRun.started_at) }} 开始 · {{ selectedRun.ended_at ? '用时' : '已运行' }} {{ duration((selectedRun.ended_at ? Date.parse(selectedRun.ended_at) : now) - Date.parse(selectedRun.started_at)) }} · {{ tokenText(selectedRun) }}</template></p>
            <dl>
              <dt>执行者</dt><dd>{{ selectedRun?.executor_name ?? (selectedInfo.assignee_instance_id || selectedInfo.assignee_user_id ? lanes.find(l => l.track.id === (selectedInfo!.assignee_instance_id ?? selectedInfo!.assignee_user_id))?.track.name ?? '已指派' : '还没有执行者') }}</dd>
              <template v-if="selectedRun"><dt>开始</dt><dd>{{ clock(selectedRun.started_at) }}</dd><dt>{{ selectedRun.ended_at ? '用时' : '已运行' }}</dt><dd>{{ duration((selectedRun.ended_at ? Date.parse(selectedRun.ended_at) : now) - Date.parse(selectedRun.started_at)) }}</dd></template>
              <template v-else-if="selectedInfo.planned_start_at"><dt>计划开始</dt><dd>{{ clock(selectedInfo.planned_start_at) }}</dd></template>
              <dt>截止</dt><dd>{{ selectedInfo.due_at ? clock(selectedInfo.due_at) : '没有截止时间' }}</dd>
              <template v-if="selectedRun"><dt>token</dt><dd>{{ tokenText(selectedRun) }}<template v-if="selectedRun.tokens_shared">（同一智能体同时有几段在执行，平均分）</template></dd></template>
              <template v-if="waited > 0"><dt>等待审批</dt><dd>{{ duration(waited) }}</dd></template>
            </dl>
            <template v-if="seriesBars.length">
              <p class="tl-note">每 10 分钟的 token（按时间归属：这段时间里它的其他对话也算在内）</p>
              <div class="tl-series" role="img" :aria-label="`每 10 分钟的 token：${seriesBars.map(b => b.label).join('，')}`"><span v-for="bar in seriesBars" :key="bar.key" :style="{ height: `${bar.height}%` }" :title="bar.label" /></div>
            </template>
            <template v-if="runList.length">
              <p class="tl-note">执行记录</p>
              <ul class="tl-runs">
                <li v-for="r in runList.slice(0, 6)" :key="r.id"><span class="num">{{ clock(r.started_at) }}–{{ r.ended_at ? clock(r.ended_at) : '现在' }} · {{ r.end_reason ? STATE_LABELS[r.end_reason] : '执行中' }}</span><span class="num">{{ tokenText(r) }}</span></li>
              </ul>
            </template>
            <div class="tl-actions">
              <RouterLink class="primary-button" :to="{ path: '/tasks', query: { task: selectedInfo.id } }"><ExternalLink :size="16" aria-hidden="true" />打开任务</RouterLink>
              <template v-if="!selectedInfo.done">
                <template v-if="isAgentTask">
                  <button v-if="auth.canManage && !openRun" type="button" class="secondary-button" :disabled="busy" @click="dispatch"><Send :size="16" aria-hidden="true" />交给它执行</button>
                  <button v-if="auth.canManage && openRun" type="button" class="secondary-button" :disabled="busy" @click="stopRun('interrupted')"><Square :size="16" aria-hidden="true" />结束这段</button>
                  <button v-if="auth.canManage && openRun" type="button" class="secondary-button im-danger" :disabled="busy" @click="terminate"><CircleStop :size="16" aria-hidden="true" />终止执行</button>
                </template>
                <template v-else-if="writable">
                  <button v-if="!openRun" type="button" class="secondary-button" :disabled="busy" @click="startHuman"><Play :size="16" aria-hidden="true" />开始</button>
                  <button v-else type="button" class="secondary-button" :disabled="busy" @click="stopRun('paused')"><Pause :size="16" aria-hidden="true" />暂停</button>
                </template>
              </template>
              <button v-if="auth.canManage && (selectedInfo.assignee_instance_id || selectedRun?.instance_id)" type="button" class="secondary-button" :disabled="busy" @click="openChat"><MessageSquare :size="16" aria-hidden="true" />看会话</button>
            </div>
            <p v-if="feedback" class="form-help" role="status">{{ feedback }}</p>
            <p v-if="failure" class="feedback" role="alert">{{ failure }}</p>
          </template>
          <p v-else class="tl-note">点一个片段查看详情。拖动计划中的片段可以改计划时间；键盘用左右键在片段之间移动、回车选中。</p>
        </aside>
      </div>
      <div class="tl-legend" aria-label="图例">
        <span v-for="item in legend" :key="item.title" class="tl-legend-item"><span class="tl-swatch" :class="`c-${item.color}`" aria-hidden="true" />{{ item.color ? item.title : `${item.title}（其他项目）` }}</span>
        <span class="tl-legend-item"><span class="tl-key is-planned" aria-hidden="true" />计划中</span>
        <span class="tl-legend-item"><span class="tl-key is-over" aria-hidden="true" />超过截止时间</span>
        <span class="tl-legend-item"><span class="tl-key is-wait" aria-hidden="true" />等待审批</span>
        <span class="tl-legend-item"><span class="tl-key is-bad" aria-hidden="true" />中断</span>
        <span class="tl-legend-item">≈ 同一智能体同时几段在执行，token 平均分</span>
        <button type="button" class="tl-chip" :aria-pressed="table" @click="table = !table">表格视图</button>
      </div>
      <div v-if="table" class="tl-table-wrap">
        <table class="tl-table">
          <caption class="sr-only">时间轴上的片段</caption>
          <thead><tr><th scope="col">任务</th><th scope="col">项目</th><th scope="col">执行者</th><th scope="col">状态</th><th scope="col">开始</th><th scope="col">结束 / 截止</th><th scope="col">token</th></tr></thead>
          <tbody>
            <tr v-for="row in rows" :key="row.key">
              <td><button type="button" class="link-button" @click="select(row)">{{ row.task?.title ?? '任务' }}</button></td>
              <td>{{ row.task?.project_title }}</td>
              <td>{{ row.segment.run?.executor_name ?? '—' }}</td>
              <td>{{ STATE_LABELS[row.state] }}<template v-if="overdueBy(row.task, now) !== null && row.state === 'running'">（超时）</template></td>
              <td class="num">{{ row.state === 'deadline' ? '—' : clock(row.start) }}</td>
              <td class="num">{{ row.state === 'running' ? '现在' : clock(row.end) }}</td>
              <td class="num">{{ tokenText(row.segment.run) || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>
