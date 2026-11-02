<script setup lang="ts">
import { MessageSquare, ShieldCheck } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { useImBadges } from './queries'

// Top bar counts for owner / admin: IM unread and AI approvals waiting (OC-1 contract §11). Hidden on error.
const auth = useAuthStore(), badges = useImBadges(auth.workspaceId!)
</script>

<template>
  <template v-if="badges.data.value">
    <RouterLink to="/im" class="shell-icon" :aria-label="`消息，${badges.data.value.im_unread} 条未读`" title="消息"><MessageSquare aria-hidden="true" /><span v-if="badges.data.value.im_unread" class="count-badge" aria-hidden="true">{{ badges.data.value.im_unread > 99 ? '99+' : badges.data.value.im_unread }}</span></RouterLink>
    <RouterLink v-if="badges.data.value.pending_approvals" to="/ai" class="shell-icon" :aria-label="`${badges.data.value.pending_approvals} 项待审批`" title="待审批"><ShieldCheck aria-hidden="true" /><span class="count-badge is-warning" aria-hidden="true">{{ badges.data.value.pending_approvals > 99 ? '99+' : badges.data.value.pending_approvals }}</span></RouterLink>
  </template>
</template>
