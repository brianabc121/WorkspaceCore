<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, type Schema } from '../../services/api'
import { formatTime } from '../time'
import NewsSecretInput from './NewsSecretInput.vue'
import { CHANNELS, PLATFORMS, channelName, newsError, newsKey, useNewsActions, useNewsAgents, useNewsSettings, useNewsStatus, type ChannelId, type NewsSettings } from './api'
import { MAX_FEEDS, MAX_PUSH_TIMES, MAX_TOPICS, PUSH_TIME_OPTIONS, buildBody, checkForm, emptyFeed, emptyTopic, fieldErrors, toForm, type NewsForm } from './settingsForm'
import './news.css'

// 设置 → 新闻早报（T-0153，NEWS_PLAN 第 4、7、8 节）：owner 改，admin 只读。
// 保存带 version（乐观并发）；密钥只写不读（只显示“已设置”）。保存后工作台重新生成 TrendRadar 的配置文件，下一次运行生效。
const auth = useAuthStore(), scope = auth.workspaceId!, cache = useQueryClient()
const settings = useNewsSettings(scope), status = useNewsStatus(scope), agents = useNewsAgents(scope), actions = useNewsActions(scope)
const canEdit = computed(() => auth.isOwner)
const form = ref<NewsForm | null>(null), baseVersion = ref(0)
const busy = ref(false), error = ref(''), problems = ref<string[]>([]), notice = ref(''), conflict = ref(false)
const pushResults = ref<Schema['NewsPushResult'][] | null>(null)

function load(value: NewsSettings) { form.value = toForm(value); baseVersion.value = value.version }
// 只在第一次拿到设置时填表；之后别处改了设置（版本变了）只提示，不覆盖正在改的内容
watch(() => settings.data.value, (value) => { if (value && !form.value) load(value) }, { immediate: true })
const current = computed(() => settings.data.value)
const outdated = computed(() => !!current.value && !!form.value && current.value.version !== baseVersion.value)
const choices = computed(() => (current.value?.platform_choices.length ? current.value.platform_choices : PLATFORMS))
const instances = computed(() => agents.data.value ?? [])
const missingInstance = computed(() => !!form.value?.agent_instance_id && !instances.value.some(i => i.id === form.value!.agent_instance_id))
const timezones = (() => { try { return Intl.supportedValuesOf('timeZone') } catch { return ['Asia/Shanghai', 'UTC'] } })()
const secretsSet = (id: ChannelId) => current.value?.channels[id]?.secrets_set ?? []
const ONLINE = computed(() => {
  const s = status.data.value
  if (!s) return ''
  if (s.online === true) return '在线'
  if (s.online === false) return '连不上'
  return !s.deployed ? '这个部署没有安装' : !s.enabled ? '早报没有启用' : '未知'
})

function clearMessages() { error.value = ''; problems.value = []; notice.value = ''; conflict.value = false }
async function reload() {
  clearMessages()
  const result = await settings.refetch()
  if (result.data) { load(result.data); notice.value = '已重新加载最新的设置。' }
  else if (result.error) error.value = newsError(result.error)
}
async function save() {
  if (!form.value || !current.value || busy.value || !canEdit.value) return
  clearMessages()
  const found = checkForm(form.value, secretsSet)
  if (found.length) { error.value = '还有几处要改：'; problems.value = found; return }
  busy.value = true
  try {
    const saved = await actions.saveSettings(buildBody(form.value, baseVersion.value))
    load(saved)
    notice.value = saved.render_error ? '设置已保存，但配置文件没有写成功（见上方说明）。' : '设置已保存：TrendRadar 下一次运行时生效。'
  } catch (cause) {
    conflict.value = cause instanceof ApiError && cause.code === 'VERSION_CONFLICT'
    problems.value = fieldErrors(cause)
    error.value = problems.value.length ? '有几处不符合要求：' : newsError(cause)
  } finally { busy.value = false }
}
async function testPush() {
  if (busy.value) return
  clearMessages(); pushResults.value = null; busy.value = true
  try {
    pushResults.value = await actions.testPush()
    notice.value = pushResults.value.every(r => r.ok) ? '测试推送已发出，去各渠道看看有没有收到。' : '测试推送已发出，有的渠道失败了（见下方结果）。'
  } catch (cause) { error.value = newsError(cause) }
  finally { busy.value = false }
}
async function generate() {
  if (busy.value) return
  clearMessages(); busy.value = true
  try {
    await actions.generate()
    notice.value = '已让智能体开始写早报：写好后在“早报”页查看，并按推送渠道推送。'
  } catch (cause) { error.value = newsError(cause) }
  finally { busy.value = false }
}
function addTime() {
  if (!form.value || form.value.push_times.length >= MAX_PUSH_TIMES) return
  form.value.push_times.push(PUSH_TIME_OPTIONS.find(t => !form.value!.push_times.includes(t)) ?? '07:30')
}
function refreshStatus() { void cache.invalidateQueries({ queryKey: newsKey(auth.user?.id, scope, 'status') }) }
const sourceName = (type: string) => (type === 'rss' ? 'RSS' : '热榜')
</script>

<template>
  <div class="news-settings">
    <p v-if="settings.isPending.value" role="status">正在加载…</p>
    <div v-else-if="settings.error.value && !current" class="data-state">
      <p role="alert">{{ newsError(settings.error.value) }}</p>
      <button type="button" class="secondary-button" @click="settings.refetch()">重试</button>
    </div>
    <template v-else-if="current && form">
      <p v-if="!current.deployed" role="note" class="feedback news-banner">这个部署没有安装 TrendRadar：设置可以保存，但不会抓取新闻，也不会推送。安装方法见部署指南的“新闻早报”一节。</p>
      <p v-if="current.taken_by_other" role="note" class="feedback news-banner">另一个工作空间已经启用了早报（一个部署只能有一个 TrendRadar），这里启用不了。</p>
      <p v-if="current.render_error" role="alert" class="feedback news-banner">配置文件写入失败：{{ current.render_error }}</p>
      <p v-if="!canEdit" role="note" class="feedback news-banner">只有 owner 能修改新闻早报的设置；你看到的是只读视图。</p>

      <section class="panel news-card" aria-label="状态">
        <div class="news-entry-head"><h2>状态</h2><button type="button" class="secondary-button" :disabled="status.isFetching.value" @click="refreshStatus">刷新状态</button></div>
        <p v-if="status.isPending.value" role="status">正在检查 TrendRadar…</p>
        <p v-else-if="status.error.value" role="alert">{{ newsError(status.error.value) }}</p>
        <dl v-else-if="status.data.value" class="agent-facts">
          <div><dt>TrendRadar</dt><dd>{{ ONLINE }}</dd></div>
          <div><dt>最近一次抓到数据</dt><dd>{{ status.data.value.latest_data_date ?? '还没有' }}</dd></div>
          <div><dt>最近一份早报</dt><dd>{{ formatTime(status.data.value.latest_briefing_at) }}</dd></div>
          <div><dt>配置写入时间</dt><dd>{{ formatTime(status.data.value.rendered_at) }}（共写入 {{ current.config_rev }} 次）</dd></div>
        </dl>
        <p v-if="status.data.value?.error" class="form-help">TrendRadar 的说明：{{ status.data.value.error }}</p>
        <p v-if="status.data.value?.render_error && !current.render_error" role="alert" class="form-help">配置文件写入失败：{{ status.data.value.render_error }}</p>
      </section>

      <p v-if="outdated" role="status" class="feedback news-banner">设置刚被别处修改了（例如有人顺手加了排除词）：保存前请先 <button type="button" class="secondary-button" @click="reload">重新加载</button></p>

      <form aria-label="新闻早报设置" novalidate @submit.prevent="save">
        <fieldset :disabled="!canEdit || busy">
          <section class="panel news-card" aria-label="基本">
            <h2>基本</h2>
            <label class="check-field"><input v-model="form.enabled" type="checkbox" />启用新闻早报</label>
            <p class="form-help">关闭时 TrendRadar 不抓取、不推送；早报页仍能看往期。</p>
            <div class="news-grid">
              <label class="form-field"><span>时区</span><input v-model="form.timezone" list="news-timezones" maxlength="64" placeholder="Asia/Shanghai" /></label>
            </div>
            <datalist id="news-timezones"><option v-for="zone in timezones" :key="zone" :value="zone" /></datalist>
            <div class="form-field">
              <span>推送时间（1~3 个，半小时为单位）</span>
              <div class="news-times">
                <template v-for="(_, index) in form.push_times" :key="index">
                  <select v-model="form.push_times[index]" :aria-label="`推送时间 ${index + 1}`"><option v-for="t in PUSH_TIME_OPTIONS" :key="t" :value="t">{{ t }}</option></select>
                  <button v-if="form.push_times.length > 1" type="button" class="secondary-button" :aria-label="`删除推送时间 ${index + 1}`" @click="form.push_times.splice(index, 1)">删除</button>
                </template>
                <button v-if="form.push_times.length < MAX_PUSH_TIMES" type="button" class="secondary-button" @click="addTime">添加推送时间</button>
              </div>
            </div>
            <div class="form-field" role="radiogroup" aria-label="早报方式">
              <span>早报方式</span>
              <label class="check-field"><input v-model="form.briefing_mode" type="radio" value="trendradar" />方案 A：到推送时间由 TrendRadar 汇总、AI 分析后推送</label>
              <label class="check-field"><input v-model="form.briefing_mode" type="radio" value="agent" />方案 B：到推送时间由智能体用新闻工具写早报，工作台再推送</label>
            </div>
            <label v-if="form.briefing_mode === 'agent'" class="form-field"><span>写早报的智能体实例</span>
              <select v-model="form.agent_instance_id">
                <option value="">选择一个实例</option>
                <option v-for="i in instances" :key="i.id" :value="i.id">{{ i.name }}（{{ i.status === 'online' ? '在线' : i.status === 'offline' ? '离线' : '等待接入' }}）</option>
                <option v-if="missingInstance" :value="form.agent_instance_id">已停用或不存在的实例</option>
              </select>
            </label>
            <p v-if="form.briefing_mode === 'agent'" class="form-help">方案 B 会在“每日早报”项目里建任务交给这个实例；90 分钟没写完记为失败并通知 owner。</p>
          </section>

          <section class="panel news-card" aria-label="新闻来源">
            <h2>新闻来源</h2>
            <h3>热榜平台</h3>
            <div class="news-checks" role="group" aria-label="热榜平台">
              <label v-for="p in choices" :key="p.id" class="check-field"><input v-model="form.platforms" type="checkbox" :value="p.id" />{{ p.name }}</label>
            </div>
            <label class="form-field"><span>自建 NewsNow 地址（可不填，留空用公共接口）</span><input v-model="form.newsnow_api_url" type="url" maxlength="500" placeholder="https://newsnow.example.com" /></label>
            <h3>RSS 源</h3>
            <p class="form-help">RSSHub 的订阅也填在这里。ID 用小写字母、数字和连字符（例如 hacker-news）。</p>
            <div v-for="(feed, index) in form.feeds" :key="index" class="news-entry" role="group" :aria-label="`RSS 源 ${index + 1}`">
              <div class="news-entry-head">
                <label class="check-field"><input v-model="feed.enabled" type="checkbox" />启用</label>
                <button type="button" class="secondary-button" :aria-label="`删除 RSS 源 ${index + 1}`" @click="form.feeds.splice(index, 1)">删除</button>
              </div>
              <div class="news-grid">
                <label class="form-field"><span>ID</span><input v-model="feed.id" maxlength="40" placeholder="hacker-news" autocapitalize="off" /></label>
                <label class="form-field"><span>名称</span><input v-model="feed.name" maxlength="60" placeholder="Hacker News" /></label>
                <label class="form-field"><span>地址</span><input v-model="feed.url" type="url" maxlength="500" placeholder="https://hnrss.org/frontpage" /></label>
                <label class="form-field"><span>只看最近几天（0 = 默认 3 天）</span><input v-model.number="feed.max_age_days" type="number" min="0" max="30" /></label>
              </div>
            </div>
            <button v-if="form.feeds.length < MAX_FEEDS" type="button" class="secondary-button" @click="form.feeds.push(emptyFeed())">添加 RSS 源</button>
          </section>

          <section class="panel news-card" aria-label="兴趣与筛选">
            <h2>兴趣与筛选</h2>
            <p class="form-help">关键词任意一个出现就算这个主题，用逗号分隔；<code>/正则/</code> 写法按正则匹配。必含词要同时出现（最多 5 个）。每天最多几条填 0 表示不限。</p>
            <div v-for="(topic, index) in form.topics" :key="index" class="news-entry" role="group" :aria-label="`兴趣主题 ${index + 1}`">
              <div class="news-entry-head">
                <label class="check-field"><input v-model="topic.enabled" type="checkbox" />启用</label>
                <button type="button" class="secondary-button" :aria-label="`删除兴趣主题 ${index + 1}`" @click="form.topics.splice(index, 1)">删除</button>
              </div>
              <div class="news-grid">
                <label class="form-field"><span>名称</span><input v-model="topic.name" maxlength="30" placeholder="人工智能" /></label>
                <label class="form-field"><span>每天最多几条</span><input v-model.number="topic.max_items" type="number" min="0" max="50" /></label>
              </div>
              <label class="form-field"><span>关键词</span><input v-model="topic.keywords" placeholder="AI, 大模型, /GPT-\d/" /></label>
              <label class="form-field"><span>必含词（可不填）</span><input v-model="topic.required" placeholder="例如：发布" /></label>
              <label class="form-field"><span>给 AI 的描述（AI 筛选时用）</span><input v-model="topic.description" maxlength="200" placeholder="关注大模型的新产品与研究进展" /></label>
            </div>
            <button v-if="form.topics.length < MAX_TOPICS" type="button" class="secondary-button" @click="form.topics.push(emptyTopic())">添加兴趣主题</button>
            <label class="form-field"><span>全局排除词（标题里有这些词的都不要；每行一个或用逗号分隔）</span><textarea v-model="form.exclusions" rows="3" placeholder="广告&#10;带货"></textarea></label>
            <div class="form-field" role="radiogroup" aria-label="筛选方式">
              <span>筛选方式</span>
              <label class="check-field"><input v-model="form.filter_method" type="radio" value="keyword" />关键词：按上面的兴趣主题匹配</label>
              <label class="check-field"><input v-model="form.filter_method" type="radio" value="ai" />AI：让大模型按主题描述筛选（失败时自动退回关键词）</label>
            </div>
          </section>

          <section class="panel news-card" aria-label="大模型">
            <h2>大模型</h2>
            <p class="form-help">模型用 LiteLLM 的写法，例如 <code>deepseek/deepseek-chat</code>、<code>openai/gpt-4o-mini</code>、<code>ollama/qwen2.5</code>。Ollama 这类不需要 Key 的可以不填 Key。</p>
            <div class="news-grid">
              <label class="form-field"><span>模型</span><input v-model="form.ai_model" maxlength="100" placeholder="deepseek/deepseek-chat" autocapitalize="off" /></label>
              <label class="form-field"><span>接口地址（可不填）</span><input v-model="form.ai_api_base" type="url" maxlength="500" placeholder="https://api.deepseek.com" /></label>
            </div>
            <NewsSecretInput v-model="form.ai_api_key" label="API Key" :is-set="current.ai_api_key_set" placeholder="sk-…" :disabled="!canEdit || busy" />
            <div class="news-checks">
              <label class="check-field"><input v-model="form.ai_analysis" type="checkbox" />AI 分析（早报里加一段分析）</label>
              <label class="check-field"><input v-model="form.ai_translation" type="checkbox" />翻译（把外文标题译成中文）</label>
            </div>
          </section>

          <section class="panel news-card" aria-label="推送渠道">
            <h2>推送渠道</h2>
            <p class="form-help">凭证加密保存，页面上只显示“已设置”。早报总会推到工作台（早报页与站内通知），这里选的是额外推到哪里。</p>
            <details v-for="def in CHANNELS" :key="def.id" class="news-channel" :open="form.channels[def.id].enabled || undefined">
              <summary>{{ def.name }}<span class="badge">{{ form.channels[def.id].enabled ? '已启用' : '未启用' }}</span></summary>
              <div role="group" :aria-label="`推送渠道：${def.name}`">
                <p class="form-help">{{ def.help }}</p>
                <label class="check-field"><input v-model="form.channels[def.id].enabled" type="checkbox" />启用{{ def.name }}</label>
                <div class="news-grid">
                  <template v-for="field in def.plain" :key="field.key">
                    <label v-if="field.key === 'msg_type'" class="form-field"><span>{{ field.label }}</span>
                      <select v-model="form.channels[def.id].msg_type"><option value="markdown">Markdown</option><option value="text">文本</option></select>
                    </label>
                    <label v-else class="form-field"><span>{{ field.label }}</span><input v-model="form.channels[def.id][field.key]" :inputmode="field.key === 'smtp_port' ? 'numeric' : undefined" :placeholder="field.placeholder" /></label>
                  </template>
                </div>
                <NewsSecretInput v-for="field in def.secrets" :key="field.key" v-model="form.channels[def.id].secrets[field.key]" :label="field.label" :is-set="secretsSet(def.id).includes(field.key)" :placeholder="field.placeholder" :disabled="!canEdit || busy" />
              </div>
            </details>
          </section>
        </fieldset>

        <div v-if="canEdit" class="panel news-savebar">
          <button type="submit" class="primary-button" :disabled="busy">{{ busy ? '正在保存…' : '保存设置' }}</button>
          <button v-if="conflict || outdated" type="button" class="secondary-button" :disabled="busy" @click="reload">重新加载</button>
          <span v-if="conflict" class="form-help">重新加载会丢掉这里没保存的修改。</span>
        </div>
      </form>

      <div v-if="error" role="alert" class="feedback news-banner">
        <p>{{ error }}</p>
        <ul v-if="problems.length" class="news-problems"><li v-for="p in problems" :key="p">{{ p }}</li></ul>
      </div>
      <p v-if="notice" role="status" class="feedback news-banner">{{ notice }}</p>

      <section v-if="canEdit" class="panel news-card" aria-label="试一试">
        <h2>试一试</h2>
        <p class="form-help">测试推送会发到所有已保存并启用的渠道（先保存设置）。</p>
        <div class="form-actions">
          <button type="button" class="secondary-button" :disabled="busy" @click="testPush">发一条测试推送</button>
          <button v-if="current.briefing_mode === 'agent'" type="button" class="secondary-button" :disabled="busy" @click="generate">现在生成一份</button>
        </div>
        <ul v-if="pushResults" class="news-results" aria-label="测试推送结果">
          <li v-for="r in pushResults" :key="r.channel" :class="r.ok ? 'is-ok' : 'is-failed'">{{ channelName(r.channel) }}：{{ r.ok ? '成功' : '失败' }}{{ r.detail ? `（${r.detail}）` : '' }}</li>
        </ul>
      </section>

      <section class="panel news-card" aria-label="学到的偏好">
        <h2>学到的偏好</h2>
        <p class="form-help">按最近 60 天的“收藏”和“不感兴趣”计算：每个主题的分数 = 收藏数 − 不感兴趣数。分数高的主题排在前面、每天多推几条（最多 +10），分数低的少推（最少 3 条）。每天 03:10 重新计算一次。最近一次计算：{{ formatTime(current.learned.computed_at) }}。</p>
        <p v-if="!current.learned.topics.length" class="card-state">还没有可以调整的主题。</p>
        <div v-else class="table-scroll">
          <table class="news-table">
            <thead><tr><th scope="col">主题</th><th scope="col" class="is-num">分数</th><th scope="col" class="is-num">设置的条数</th><th scope="col" class="is-num">实际推送条数</th></tr></thead>
            <tbody>
              <tr v-for="t in current.learned.topics" :key="t.name">
                <th scope="row">{{ t.name }}</th>
                <td class="is-num">{{ t.score > 0 ? `+${t.score}` : t.score }}</td>
                <td class="is-num">{{ t.max_items || '不限' }}</td>
                <td class="is-num">{{ t.adjusted_max_items || '不限' }}{{ t.adjusted_max_items > t.max_items && t.max_items ? '（上调）' : t.adjusted_max_items < t.max_items ? '（下调）' : '' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <template v-if="current.learned.source_suggestions.length">
          <h3>可以考虑关掉的来源</h3>
          <ul class="news-results">
            <li v-for="s in current.learned.source_suggestions" :key="`${s.source_type}:${s.source_id}`">「{{ s.source_name || s.source_id }}」（{{ sourceName(s.source_type) }}）被标了 {{ s.dismisses }} 次不感兴趣、{{ s.favorites }} 次收藏：可以在“新闻来源”里关掉它（不会自动关）。</li>
          </ul>
        </template>
      </section>
    </template>
  </div>
</template>
