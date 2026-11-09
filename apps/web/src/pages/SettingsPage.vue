<script setup lang="ts">
import { computed } from 'vue'
import { ArrowRight, ArrowUpDown, Layers, Lock, PanelLeft, Users } from '@lucide/vue'
import { useAuthStore } from '../stores/auth'
import ManagementPage from '../components/management/ManagementPage.vue'
import ManagementSection from '../components/management/ManagementSection.vue'
import { ROLE_NAMES, roleName } from '../components/management/roles'
import ProfileForm from '../features/ProfileForm.vue'
import WorkspaceForm from '../features/WorkspaceForm.vue'
import MembersPanel from '../features/MembersPanel.vue'
import '../features/settings-page.css'

// 设置（T-0189，效果图 02）：个人资料与工作空间各自保存；成员与角色由 owner / admin 管理（规则在服务端）；
// 右侧快捷入口只链接到已有页面，不在这里重复实现安全、侧栏、应用中心与首页布局
const auth = useAuthStore()
const shortcuts = computed(() => [
  { icon: Lock, title: '密码与登录会话', text: '修改登录密码，查看与撤销自己的登录会话。', to: '/settings/security', link: '安全设置' },
  ...(auth.canManage ? [
    { icon: PanelLeft, title: '侧栏分组与自定义页面', text: '配置侧栏分组与自定义页面。', to: '/settings/navigation', link: '侧栏与页面' },
    { icon: Layers, title: '功能启停', text: '按工作空间启用或停用功能模块。', to: '/settings/modules', link: '应用中心' },
  ] : []),
  { icon: ArrowUpDown, title: '首页布局', text: '导入或导出首页布局配置。', to: '/#dashboard-tools', link: '导入 / 导出' },
])
const ROLE_HELP = [
  { role: 'owner', text: '工作空间的最高权限，可管理管理员、成员与访客，以及工作空间的整体设置；不能被修改或移出。' },
  { role: 'admin', text: '可管理成员与访客，配置工作空间的功能与设置。' },
  { role: 'member', text: '可使用工作空间的功能与资源，参与协作。' },
  { role: 'viewer', text: '可查看被授权的内容，通常为只读。' },
] as const
</script>

<template>
  <ManagementPage v-if="auth.user" title="设置" description="管理个人资料、工作空间与成员。" aside-label="快捷设置与角色说明">
    <template v-if="auth.currentWorkspace" #context>
      <span class="mg-tag"><Users aria-hidden="true" />{{ auth.currentWorkspace.name }} · {{ roleName(auth.currentWorkspace.role) }}</span>
    </template>
    <div class="st-cards">
      <ProfileForm />
      <WorkspaceForm v-if="auth.currentWorkspace" :key="auth.workspaceId!" :workspace="auth.currentWorkspace" />
      <section v-else class="ws-panel mg-section"><h2>工作空间</h2><p>你尚未加入任何工作空间，请联系管理员。</p></section>
    </div>
    <MembersPanel v-if="auth.canManage && auth.workspaceId" :key="auth.workspaceId" :workspace-id="auth.workspaceId" />
    <template #aside>
      <ManagementSection title="快捷设置">
        <ul class="mg-links">
          <li v-for="item in shortcuts" :key="item.to">
            <component :is="item.icon" aria-hidden="true" />
            <div><strong>{{ item.title }}</strong><span class="asv-sub">{{ item.text }}</span></div>
            <RouterLink class="asv-link" :to="item.to">{{ item.link }}<ArrowRight aria-hidden="true" /></RouterLink>
          </li>
        </ul>
      </ManagementSection>
      <ManagementSection title="角色说明">
        <dl class="st-roles">
          <div v-for="item in ROLE_HELP" :key="item.role">
            <dt><span class="mg-role" :class="`is-${item.role}`">{{ ROLE_NAMES[item.role] }} <small>{{ item.role }}</small></span></dt>
            <dd>{{ item.text }}</dd>
          </div>
        </dl>
      </ManagementSection>
    </template>
  </ManagementPage>
  <p v-else role="alert">请先登录以查看设置。</p>
</template>
