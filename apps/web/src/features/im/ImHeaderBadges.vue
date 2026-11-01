<script setup lang="ts">
import { useAuthStore } from '../../stores/auth'
import { useImBadges } from './queries'

// Top bar counts for owner / admin: IM unread and AI approvals waiting (OC-1 contract §11). Hidden on error.
const auth = useAuthStore(), badges = useImBadges(auth.workspaceId!)
</script>

<template>
  <template v-if="badges.data.value">
    <RouterLink to="/im" class="theme-button im-header-link" :aria-label="`消息，${badges.data.value.im_unread} 条未读`">消息<span v-if="badges.data.value.im_unread" class="im-unread">{{ badges.data.value.im_unread > 99 ? '99+' : badges.data.value.im_unread }}</span></RouterLink>
    <RouterLink v-if="badges.data.value.pending_approvals" to="/ai" class="theme-button im-header-link" :aria-label="`${badges.data.value.pending_approvals} 项待审批`">待审批<span class="im-unread">{{ badges.data.value.pending_approvals }}</span></RouterLink>
  </template>
</template>
