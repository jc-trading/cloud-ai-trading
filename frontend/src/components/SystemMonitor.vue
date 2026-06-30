<template>
  <a-card title="系统监控" class="system-monitor">
    <template #extra>
      <a-tag :color="healthColor">{{ healthStatus }}</a-tag>
    </template>

    <a-space direction="vertical" style="width: 100%;" size="large">
      <!-- CPU -->
      <div class="metric-item">
        <div class="metric-header">
          <span>CPU</span>
          <span class="metric-value">{{ formatValue(metrics?.cpu_percent) }}%</span>
        </div>
        <a-progress 
          :percent="metrics?.cpu_percent || 0"
          :stroke-color="getStatusColor(metrics?.cpu_percent)"
          :format="() => ''"
        />
      </div>

      <!-- 内存 -->
      <div class="metric-item">
        <div class="metric-header">
          <span>内存</span>
          <span class="metric-value">{{ formatValue(metrics?.memory_percent) }}%</span>
        </div>
        <a-progress 
          :percent="metrics?.memory_percent || 0"
          :stroke-color="getStatusColor(metrics?.memory_percent)"
          :format="() => ''"
        />
      </div>

      <!-- 磁盘 -->
      <div class="metric-item">
        <div class="metric-header">
          <span>磁盘</span>
          <span class="metric-value">{{ formatValue(metrics?.disk_percent) }}%</span>
        </div>
        <a-progress 
          :percent="metrics?.disk_percent || 0"
          :stroke-color="getStatusColor(metrics?.disk_percent)"
          :format="() => ''"
        />
      </div>

      <!-- Load Average -->
      <div v-if="metrics?.load_average_1 !== undefined" class="load-average">
        <div class="load-item">
          <span>Load 1min</span>
          <span class="load-value">{{ formatValue(metrics.load_average_1) }}</span>
        </div>
        <div class="load-item">
          <span>Load 5min</span>
          <span class="load-value">{{ formatValue(metrics.load_average_5) }}</span>
        </div>
        <div class="load-item">
          <span>Load 15min</span>
          <span class="load-value">{{ formatValue(metrics.load_average_15) }}</span>
        </div>
      </div>

      <!-- 更新时间 -->
      <div class="update-time">
        <span class="secondary-text">最后更新: {{ lastUpdate }}</span>
      </div>
    </a-space>
  </a-card>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  metrics: Object,
  health: Object
})

const lastUpdate = computed(() => {
  if (!props.metrics) return '--'
  const date = new Date(props.metrics.timestamp)
  return date.toLocaleTimeString('zh-CN')
})

const healthStatus = computed(() => {
  if (!props.health) return '未知'
  if (props.health.is_healthy === true) return '健康'
  if (props.health.alerts?.some(alert => alert.severity === 'warning')) return '警告'
  return '异常'
})

const healthColor = computed(() => {
  if (!props.health) return 'default'
  if (props.health.is_healthy === true) return 'green'
  if (props.health.alerts?.some(alert => alert.severity === 'warning')) return 'orange'
  return 'red'
})

const formatValue = (value) => {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'number') {
    return value.toFixed(1)
  }
  return String(value)
}

const getStatusColor = (value) => {
  if (value === null || value === undefined) return '#d9d9d9'
  if (value > 85) return '#ff4d4f' // 红色 - 危险
  if (value > 70) return '#faad14' // 橙色 - 警告
  return '#52c41a' // 绿色 - 正常
}
</script>

<style scoped>
.system-monitor {
  height: 100%;
}

.metric-item {
  padding: 8px 0;
}

.metric-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 14px;
}

.metric-value {
  font-weight: 600;
  color: #1890ff;
}

.load-average {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  padding-top: 8px;
  border-top: 1px solid #f0f0f0;
}

.load-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  font-size: 12px;
}

.load-value {
  font-weight: 600;
  color: #1890ff;
  margin-top: 4px;
}

.update-time {
  padding-top: 8px;
  border-top: 1px solid #f0f0f0;
  text-align: center;
}

.secondary-text {
  color: #8c8c8c;
  font-size: 13px;
}
</style>
