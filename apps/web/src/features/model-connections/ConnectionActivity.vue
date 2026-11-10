<script setup lang="ts">
import { computed } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { formatTime } from '../time'
import { ACCESS_RESULT, actionLabel, mcError, useActivity, type Connection } from './api'

// 活动记录（契约第 2 节 GET activity）：创建 / 配置 / 更换 Key / 授权 / 取用 / 应用事件的脱敏投影，按时间倒序分页。
const props = defineProps<{ connection: Connection }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const activity = useActivity(scope, () => props.connection.id)
const items = computed(() => [...new Map((activity.data.value?.pages ?? []).flatMap(p => p.items).map(a => [a.id, a])).values()])
</script>

<template>
  <section class="mc-tabpanel" aria-label="活动记录">
    <p v-if="activity.isPending.value" role="status" class="ws-state-line">正在加载活动记录…</p>
    <div v-else-if="activity.error.value && !items.length" class="ws-state-line"><p role="alert">{{ mcError(activity.error.value) }}</p><button type="button" class="secondary-button" @click="activity.refetch()">重试</button></div>
    <p v-else-if="!items.length" class="ws-state-line">还没有活动记录。</p>
    <ol v-else class="mc-activity">
      <li v-for="a in items" :key="a.id">
        <time :datetime="a.at">{{ formatTime(a.at) }}</time>
        <span class="mc-activity-what">{{ actionLabel(a.action) }}<template v-if="a.instance_name"> · {{ a.instance_name }}</template><template v-if="a.value_version !== null"> · Key 第 {{ a.value_version }} 版</template></span>
        <span v-if="a.kind === 'access' && a.result" class="asv-state" :class="`tone-${ACCESS_RESULT[a.result]?.tone ?? 'muted'}`">{{ ACCESS_RESULT[a.result]?.label ?? a.result }}</span>
      </li>
    </ol>
    <footer v-if="items.length" class="asv-foot ag-foot">
      <span>已显示 {{ items.length }} 条<template v-if="activity.error.value"> · 刷新失败，显示的是上次取回的记录</template></span>
      <button v-if="activity.hasNextPage.value" type="button" class="secondary-button" :disabled="activity.isFetchingNextPage.value" @click="activity.fetchNextPage()">加载更多记录</button>
    </footer>
  </section>
</template>
