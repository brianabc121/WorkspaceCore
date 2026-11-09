<script setup lang="ts">
import { computed } from 'vue'
import { FolderKanban } from '@lucide/vue'
import { errorMessage } from '../../services/api'
import { relativeTime } from '../../widgets/home/format'
import { useProjectActivity } from './projectQueries'

// 项目页“最近动态”（T-0168，规范第 5 节）：只用真实的任务动态；读取失败只在这里提示，不影响项目卡片。
// 名字、任务标题、说明来自人或智能体，按纯文本显示
const props = defineProps<{ scope: string }>()
const query = useProjectActivity(props.scope)
const items = computed(() => query.data.value?.pages.flatMap((p) => p.items) ?? [])
const verbs: Record<string, string> = { opened: '把任务设为可自由接取', claimed: '领取了', released: '放弃了', completed: '完成了', reopened: '打回了', reviewed: '确认了结果', blocked: '标记阻塞', unblocked: '解除了阻塞' }
</script>

<template>
  <section class="ws-panel project-activity" aria-label="最近动态">
    <div class="ws-panel-body">
      <h2 class="ws-panel-title">最近动态</h2>
      <p v-if="query.isPending.value" role="status" class="ws-state-line">正在读取动态…</p>
      <p v-else-if="query.error.value && !items.length" role="alert" class="ws-state-line">动态暂时读不到：{{ errorMessage(query.error.value) }} <button type="button" class="link-button" @click="query.refetch()">重试</button></p>
      <p v-else-if="!items.length" class="ws-state-line">还没有任务动态。领取、完成、打回、标记阻塞都会记在这里。</p>
      <ol v-else class="activity-list">
        <li v-for="item in items" :key="item.id">
          <span class="activity-dot" aria-hidden="true"></span>
          <p class="activity-text"><strong>{{ item.actor_name || '系统' }}</strong> {{ verbs[item.kind] ?? item.kind }} <RouterLink :to="{ path: '/tasks', query: { task: item.task_id } }">{{ item.task_title }}</RouterLink><template v-if="item.note && (item.kind === 'blocked' || item.kind === 'reopened')">：{{ item.note }}</template></p>
          <RouterLink class="activity-project" :to="`/projects/${item.project_id}`"><FolderKanban aria-hidden="true" />{{ item.project_title }}</RouterLink>
          <time class="activity-time" :datetime="item.created_at">{{ relativeTime(item.created_at) }}</time>
        </li>
      </ol>
      <button v-if="query.hasNextPage.value" type="button" class="secondary-button" :disabled="query.isFetching.value" @click="query.fetchNextPage()">查看更早的动态</button>
    </div>
  </section>
</template>
