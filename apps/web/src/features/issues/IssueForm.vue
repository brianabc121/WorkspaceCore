<script setup lang="ts">
// 新建 Issue（T-0124，方案 8.8）：先选类型，再填模板；从任务的“讨论”里新建时自动关联这个任务。
import { computed, ref, watch } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, errorMessage } from '../../services/api'
import { KIND_LABELS, KINDS, useIssueActions, useIssueTemplates, type IssueDetail, type IssueKind } from './api'

const props = defineProps<{ projectId: string; taskIds?: string[] }>()
const emit = defineEmits<{ created: [issue: IssueDetail]; cancel: [] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const templates = useIssueTemplates(scope), actions = useIssueActions(scope)
const kind = ref<IssueKind | ''>(''), title = ref(''), body = ref(''), labels = ref(''), busy = ref(false), problem = ref('')
const template = (k: IssueKind | '') => templates.data.value?.items.find((t) => t.kind === k)?.body ?? ''
// 换类型（或模板刚加载好）时：正文还是空的或者还是上一次套的模板，就换成这种类型的模板
let applied = ''
watch([kind, () => templates.data.value], () => {
  const next = template(kind.value)
  if (!body.value.trim() || body.value === applied) { body.value = next; applied = next }
})
const labelList = computed(() => [...new Set(labels.value.split(/[,，]/).map((l) => l.trim()).filter(Boolean))].slice(0, 10))
async function submit() {
  if (!kind.value) { problem.value = '先选类型'; return }
  if (!title.value.trim()) { problem.value = '写个标题'; return }
  if (busy.value) return
  busy.value = true; problem.value = ''
  try {
    const created = await actions.create(props.projectId, { title: title.value.trim(), body: body.value, kind: kind.value, labels: labelList.value, task_ids: props.taskIds ?? [], assignee_user_ids: [], assignee_instance_ids: [], use_template: false })
    emit('created', created)
  } catch (cause) {
    problem.value = cause instanceof ApiError && cause.code === 'SECRET_BLOCKED' ? '正文里像是有令牌、私钥一类的密钥，没有保存；去掉之后再提交。' : errorMessage(cause)
  } finally { busy.value = false }
}
</script>

<template>
  <form class="panel form-panel issue-form" aria-label="新建 Issue" novalidate @submit.prevent="submit">
    <fieldset class="issue-kinds">
      <legend>类型</legend>
      <label v-for="k in KINDS" :key="k" class="chip"><input v-model="kind" type="radio" name="issue-kind" :value="k" :disabled="busy"> {{ KIND_LABELS[k] }}</label>
    </fieldset>
    <label class="form-field">标题<input v-model="title" maxlength="200" required :disabled="busy"></label>
    <label class="form-field">正文（Markdown；#编号 引用别的 Issue，@名字 提到人或智能体）<textarea v-model="body" rows="10" maxlength="20000" :disabled="busy"></textarea></label>
    <label class="form-field">标签（用逗号分开，最多 10 个）<input v-model="labels" maxlength="300" :disabled="busy"></label>
    <p v-if="taskIds?.length" class="form-help">会关联到这个任务。</p>
    <div class="form-actions">
      <button class="primary-button" :disabled="busy">开 Issue</button>
      <button type="button" class="secondary-button" :disabled="busy" @click="emit('cancel')">取消</button>
    </div>
    <p v-if="problem" role="alert">{{ problem }}</p>
  </form>
</template>

<style scoped>
.issue-form { display: flex; flex-direction: column; gap: 10px; }
.issue-kinds { display: flex; flex-wrap: wrap; gap: 6px; border: 0; padding: 0; margin: 0; }
.issue-kinds legend { margin-bottom: 4px; }
</style>
