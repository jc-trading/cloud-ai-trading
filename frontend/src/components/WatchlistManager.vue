<template>
  <div class="watchlist-manager">
    <a-card title="📊 市场观察列表 (Watchlist)" :bordered="false">
      <!-- Add Symbol Input -->
      <div class="add-symbol-section">
        <a-space direction="vertical" style="width: 100%">
          <div class="input-group">
            <a-input-group compact>
              <a-select
                v-model:value="newItem.market_type"
                style="width: 120px"
                placeholder="市场类型"
              >
                <a-select-option value="crypto">Crypto (加密)</a-select-option>
                <a-select-option value="stock">Stock (股票)</a-select-option>
              </a-select>
              <a-input
                v-model:value="newItem.symbol"
                placeholder="输入符号 (e.g., BTCUSDT, AAPL)"
                style="width: calc(100% - 200px)"
                @keyup.enter="addSymbol"
              />
              <a-button
                type="primary"
                :loading="addingSymbol"
                @click="addSymbol"
              >
                添加 (Add)
              </a-button>
            </a-input-group>
          </div>
        </a-space>
      </div>

      <!-- Watchlist Items -->
      <div class="watchlist-items" v-if="watchlistItems.length > 0">
        <a-divider />
        <h3>您的观察列表 (Your Watchlist)</h3>
        <a-table
          :columns="columns"
          :data-source="watchlistItems"
          :pagination="false"
          :loading="loadingItems"
          size="small"
          :scroll="{ x: 600 }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'symbol'">
              <strong>{{ record.symbol }}</strong>
            </template>
            <template v-else-if="column.key === 'market_type'">
              <a-tag :color="record.market_type === 'crypto' ? 'blue' : 'green'">
                {{ record.market_type === 'crypto' ? '🪙 Crypto' : '📈 Stock' }}
              </a-tag>
            </template>
            <template v-else-if="column.key === 'last_price'">
              <span v-if="record.last !== null && record.last !== undefined">
                ${{ parseFloat(record.last).toFixed(2) }}
              </span>
              <span v-else class="no-data">-</span>
            </template>
            <template v-else-if="column.key === 'change_24h'">
              <span
                v-if="record.change_24h !== null && record.change_24h !== undefined"
                :style="{
                  color: parseFloat(record.change_24h) >= 0 ? '#52c41a' : '#ff4d4f'
                }"
              >
                {{ parseFloat(record.change_24h) >= 0 ? '+' : '' }}
                {{ parseFloat(record.change_24h).toFixed(2) }}%
              </span>
              <span v-else class="no-data">-</span>
            </template>
            <template v-else-if="column.key === 'action'">
              <a-button
                type="text"
                danger
                size="small"
                :loading="removingId === record.id"
                @click="removeSymbol(record.id)"
              >
                删除 (Remove)
              </a-button>
            </template>
          </template>
        </a-table>
      </div>

      <!-- Empty State -->
      <a-empty
        v-else
        description="暂无观察列表 (No watchlist items)"
        style="margin-top: 20px"
      >
        <template #extra>
          <p style="color: #999; margin-top: 10px">
            添加符号开始跟踪市场数据 (Add symbols to start tracking market data)
          </p>
        </template>
      </a-empty>
    </a-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import { watchlistApi } from '@/api/market'

// State
const newItem = ref({
  symbol: '',
  market_type: 'crypto'
})
const watchlistItems = ref([])
const addingSymbol = ref(false)
const removingId = ref(null)
const loadingItems = ref(false)

// Table columns
const columns = [
  {
    title: '符号 (Symbol)',
    dataIndex: 'symbol',
    key: 'symbol',
    width: 120
  },
  {
    title: '市场 (Market)',
    dataIndex: 'market_type',
    key: 'market_type',
    width: 100
  },
  {
    title: '最新价格 (Last Price)',
    dataIndex: 'last',
    key: 'last_price',
    width: 120,
    align: 'right'
  },
  {
    title: '24小时涨跌 (24h Change)',
    dataIndex: 'change_24h',
    key: 'change_24h',
    width: 120,
    align: 'right'
  },
  {
    title: '操作 (Action)',
    key: 'action',
    width: 100,
    align: 'center'
  }
]

// Methods
const loadWatchlist = async () => {
  try {
    loadingItems.value = true
    const response = await watchlistApi.getDefaultWithPrices()
    watchlistItems.value = response.data || []
  } catch (error) {
    message.error('加载观察列表失败: ' + (error.response?.data?.detail || error.message))
  } finally {
    loadingItems.value = false
  }
}

const addSymbol = async () => {
  if (!newItem.value.symbol.trim()) {
    message.warning('请输入符号 (Please enter a symbol)')
    return
  }

  try {
    addingSymbol.value = true
    const rawSymbol = newItem.value.symbol.trim().toUpperCase()
    const symbol = newItem.value.market_type === 'crypto' && !rawSymbol.includes('/')
      ? rawSymbol.replace(/USDT$/, '') + '/USDT'
      : rawSymbol
    await watchlistApi.addToDefault({
      symbol,
      market_type: newItem.value.market_type
    })
    message.success('添加成功 (Added successfully)')
    newItem.value.symbol = ''
    await loadWatchlist()
  } catch (error) {
    message.error('添加失败: ' + (error.response?.data?.detail || error.message))
  } finally {
    addingSymbol.value = false
  }
}

const removeSymbol = async (itemId) => {
  try {
    removingId.value = itemId
    await watchlistApi.removeFromDefault(itemId)
    message.success('删除成功 (Removed successfully)')
    await loadWatchlist()
  } catch (error) {
    message.error('删除失败: ' + (error.response?.data?.detail || error.message))
  } finally {
    removingId.value = null
  }
}

// Lifecycle
onMounted(() => {
  loadWatchlist()
})
</script>

<style scoped>
.watchlist-manager {
  width: 100%;
}

.add-symbol-section {
  margin-bottom: 20px;
}

.input-group {
  display: flex;
  gap: 8px;
}

.watchlist-items {
  margin-top: 20px;
}

.no-data {
  color: #999;
}

:deep(.ant-table-small) {
  font-size: 13px;
}
</style>
