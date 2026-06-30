<template>
  <a-card title="实时日志查看器" class="log-viewer">
    <!-- 过滤器 -->
    <div class="log-filters" style="margin-bottom: 16px;">
      <a-space>
        <a-select
          v-model:value="filterCategory"
          placeholder="选择类别"
          style="width: 150px"
          allowClear
          @change="handleFilterChange"
        >
          <a-select-option value="">全部</a-select-option>
          <a-select-option value="market_data">市场数据</a-select-option>
          <a-select-option value="trading">交易</a-select-option>
          <a-select-option value="schedule">调度</a-select-option>
          <a-select-option value="system">系统</a-select-option>
        </a-select>

        <a-select
          v-model:value="filterLevel"
          placeholder="选择优先级"
          style="width: 120px"
          allowClear
          @change="handleFilterChange"
        >
          <a-select-option value="">全部</a-select-option>
          <a-select-option value="DEBUG">
            <a-tag color="blue">DEBUG</a-tag>
          </a-select-option>
          <a-select-option value="INFO">
            <a-tag color="green">INFO</a-tag>
          </a-select-option>
          <a-select-option value="WARNING">
            <a-tag color="orange">WARNING</a-tag>
          </a-select-option>
          <a-select-option value="ERROR">
            <a-tag color="red">ERROR</a-tag>
          </a-select-option>
          <a-select-option value="CRITICAL">
            <a-tag color="volcano">CRITICAL</a-tag>
          </a-select-option>
        </a-select>

        <a-button @click="clearLogs" danger>清空日志</a-button>
      </a-space>
    </div>

    <!-- 日志列表 -->
    <div class="log-container">
      <a-empty v-if="filteredLogs.length === 0" description="暂无日志" />
      <div v-else class="log-list">
        <div
          v-for="log in filteredLogs"
          :key="log.id"
          class="log-entry"
          :class="log.level.toLowerCase()"
        >
          <div class="log-header">
            <span class="log-time">{{ formatTime(log.timestamp) }}</span>
            <a-tag :color="getLevelColor(log.level)">{{ log.level }}</a-tag>
            <a-tag v-if="log.category" color="purple">{{ log.category }}</a-tag>
            <a-tag v-if="log.task_name" color="cyan">{{ log.task_name }}</a-tag>
            <a-tag v-if="log.symbol" color="blue">{{ log.symbol }}</a-tag>
          </div>
          <div class="log-message">{{ log.message }}</div>
          <div v-if="log.event_metadata" class="log-metadata">
            <a-button 
              type="text" 
              size="small"
              @click="toggleMetadata(log.id)"
            >
              {{ expandedMetadata[log.id] ? '隐藏' : '查看' }}元数据
            </a-button>
            <div v-if="expandedMetadata[log.id]" class="metadata-content">
              <pre>{{ JSON.stringify(log.event_metadata, null, 2) }}</pre>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 统计信息 -->
    <a-divider style="margin: 16px 0;" />
    <div class="log-stats">
      <a-space>
        <span>总日志数: <strong>{{ logs.length }}</strong></span>
        <span>ERROR: <strong style="color: red;">{{ errorCount }}</strong></span>
        <span>WARNING: <strong style="color: orange;">{{ warningCount }}</strong></span>
      </a-space>
    </div>
  </a-card>
</template>

<script setup>
import { ref, computed } from 'vue'
import dayjs from 'dayjs'

const props = defineProps({
  logs: {
    type: Array,
    default: () => []
  }
})

const emit = defineEmits(['filter-change'])

const filterCategory = ref('')
const filterLevel = ref('')
const expandedMetadata = ref({})

const filteredLogs = computed(() => {
  return props.logs.filter(log => {
    if (filterCategory.value && log.category !== filterCategory.value) return false
    if (filterLevel.value && log.level !== filterLevel.value) return false
    return true
  })
})

const errorCount = computed(() => {
  return props.logs.filter(log => log.level === 'ERROR').length
})

const warningCount = computed(() => {
  return props.logs.filter(log => log.level === 'WARNING').length
})

const handleFilterChange = () => {
  emit('filter-change', {
    category: filterCategory.value || null,
    level: filterLevel.value || null
  })
}

const clearLogs = () => {
  filterCategory.value = ''
  filterLevel.value = ''
  expandedMetadata.value = {}
}

const toggleMetadata = (logId) => {
  expandedMetadata.value[logId] = !expandedMetadata.value[logId]
}

const formatTime = (timestamp) => {
  return dayjs(timestamp).format('HH:mm:ss')
}

const getLevelColor = (level) => {
  const colorMap = {
    'DEBUG': 'blue',
    'INFO': 'green',
    'WARNING': 'orange',
    'ERROR': 'red',
    'CRITICAL': 'volcano'
  }
  return colorMap[level] || 'default'
}
</script>

<style scoped>
.log-viewer {
  margin-top: 16px;
}

.log-filters {
  padding: 12px;
  background-color: #fafafa;
  border-radius: 4px;
}

.log-container {
  max-height: 500px;
  overflow-y: auto;
  border: 1px solid #f0f0f0;
  border-radius: 4px;
  background-color: #fafafa;
}

.log-list {
  padding: 0;
}

.log-entry {
  padding: 12px;
  border-bottom: 1px solid #f0f0f0;
  background-color: white;
  font-family: 'Monaco', 'Courier New', monospace;
  font-size: 12px;

  &:last-child {
    border-bottom: none;
  }

  &.error {
    background-color: #fff1f0;
  }

  &.warning {
    background-color: #fffbe6;
  }

  &.critical {
    background-color: #fff1f0;
    border-left: 4px solid #ff4d4f;
  }
}

.log-header {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.log-time {
  color: #666;
  font-weight: 600;
  min-width: 70px;
}

.log-message {
  word-break: break-word;
  white-space: pre-wrap;
  margin-bottom: 8px;
  color: #333;
}

.log-metadata {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid #f0f0f0;
}

.metadata-content {
  background-color: #f5f5f5;
  padding: 8px;
  border-radius: 4px;
  margin-top: 8px;
  overflow-x: auto;
  max-height: 200px;
  overflow-y: auto;
}

.metadata-content pre {
  margin: 0;
  font-size: 11px;
  color: #666;
}

.log-stats {
  padding: 8px;
  text-align: right;
  font-size: 12px;
  color: #666;
}

/* 滚动条样式 */
:deep(.log-container) {
  scrollbar-width: thin;
  scrollbar-color: #ccc transparent;
}

:deep(.log-container::-webkit-scrollbar) {
  width: 6px;
}

:deep(.log-container::-webkit-scrollbar-track) {
  background: transparent;
}

:deep(.log-container::-webkit-scrollbar-thumb) {
  background: #ccc;
  border-radius: 3px;
}
</style>
