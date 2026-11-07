<script setup lang="ts">
import { computed, toRef } from 'vue'
import { useAuthStore } from '../../stores/auth'
import DataState from '../../components/DataState.vue'
import { unique } from '../agents/queries'
import NewsBriefingView from './NewsBriefingView.vue'
import { BRIEFING_SOURCE, BRIEFING_STATUS, newsError, useNewsBriefing, useNewsBriefings } from './api'
import { briefingWhen } from './format'

// 往期：按时间倒序的早报列表（游标分页）；点开看全文。打开的那份写在地址里（?briefing=），通知里的链接直接打开它
const props = defineProps<{ selected: string | null }>()
const emit = defineEmits<{ select: [id: string | null] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const list = useNewsBriefings(scope)
const detail = useNewsBriefing(scope, toRef(props, 'selected'))
const items = computed(() => unique(list.data.value?.pages))
</script>

<template>
  <div class="news-panel">
    <template v-if="selected">
      <button type="button" class="secondary-button news-more" @click="emit('select', null)">← 返回往期列表</button>
      <p v-if="detail.isPending.value" role="status" class="data-state">正在加载早报…</p>
      <div v-else-if="detail.error.value" class="data-state">
        <p role="alert">{{ newsError(detail.error.value) }}</p>
        <button type="button" class="secondary-button" @click="detail.refetch()">重试</button>
      </div>
      <NewsBriefingView v-else-if="detail.data.value" :briefing="detail.data.value" />
    </template>
    <template v-else>
      <DataState :loading="list.isPending.value" :error="list.error.value" :empty="!items.length" empty-text="还没有早报：到了推送时间会自动生成。" :stale="!!list.data.value" @retry="list.refetch()" />
      <ul v-if="items.length" class="news-archive" aria-label="往期早报">
        <li v-for="item in items" :key="item.id">
          <button type="button" class="news-archive-item" @click="emit('select', item.id)">
            <span class="news-meta"><time :datetime="item.local_date">{{ briefingWhen(item) }}</time><span class="badge" :class="`news-status-${item.status}`">{{ BRIEFING_STATUS[item.status] }}</span><span>{{ BRIEFING_SOURCE[item.source] }}</span></span>
            <strong>{{ item.title.trim() || '早报' }}</strong>
            <span v-if="item.excerpt" class="news-archive-excerpt">{{ item.excerpt }}</span>
          </button>
        </li>
      </ul>
      <button v-if="list.hasNextPage.value" type="button" class="secondary-button news-more" :disabled="list.isFetchingNextPage.value" @click="list.fetchNextPage()">{{ list.isFetchingNextPage.value ? '正在加载…' : '加载更多' }}</button>
    </template>
  </div>
</template>
