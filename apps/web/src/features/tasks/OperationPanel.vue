<script setup lang="ts">
import { useTaskOperations } from '../../stores/taskOperations'
import { errorMessage } from '../../services/api'
const store = useTaskOperations()
defineProps<{ writable: boolean }>()
defineEmits<{ retry: []; resubmit: []; reload: [] }>()
</script>
<template>
  <section v-if="store.current" class="operation-panel" aria-label="任务操作结果">
    <p v-if="store.current.status === 'pending'" role="status">正在处理任务操作…</p>
    <p v-else-if="store.current.status === 'succeeded'" role="status">任务操作已完成</p>
    <p v-else role="alert">{{ errorMessage(store.current.error) }}</p>
    <p v-if="store.current.status === 'unknown'" class="form-help">保留原提交内容，查询确认前不能新建另一个操作。可关闭抽屉后继续查询；若刷新页面，请先核对任务列表，避免重复新建。</p>
    <button v-if="store.current.status === 'unknown'" class="secondary-button" :disabled="!writable" @click="$emit('retry')">查询原请求结果</button>
    <button v-if="store.current.status === 'not_applied'" class="secondary-button" :disabled="!writable" @click="$emit('resubmit')">确认重新提交</button>
    <button v-if="store.current.status === 'conflict'" class="secondary-button" @click="$emit('reload')">重新加载任务</button>
  </section>
</template>
