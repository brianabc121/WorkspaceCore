<script setup lang="ts">
import { computed } from 'vue'
import { CalendarClock, Clock, FolderGit2, Info, Layers, Radio, RefreshCw, ShieldAlert, ShieldCheck, TriangleAlert } from '@lucide/vue'
import type { Schema } from '../../services/api'
import ManagementSection from '../../components/management/ManagementSection.vue'
import SyncNotice from '../../components/SyncNotice.vue'
import { formatTime } from '../time'
import type { Tone } from './permissions'

// 连接状态条与同步状态（T-0192，效果图 03 左上）：状态、最近核验时间、上次同步都来自接口；
// “权限核验”卡只数真实的核验结果。测试连接与手动同步是两个独立的写操作，刷新状态在页头、只读。
const props = defineProps<{
  item: Schema['IntegrationOut']
  status: { text: string; tone: Tone }
  summary: { title: string; detail: string; tone: Tone }
  busy: boolean
  locked: boolean
}>()
defineEmits<{ test: []; sync: [] }>()
// 名称里已经有 GitHub 就不再重复写“GitHub ·”
const showProvider = computed(() => !/github/i.test(props.item.name))
const unauthorized = computed(() => props.item.status === 'unauthorized' || props.item.sync.status === 'unauthorized')
const syncBlocked = computed(() => props.busy || props.locked || props.item.status === 'disabled' || props.item.sync.reason === 'rate_limited' || unauthorized.value)
// 上次同步：大字是时间，小字是日期（formatTime 的格式是“YYYY/MM/DD HH:mm”）
const lastSync = computed(() => {
  if (!props.item.sync.as_of) return { main: '—', sub: '尚未同步' }
  const [date, time] = formatTime(props.item.sync.as_of).split(' ')
  return time ? { main: time, sub: date ?? '' } : { main: date ?? '—', sub: '' }
})
const repoCount = computed(() => props.item.repositories?.length ?? 0)
// 同步正常时“上次同步”卡已经说明了；过期、限流、不可用、失权时才显示同步提示（限流带预计恢复时间）
const syncTone = computed(() => {
  const sync = props.item.sync
  if (sync.status === 'unauthorized') return 'red'
  if (sync.status !== 'ok' || sync.reason) return 'amber'
  return ''
})
</script>

<template>
  <section class="ig-overview integration-card" aria-label="GitHub 连接与同步">
    <div class="ws-panel ig-bar">
      <span class="ig-name"><FolderGit2 aria-hidden="true" /><span v-if="showProvider" class="ig-provider">GitHub<span aria-hidden="true"> · </span></span><h2>{{ item.name }}</h2></span>
      <span class="asv-state" :class="`tone-${status.tone}`">{{ status.text }}</span>
      <span class="ig-fact"><Layers aria-hidden="true" />API 只读同步</span>
      <span class="ig-fact"><CalendarClock aria-hidden="true" />最近核验
        <time v-if="item.last_tested_at" :datetime="item.last_tested_at">{{ formatTime(item.last_tested_at) }}</time><template v-else>尚未测试</template>
      </span>
    </div>
    <ManagementSection title="同步状态">
      <div role="group" aria-label="同步状态概览">
        <dl class="ag-tiles ig-tiles">
          <div class="ag-tile"><dt><Clock aria-hidden="true" />上次同步</dt><dd><time v-if="item.sync.as_of" :datetime="item.sync.as_of">{{ lastSync.main }}</time><template v-else>{{ lastSync.main }}</template><small>{{ lastSync.sub }}</small></dd></div>
          <div class="ag-tile tone-violet"><dt><Layers aria-hidden="true" />已选择仓库</dt><dd>{{ repoCount }}</dd></div>
          <div class="ag-tile ig-tile-text" :class="`tone-${summary.tone}`">
            <dt><ShieldAlert v-if="unauthorized" aria-hidden="true" /><TriangleAlert v-else-if="summary.tone === 'amber'" aria-hidden="true" /><ShieldCheck v-else aria-hidden="true" />权限核验</dt>
            <dd>{{ summary.title }}<small v-if="summary.detail">{{ summary.detail }}</small></dd>
          </div>
        </dl>
      </div>
      <div v-if="syncTone" class="ig-sync-notice" :class="`tone-${syncTone}`"><SyncNotice :sync="item.sync" provider="github" /></div>
      <div class="ig-sync-foot">
        <p class="ig-note"><Info aria-hidden="true" />GitHub V1 使用周期同步，不支持 Webhook。</p>
        <div class="ig-sync-actions">
          <button type="button" class="secondary-button" :disabled="busy || locked" @click="$emit('test')"><Radio aria-hidden="true" />测试连接</button>
          <button type="button" class="primary-button" :disabled="syncBlocked" @click="$emit('sync')"><RefreshCw aria-hidden="true" />手动同步</button>
        </div>
      </div>
    </ManagementSection>
  </section>
</template>
