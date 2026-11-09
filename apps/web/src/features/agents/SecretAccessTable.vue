<script setup lang="ts">
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { ACCESS_RESULT, ACCESS_TONE } from './secrets'

// 取用记录表（T-0196）：“取用记录”标签与密钥详情共用。来源 IP 只在这个 owner 页面显示（接口本来就只给 owner）；
// compact 用在详情一栏里，省掉引用名一列。
defineProps<{ items: Schema['SecretAccessOut'][]; compact?: boolean }>()
</script>

<template>
  <div class="ws-table-wrap">
    <table class="ws-table asv-table sk-access-table" :class="{ 'is-compact': compact }">
      <thead><tr><th scope="col">时间</th><th scope="col">智能体</th><th v-if="!compact" scope="col">引用名</th><th scope="col">结果</th><th scope="col">版本</th><th scope="col">来源</th></tr></thead>
      <tbody>
        <tr v-for="a in items" :key="a.id">
          <th scope="row" data-label="时间"><time :datetime="a.at">{{ formatTime(a.at) }}</time></th>
          <td data-label="智能体">{{ a.instance_name }}<span v-if="!a.instance_id" class="asv-sub">实例已删除</span></td>
          <td v-if="!compact" data-label="引用名"><code class="asv-code">{{ a.ref_id }}</code></td>
          <td data-label="结果"><span class="asv-state" :class="`tone-${ACCESS_TONE[a.result] ?? 'muted'}`">{{ ACCESS_RESULT[a.result] ?? a.result }}</span></td>
          <td data-label="版本">{{ a.value_version === null ? '—' : `v${a.value_version}` }}</td>
          <td data-label="来源" class="sk-ip">{{ a.source_ip ?? '—' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
