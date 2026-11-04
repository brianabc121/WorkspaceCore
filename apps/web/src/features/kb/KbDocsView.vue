<script setup lang="ts">
import { useAuthStore } from '../../stores/auth'
import KnowledgeWorkspace from '../knowledge/KnowledgeWorkspace.vue'
import KbMobileHeader from './KbMobileHeader.vue'

// 外部文档（T-0099）：原来知识库页的 AFFiNE 文档收藏移到这里，功能不变
const auth = useAuthStore()
</script>

<template>
  <div class="kb-view">
    <main class="kb-main">
      <KbMobileHeader title="外部文档" back="/knowledge" />
      <div class="kb-page kb-docs">
        <div>
          <h1 class="kb-title">外部文档</h1>
          <p class="kb-meta">收藏 AFFiNE 文档链接，关联项目与任务。工作台只保存书签元数据，不抓取文档正文；要写在工作台里的内容请用笔记。</p>
        </div>
        <KnowledgeWorkspace v-if="auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" />
      </div>
    </main>
  </div>
</template>
