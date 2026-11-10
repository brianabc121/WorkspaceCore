<script setup lang="ts">
import { Blocks, Link2, PlugZap, Server } from '@lucide/vue'
import type { ModuleView } from './viewModel'

// 接入指南（T-0211，方案第 3.5 节）：三种接入方式与外部服务的部署说明。
// 不提供上传安装包、在线升级、清除数据或登记外部应用的按钮；开发细节放在仓库文档里。
defineProps<{ modules: readonly ModuleView[]; canEdit: boolean }>()
defineEmits<{ add: []; pages: [] }>()
</script>

<template>
  <section class="ws-panel apc-guide" aria-label="应用接入指南">
    <h2>应用接入指南</h2>
    <p class="form-help">应用中心统一管理三类能力：按规范开发的功能模块、零代码的页面入口，以及后续开放的外部应用。</p>
    <ol class="apc-guide-list">
      <li>
        <Blocks aria-hidden="true" />
        <div>
          <h3>功能模块</h3>
          <p>由开发者按模块清单规范在代码里注册（后端与前端各一个目录），经过测试、CI 与发布后自动出现在“功能模块”里，owner 再按工作空间启用。开发说明见仓库的 <code>docs/modules/README.md</code>。</p>
          <p class="form-help">应用中心不提供上传安装包、在线升级、卸载或清除数据：停用只隐藏入口、保留数据。</p>
        </div>
      </li>
      <li>
        <Link2 aria-hidden="true" />
        <div>
          <h3>零代码入口</h3>
          <p>直接把外部链接、站内页面、知识库笔记、仪表盘或允许来源的嵌入页放进侧栏。只改导航，不复制或授权任何数据。</p>
          <div class="form-actions"><button v-if="canEdit" type="button" class="primary-button" @click="$emit('add')">添加入口</button><button type="button" class="secondary-button" @click="$emit('pages')">查看自定义页面</button></div>
        </div>
      </li>
      <li>
        <PlugZap aria-hidden="true" />
        <div>
          <h3>外部应用 · 待支持</h3>
          <p>外部应用的接入协议（令牌、回调）还没有开放，目前不能在这里登记或授权外部应用。需要时会作为单独的功能发布。</p>
        </div>
      </li>
    </ol>
    <section id="apc-deploy" class="apc-guide-deploy" aria-label="部署外部服务">
      <h3><Server aria-hidden="true" />部署外部服务</h3>
      <p>有的模块依赖部署级的外部服务（例如新闻早报依赖 TrendRadar）。缺少部署设置时模块仍可以启用，页面会提示未部署；部署由管理员用部署脚本完成（第一次安装或升级时），完成后重启工作台，再在应用详情里点“检测连接”。</p>
      <ul v-if="modules.some(view => !view.item.module.ready)" class="apc-list">
        <li v-for="view in modules.filter(item => !item.item.module.ready)" :key="view.key"><strong>{{ view.name }}</strong><small>缺少：<code v-for="name in view.item.module.missing" :key="name">{{ name }}</code></small></li>
      </ul>
      <p v-else class="form-help">已注册的模块都已满足部署前提。</p>
    </section>
  </section>
</template>
