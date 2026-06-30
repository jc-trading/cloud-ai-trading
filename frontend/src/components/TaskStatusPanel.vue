<template>
  <a-card title="🧠 任务健康状态（系统大脑）" class="task-panel">
    <template #extra>
      <a-tag :color="onlineCount > 0 ? 'green' : 'red'">
        {{ onlineCount }}/{{ tasks.length }} 在线
      </a-tag>
    </template>

    <a-empty v-if="tasks.length === 0" description="暂无任务" />

    <a-list
      v-else
      :data-source="tasks"
      class="task-list"
      :split="true"
    >
      <template #renderItem="{ item }">
        <a-list-item class="task-item">
          <template #actions>
            <a-tag :color="getStatusColor(item.status)">
              {{ formatStatus(item.status) }}
            </a-tag>
          </template>

          <a-list-item-meta>
            <template #avatar>
              <a-avatar 
                :style="{ backgroundColor: getStatusColor(item.status) }"
              >
                <template v-if="item.status === 'online'">
                  <CheckCircleOutlined />
                </template>
                <template v-else-if="item.status === 'offline'">
                  <DisconnectOutlined />
                </template>
                <template v-else>
                  <CloseCircleOutlined />
                </template>
              </a-avatar>
            </template>

            <template #title>
              {{ item.task_name }}
            </template>

            <template #description>
              <div class="task-description">
                <div v-if="item.last_execution_time" class="desc-item">
                  <span class="label">最后执行:</span>
                  <span>{{ formatTime(item.last_execution_time) }}</span>
                </div>
                <div v-if="item.success_rate !== null" class="desc-item">
                  <span class="label">成功率:</span>
                  <a-tag :color="item.success_rate > 95 ? 'green' : 'orange'">
                    {{ item.success_rate.toFixed(1) }}%
                  </a-tag>
                </div>
                <div v-if="item.total_executions > 0" class="desc-item">
                  <span class="label">执行次数:</span>
                  <span>{{ item.total_executions }}</span>
                </div>
              </div>
            </template>
          </a-list-item-meta>
        </a-list-item>
      </template>
    </a-list>

    <!-- 告警信息 -->
    <a-divider v-if="failedTasks.length > 0" style="margin: 16px 0;" />
    <a-alert
      v-for="task in failedTasks"
      :key="task.task_name"
      type="error"
      :message="`⚠️ ${task.task_name} 失败`"
      :description="task.last_error_message || '无错误信息'"
      show-icon
      closable
      style="margin-bottom: 12px"
    />
  </a-card>
</template>

<script setup>
import { computed } from 'vue'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import relativeTime from 'dayjs/plugin/relativeTime'
import {
  CheckCircleOutlined,
  DisconnectOutlined,
  CloseCircleOutlined
} from '@ant-design/icons-vue'

dayjs.extend(relativeTime)
dayjs.locale('zh-cn')

const props = defineProps({
  tasks: {
    type: Array,
    default: () => []
  }
})

const onlineCount = computed(() => {
  return props.tasks.filter(t => t.status === 'online').length
})

const failedTasks = computed(() => {
  return props.tasks.filter(t => t.status === 'failed')
})

const formatStatus = (status) => {
  const statusMap = {
    'online': '✅ 在线',
    'offline': '⏸️ 离线',
    'running': '🔄 运行中',
    'idle': '😴 空闲',
    'failed': '❌ 失败'
  }
  return statusMap[status] || status
}

const getStatusColor = (status) => {
  const colorMap = {
    'online': 'green',
    'offline': 'default',
    'running': 'blue',
    'idle': 'cyan',
    'failed': 'red'
  }
  return colorMap[status] || 'default'
}

const formatTime = (time) => {
  if (!time) return '--'
  return dayjs(time).fromNow()
}
</script>

<style scoped>
.task-panel {
  height: 100%;
}

.task-list {
  max-height: 400px;
  overflow-y: auto;
}

.task-item {
  padding: 12px 0 !important;
}

.task-description {
  display: flex;
  gap: 16px;
  font-size: 12px;
  flex-wrap: wrap;
}

.desc-item {
  display: flex;
  gap: 4px;
  align-items: center;
}

.label {
  color: #666;
  font-weight: 500;
}

/* 滚动条样式 */
:deep(.ant-list) {
  scrollbar-width: thin;
  scrollbar-color: #ccc transparent;
}

:deep(.ant-list::-webkit-scrollbar) {
  width: 6px;
}

:deep(.ant-list::-webkit-scrollbar-track) {
  background: transparent;
}

:deep(.ant-list::-webkit-scrollbar-thumb) {
  background: #ccc;
  border-radius: 3px;
}
</style>
