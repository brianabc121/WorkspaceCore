<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { formatTime } from '../time'
import { RUN_STATUS, seconds, useRunnerRun, useRunnerRuns, type RunFilter } from './api'

// 运行记录（RUNNER_PLAN 第 11 节）：命令（脱敏）、智能体、环境、退出码、耗时、输出。
// 列表只带输出的最后 16 KB；点“完整输出”再取（最多 1 MB，已脱敏）。输出是程序写的，只当数据显示。
const props = defineProps<{ instances: Schema['RunnerInstanceUsage'][]; environmentId?: string }>()
defineEmits<{ clear: [] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const filter = ref<RunFilter>({ environment_id: props.environmentId })
watch(() => props.environmentId, (id) => { filter.value = { ...filter.value, environment_id: id } })
const runs = useRunnerRuns(scope, filter)
const items = computed(() => runs.data.value?.items ?? [])
const opened = ref<string | null>(null)
const full = useRunnerRun(scope, opened)
</script>

<template>
  <section class="panel runner-card" aria-label="筛选运行记录">
    <form class="runner-filters" @submit.prevent>
      <label class="form-field"><span>智能体</span><select v-model="filter.instance_id"><option :value="undefined">全部</option><option v-for="i in instances" :key="i.instance_id" :value="i.instance_id">{{ i.name }}</option></select></label>
      <p v-if="filter.environment_id" class="form-help">只看一个环境的运行 <button type="button" class="secondary-button" @click="filter.environment_id = undefined; $emit('clear')">看全部</button></p>
    </form>
  </section>
  <p v-if="runs.isPending.value" role="status">正在加载运行记录…</p>
  <p v-else-if="runs.error.value" role="alert">加载失败，请稍后重试。</p>
  <p v-else-if="!items.length" class="panel empty-state">还没有运行记录。</p>
  <ul v-else class="runner-list" aria-label="运行记录">
    <li v-for="run in items" :key="run.run_id">
      <article class="panel runner-card" :aria-label="`运行：${run.command}`">
        <div class="section-heading">
          <h3><code class="runner-command">{{ run.command }}</code> <span class="badge">{{ RUN_STATUS[run.status] }}</span><span v-if="run.exit_code !== null" class="badge">退出码 {{ run.exit_code }}</span></h3>
          <button type="button" class="secondary-button" @click="opened = opened === run.run_id ? null : run.run_id">{{ opened === run.run_id ? '收起' : '完整输出' }}</button>
        </div>
        <p class="form-help">{{ run.instance ?? '已删除的实例' }} · {{ run.started_at ? formatTime(run.started_at) : '还没开始' }} · 耗时 {{ seconds(run.seconds) }} · 时限 {{ seconds(run.timeout_seconds) }}</p>
        <template v-if="opened === run.run_id">
          <p v-if="full.isPending.value" role="status">正在加载输出…</p>
          <p v-else-if="full.error.value" role="alert">加载失败，请稍后重试。</p>
          <pre v-else class="runner-output" aria-label="完整输出">{{ full.data.value?.untrusted.output || '（没有输出）' }}</pre>
          <p v-if="full.data.value?.output_truncated" class="form-help">输出超过 1 MB：保留了开头与结尾。</p>
        </template>
        <pre v-else-if="run.untrusted.output" class="runner-output runner-tail" aria-label="输出的结尾">{{ run.untrusted.output.slice(-2000) }}</pre>
      </article>
    </li>
  </ul>
</template>
