<script setup lang="ts">
import PlaceholderCard from '../components/PlaceholderCard.vue'
const modules = [
  { title: '任务与项目', description: '未来连接 Vikunja，集中查看任务与项目进度。' },
  { title: '日历与知识', description: '未来管理本地日程，并收藏 AFFiNE 文档链接。' },
  { title: '开发与运维', description: '未来查看 GitHub 动态和服务健康摘要。' },
]
</script>
<template>
  <div class="page-heading"><h1>总览</h1><p>让工作、知识与工具，在一个空间相遇。</p></div>
  <section class="welcome-panel panel">
    <span class="badge">欢迎来到 My Workspace</span>
    <h2>从一个清晰的工作空间开始</h2>
    <p>这里是平台的基础布局。业务模块将按开发计划逐步开放，当前未连接任何外部服务。</p>
    <RouterLink class="primary-link" to="/settings">查看设置入口 <span aria-hidden="true">→</span></RouterLink>
  </section>
  <div class="section-heading"><h2>工作台模块</h2><span>后续阶段逐步开放</span></div>
  <div class="module-grid"><PlaceholderCard v-for="module in modules" :key="module.title" v-bind="module" /></div>
</template>
