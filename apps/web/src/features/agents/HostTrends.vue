<script setup lang="ts">
import type { Schema } from '../../services/api'
import type { HostRange } from './host'
import HostChart from './HostChart.vue'

// 整台服务器的每小时趋势（T-0113；实例卡片与服务器详情共用，T-0147）。一张图一条轴。
defineProps<{ points: Schema['HostPoint'][]; range: HostRange; cores?: number | null }>()
</script>

<template>
  <div class="host-charts">
    <HostChart title="CPU 使用率" kind="percent" :range="range" :points="points" :series="[{ key: 'cpu_avg', label: '平均' }, { key: 'cpu_max', label: '峰值' }]" />
    <HostChart title="负载（1 分钟）" :note="cores ? `${cores} 核` : undefined" kind="load" :range="range" :points="points" :series="[{ key: 'load_avg', label: '平均' }, { key: 'load_max', label: '峰值' }]" />
    <HostChart title="内存已用" kind="percent" :range="range" :points="points" :series="[{ key: 'mem_avg', label: '平均' }, { key: 'mem_max', label: '峰值' }]" />
    <HostChart title="磁盘已用" note="每小时平均" kind="percent" :range="range" :points="points" :series="[{ key: 'data_avg', label: '接入包目录' }, { key: 'root_avg', label: 'Docker 数据目录' }]" />
    <HostChart title="网络" note="每小时平均，悬停看峰值" kind="rate" :range="range" :points="points" :series="[{ key: 'rx_avg', label: '下载' }, { key: 'tx_avg', label: '上传' }]" :extra="[{ key: 'rx_max', label: '下载峰值' }, { key: 'tx_max', label: '上传峰值' }]" />
  </div>
</template>
