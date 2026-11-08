<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import DataState from '../../components/DataState.vue'
import NewsItems from './NewsItems.vue'
import { PLATFORMS, useNewsHot, type PlatformId } from './api'

// 热榜：最新一批，可以只看一个平台
const auth = useAuthStore()
const platform = ref<PlatformId | ''>('')
const hot = useNewsHot(auth.workspaceId!, platform)
</script>

<template>
  <section class="panel news-card" aria-label="热榜">
    <form class="news-filters" @submit.prevent="hot.refetch()">
      <label class="form-field"><span>平台</span><select v-model="platform"><option value="">全部平台</option><option v-for="p in PLATFORMS" :key="p.id" :value="p.id">{{ p.name }}</option></select></label>
      <button type="submit" class="secondary-button" :disabled="hot.isFetching.value">{{ hot.isFetching.value ? '正在刷新…' : '刷新' }}</button>
    </form>
    <DataState :loading="hot.isPending.value" :error="hot.error.value" :empty="false" empty-text="" :stale="!!hot.data.value" @retry="hot.refetch()" />
    <NewsItems v-if="hot.data.value" :items="hot.data.value.items" :message="hot.data.value.message" :unavailable="hot.data.value.unavailable" empty-text="还没有热榜数据（TrendRadar 每 30 分钟抓取一次）。" label="热榜条目" />
  </section>
</template>
