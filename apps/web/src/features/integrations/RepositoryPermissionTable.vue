<script setup lang="ts">
import { ArrowRight, BookMarked, CircleCheck, CircleHelp, CircleX, Info, LoaderCircle, TriangleAlert } from '@lucide/vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import { formatTime } from '../time'
import { PERMISSIONS, sourceText, type PermissionRow } from './permissions'

// 仓库权限核验表（T-0192，效果图 03）：每个已选仓库的 Metadata / Issues / Pull requests / Actions 与结论。
// 数据由页面按真实核验结果算好（permissions.ts）；这里只负责显示，没有结果的格子写“未核验”。
// 授权失效时页面不传结果，只显示说明，不留旧的私有结果。
defineProps<{ rows: PermissionRow[]; unauthorized: boolean; error?: string }>()
defineEmits<{ retry: [] }>()
// 长仓库名在“/”后面换行，不在单词中间断开
const ownerOf = (name: string) => name.slice(0, name.indexOf('/') + 1)
const repoOf = (name: string) => name.slice(name.indexOf('/') + 1)
</script>

<template>
  <ManagementSection title="仓库权限核验" class="ig-permissions">
    <p v-if="unauthorized" class="ws-state-line">GitHub 授权失效时不显示核验结果；重新连接并测试连接后再查看。</p>
    <p v-else-if="!rows.length" class="ws-state-line">还没有选择要同步的仓库。</p>
    <template v-else>
      <div v-if="error" class="ig-inline-error">
        <p role="alert">仓库清单读取失败：{{ error }}</p>
        <button type="button" class="secondary-button" @click="$emit('retry')">重试</button>
      </div>
      <table class="ws-table asv-table ig-table">
        <caption class="sr-only">仓库权限核验</caption>
        <thead><tr><th scope="col">仓库</th><th v-for="p in PERMISSIONS" :key="p.name" scope="col">{{ p.label }}</th><th scope="col">结果</th></tr></thead>
        <tbody>
          <tr v-for="row in rows" :key="row.fullName" :aria-label="`仓库：${row.fullName}`">
            <th scope="row">
              <span class="asv-name"><BookMarked aria-hidden="true" /><span>{{ ownerOf(row.fullName) }}<wbr>{{ repoOf(row.fullName) }}</span></span>
              <span class="asv-sub">{{ sourceText(row, formatTime) }}</span>
            </th>
            <td v-for="cell in row.cells" :key="cell.name" :data-label="cell.label">
              <span class="ig-check" :class="`tone-${cell.tone}`">
                <CircleCheck v-if="cell.state === 'ok'" aria-hidden="true" />
                <TriangleAlert v-else-if="cell.state === 'failed' && cell.tone === 'amber'" aria-hidden="true" />
                <CircleX v-else-if="cell.state === 'failed'" aria-hidden="true" />
                <LoaderCircle v-else-if="cell.state === 'loading'" aria-hidden="true" />
                <CircleHelp v-else aria-hidden="true" />{{ cell.text }}
              </span>
            </td>
            <td data-label="结果"><span class="asv-state" :class="`tone-${row.result.tone}`">{{ row.result.text }}</span></td>
          </tr>
        </tbody>
      </table>
    </template>
    <div class="ig-table-foot">
      <p class="ig-note"><Info aria-hidden="true" />逐仓库核验实际读取权限，未核验项显示“未核验”；结果来自测试连接，不是实时权限。</p>
      <RouterLink class="asv-link" to="/developer">查看开发者页面<ArrowRight aria-hidden="true" /></RouterLink>
    </div>
  </ManagementSection>
</template>
