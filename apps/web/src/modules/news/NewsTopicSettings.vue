<script setup lang="ts">
import { Ban, Plus, Tag, Trash } from '@lucide/vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import { MAX_TOPICS, emptyTopic, type FormPatch, type NewsForm, type TopicDraft } from './settingsForm'

// “兴趣主题”标签（T-0195）：每个主题的关键词、必含词、每天条数与给 AI 的描述，以及全局排除词。
// 只发出对草稿的修改，不保存。
const props = defineProps<{ form: NewsForm; canEdit: boolean; disabled: boolean }>()
const emit = defineEmits<{ patch: [FormPatch] }>()
const target = (event: Event) => event.target as HTMLInputElement

function setTopic(index: number, change: Partial<TopicDraft>) {
  emit('patch', { topics: props.form.topics.map((t, i) => (i === index ? { ...t, ...change } : t)) })
}
function addTopic() { if (props.form.topics.length < MAX_TOPICS) emit('patch', { topics: [...props.form.topics, emptyTopic()] }) }
function removeTopic(index: number) { emit('patch', { topics: props.form.topics.filter((_, i) => i !== index) }) }
</script>

<template>
  <ManagementSection title="兴趣主题" description="关键词任意一个出现就算这个主题，用逗号分隔；/正则/ 写法按正则匹配。必含词要同时出现（最多 5 个）。每天最多几条填 0 表示不限。">
    <template #title><span class="nws-title"><Tag aria-hidden="true" />兴趣主题<span class="nws-count">{{ form.topics.length }} / {{ MAX_TOPICS }}</span></span></template>
    <template v-if="canEdit && form.topics.length < MAX_TOPICS" #actions>
      <button type="button" class="secondary-button" :disabled="disabled" @click="addTopic"><Plus aria-hidden="true" />添加兴趣主题</button>
    </template>
    <p v-if="!form.topics.length" class="asv-sub">还没有兴趣主题：早报会按热度推送，不按主题筛选。</p>
    <div v-else class="nws-topics">
      <fieldset v-for="(topic, index) in form.topics" :key="index" class="nws-fieldset nws-topic" :class="{ 'is-off': !topic.enabled }" :disabled="disabled" :aria-label="`兴趣主题 ${index + 1}`">
        <div class="nws-entry-head">
          <label class="check-field"><input type="checkbox" :checked="topic.enabled" @change="setTopic(index, { enabled: target($event).checked })" />启用</label>
          <button v-if="canEdit" type="button" class="nws-icon-button" :aria-label="`删除兴趣主题 ${index + 1}`" @click="removeTopic(index)"><Trash aria-hidden="true" /></button>
        </div>
        <div class="nws-grid">
          <label class="form-field"><span>名称</span><input :value="topic.name" maxlength="30" placeholder="人工智能" @input="setTopic(index, { name: target($event).value })" /></label>
          <label class="form-field"><span>每天最多几条</span><input :value="topic.max_items" type="number" min="0" max="50" @input="setTopic(index, { max_items: Number(target($event).value) || 0 })" /></label>
        </div>
        <label class="form-field"><span>关键词</span><input :value="topic.keywords" placeholder="AI, 大模型, /GPT-\d/" @input="setTopic(index, { keywords: target($event).value })" /></label>
        <label class="form-field"><span>必含词（可不填）</span><input :value="topic.required" placeholder="例如：发布" @input="setTopic(index, { required: target($event).value })" /></label>
        <label class="form-field"><span>给 AI 的描述（AI 筛选时用）</span><input :value="topic.description" maxlength="200" placeholder="关注大模型的新产品与研究进展" @input="setTopic(index, { description: target($event).value })" /></label>
      </fieldset>
    </div>
  </ManagementSection>

  <ManagementSection title="全局排除词" description="标题里有这些词的条目，所有主题都不要。成员在早报页标“不感兴趣”时，owner 也可以顺手加一个词。">
    <template #title><span class="nws-title"><Ban aria-hidden="true" />全局排除词</span></template>
    <fieldset class="nws-fieldset" :disabled="disabled">
      <label class="form-field nws-wide-field"><span>全局排除词（每行一个，或用逗号分隔）</span>
        <textarea :value="form.exclusions" rows="4" placeholder="广告&#10;带货" @input="emit('patch', { exclusions: (target($event) as unknown as HTMLTextAreaElement).value })"></textarea>
      </label>
    </fieldset>
  </ManagementSection>
</template>
