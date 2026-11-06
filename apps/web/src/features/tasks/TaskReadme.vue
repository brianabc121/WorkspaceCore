<script setup lang="ts">
// 任务详情抽屉的 README（T-0122，方案 9.7）：任务导览，自动部分随任务数据更新。
import { computed } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { useTaskReadme } from '../kb/api'
import ReadmeView from '../kb/ReadmeView.vue'
import DataState from '../../components/DataState.vue'
const props = defineProps<{ taskId: string; writable: boolean }>()
const auth = useAuthStore()
const readme = useTaskReadme(auth.workspaceId!, computed(() => props.taskId))
</script>
<template>
  <DataState :loading="readme.isPending.value" :error="readme.error.value" :empty="false" empty-text="" @retry="readme.refetch()" />
  <ReadmeView v-if="readme.data.value" :readme="readme.data.value" :writable="writable" />
</template>
