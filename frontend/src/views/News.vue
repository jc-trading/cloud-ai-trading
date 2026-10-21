<template>
  <div class="jd-page">
    <div class="jd-page-head" style="margin-bottom:0">
      <div>
        <h1 class="jd-h1">News</h1>
        <p class="jd-sub">
          LLM budget {{ meta.budget_used_today ?? '—' }}/{{ meta.budget ?? '—' }} today (UTC)
          · pending {{ meta.pending ?? '—' }}
          · last cycle {{ fmtEt(meta.last_cycle?.at) }}<template v-if="cycleLlm"> · {{ cycleLlm }}</template>
        </p>
      </div>
      <div class="head-actions">
        <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" :disabled="refreshing" @click="refresh">
          <i class="pi pi-refresh"></i> Refresh
        </button>
      </div>
    </div>

    <div class="jd-tabs tabs">
      <button type="button" class="jd-tab" :class="{ active: tab === 'feed' }" @click="tab = 'feed'">Feed</button>
      <button type="button" class="jd-tab" :class="{ active: tab === 'queue' }" @click="openQueue">Review queue</button>
    </div>

    <template v-if="tab === 'feed'">
      <DataTable
        :columns="feedColumns"
        :data="items"
        row-key="id"
        :pagination="false"
        clickable-rows
        :loading="feedLoading"
        :error="feedError"
        empty-text="No news matches these filters"
        @row-click="(row) => openDetail(row.id)"
      >
        <template #toolbar-left>
          <input v-model="filters.symbol" class="jd-input toolbar-ctl symbol-input" placeholder="Symbol"
                 maxlength="20" aria-label="Symbol" @keyup.enter="loadFeed" />
          <select v-model="filters.action" class="jd-input jd-select toolbar-ctl" aria-label="Action" @change="loadFeed">
            <option value="">Action · All</option>
            <option v-for="a in ACTIONS" :key="a" :value="a">{{ a }}</option>
          </select>
          <select v-model="filters.status" class="jd-input jd-select toolbar-ctl" aria-label="Status" @change="loadFeed">
            <option value="">Status · All</option>
            <option v-for="s in STATUSES" :key="s" :value="s">{{ s }}</option>
          </select>
          <select v-model="filters.origin" class="jd-input jd-select toolbar-ctl" aria-label="Origin" @change="loadFeed">
            <option value="">Origin · All</option>
            <option v-for="o in ORIGINS" :key="o" :value="o">{{ o }}</option>
          </select>
        </template>
        <template #cell:published_at="{ value }">
          <span class="jd-mono cell-time">{{ fmtEt(value) }}</span>
        </template>
        <template #cell:headline="{ value }">
          <span class="headline" :title="value">{{ value }}</span>
        </template>
        <template #cell:symbols="{ row }">
          <span class="chips">
            <span v-for="s in row.symbols" :key="s.id" :class="['jd-badge', claimColor(s)]">{{ symbolChip(s) }}</span>
          </span>
        </template>
        <template #cell:actions="{ row }">
          <span class="chips">
            <span v-for="a in actionsOf(row)" :key="a" :class="['jd-badge', ACTION_COLORS[a] || 'gray']">{{ a }}</span>
          </span>
        </template>
        <template #cell:status="{ value }">
          <span :class="['jd-badge', STATUS_COLORS[value] || 'gray']">{{ value }}</span>
        </template>
        <template #cell:source="{ row }">
          <span class="cell-muted">{{ sourceLabel(row) }}</span>
        </template>
      </DataTable>
      <div v-if="nextCursor && !feedLoading && !feedError" class="load-more">
        <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" :disabled="loadingMore" @click="loadMore">
          <i v-if="loadingMore" class="pi pi-spin pi-spinner"></i> Load more
        </button>
      </div>
    </template>

    <template v-else>
      <DataTable
        :columns="queueColumns"
        :data="queue"
        row-key="id"
        :page-size="25"
        clickable-rows
        :loading="queueLoading"
        :error="queueError"
        empty-text="Nothing to review"
        @row-click="(row) => openDetail(row.news_id)"
      >
        <template #toolbar-left>
          <label class="upcoming">
            <input v-model="includeUpcoming" type="checkbox" @change="loadQueue" /> Include upcoming
          </label>
        </template>
        <template #cell:review_at="{ value }">
          <span class="jd-mono cell-time">{{ fmtEt(value) }}</span>
        </template>
        <template #cell:symbol="{ value }">
          <span class="font-semibold">{{ value }}</span>
        </template>
        <template #cell:headline="{ value }">
          <span class="headline" :title="value">{{ value }}</span>
        </template>
        <template #cell:claim="{ row }">
          <span class="jd-mono" :class="row.direction === 'down' ? 'price-down' : 'price-up'">{{ claimSummary(row) }}</span>
        </template>
        <template #cell:suggested_action="{ value }">
          <span :class="['jd-badge', ACTION_COLORS[value] || 'gray']">{{ value }}</span>
        </template>
        <template #cell:action_reason="{ value }">
          <span class="cell-muted">{{ value || '—' }}</span>
        </template>
        <template v-if="auth.isAdmin" #row-actions="{ row }">
          <div class="row-actions">
            <button type="button" class="jd-btn jd-btn-success jd-btn-sm" @click="askReview(row, 'done')">Done</button>
            <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" @click="askReview(row, 'dismissed')">Dismiss</button>
          </div>
        </template>
      </DataTable>
    </template>

    <Modal v-model="showDetail" title="News item" width="760px">
      <NewsItemDetail :item="detail" :loading="detailLoading" :error="detailError" />
    </Modal>

    <Modal v-model="showReview" :title="review.status === 'done' ? 'Mark review done' : 'Dismiss review'" width="520px">
      <p class="confirm-text">{{ review.row?.symbol }} · {{ review.row?.headline }}</p>
      <label class="jd-label" for="review-note" style="margin-top:14px">Note (optional)</label>
      <textarea id="review-note" v-model="review.note" class="jd-input" rows="3" maxlength="500"></textarea>
      <template #footer>
        <button type="button" class="jd-btn jd-btn-ghost jd-btn-sm" @click="showReview = false">Cancel</button>
        <button type="button" class="jd-btn jd-btn-sm" :class="review.status === 'done' ? 'jd-btn-success' : 'jd-btn-primary'"
                :disabled="reviewing" @click="confirmReview">
          <i v-if="reviewing" class="pi pi-spin pi-spinner"></i> Confirm
        </button>
      </template>
    </Modal>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import DataTable from '@/components/common/DataTable.vue'
import Modal from '@/components/common/Modal.vue'
import NewsItemDetail from '@/components/news/NewsItemDetail.vue'
import { getNewsItems, getNewsItem, getReviewQueue, reviewSymbol } from '@/api/news'
import { useAuthStore } from '@/stores/auth'
import { useToast } from '@/composables/useToast'
import { detailOf } from '@/composables/useApiError'

const auth = useAuthStore()
const toast = useToast()

const ACTIONS = ['buy', 'sell', 'review', 'ignore']
const STATUSES = ['pending', 'irrelevant', 'skipped', 'extracted', 'no_claim', 'failed']
const ORIGINS = ['live', 'backfill']
const ACTION_COLORS = { buy: 'green', sell: 'red', review: 'yellow', ignore: 'gray' }
const STATUS_COLORS = { extracted: 'green', pending: 'yellow', failed: 'red' }

const tab = ref('feed')
const meta = ref({})
const items = ref([])
const nextCursor = ref(null)
const feedLoading = ref(false)
const feedError = ref(null)
const loadingMore = ref(false)
const filters = reactive({ symbol: '', action: '', status: '', origin: '' })
let seq = 0
let applied = {}

const queue = ref([])
const queueLoaded = ref(false)
const queueLoading = ref(false)
const queueError = ref(null)
const includeUpcoming = ref(false)

const showDetail = ref(false)
const detail = ref(null)
const detailLoading = ref(false)
const detailError = ref(null)
let detailSeq = 0

const showReview = ref(false)
const review = reactive({ row: null, status: 'done', note: '' })
const reviewing = ref(false)

const feedColumns = [
  { key: 'published_at', header: 'Published (ET)' },
  { key: 'headline', header: 'Headline' },
  { key: 'symbols', header: 'Symbols' },
  { key: 'actions', header: 'Actions', accessor: (r) => r.symbols.map((s) => s.suggested_action).join(',') },
  { key: 'status', header: 'Status' },
  { key: 'source', header: 'Source', accessor: (r) => r.sources[0]?.name ?? null },
]

const queueColumns = [
  { key: 'review_at', header: 'Due (ET)' },
  { key: 'symbol', header: 'Symbol' },
  { key: 'headline', header: 'Headline' },
  { key: 'claim', header: 'Claim', accessor: (r) => r.probability },
  { key: 'suggested_action', header: 'Action' },
  { key: 'relevance', header: 'Relevance' },
  { key: 'action_reason', header: 'Reason' },
]

const cycleLlm = computed(() => {
  const llm = meta.value.last_cycle?.meta?.extract?.llm
  return llm && llm !== 'ok' ? llm : null
})
const refreshing = computed(() => (tab.value === 'feed' ? feedLoading.value : queueLoading.value))

const fmtEt = (iso) => {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-US', {
    timeZone: 'America/New_York', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }) + ' ET'
}
const arrow = (direction) => (direction === 'down' ? '↓' : '↑')
const symbolChip = (s) => (s.direction ? `${s.symbol} ${arrow(s.direction)}${s.probability}` : s.symbol)
const claimColor = (s) => (s.direction === 'up' ? 'green' : s.direction === 'down' ? 'red' : 'gray')
const actionsOf = (row) => [...new Set(row.symbols.map((s) => s.suggested_action).filter(Boolean))]
const sourceLabel = (row) => {
  if (!row.sources.length) return '—'
  const extra = row.sources.length - 1
  return extra > 0 ? `${row.sources[0].name} +${extra}` : row.sources[0].name
}
const claimSummary = (r) => {
  if (!r.direction) return '—'
  return `${arrow(r.direction)} p${r.probability} · ${r.expected_move_pct}% · ${r.horizon_days}d`
}

const feedParams = () => ({
  symbol: filters.symbol.trim() || null,
  action: filters.action || null,
  status: filters.status || null,
  origin: filters.origin || null,
})

async function loadFeed() {
  const my = ++seq
  feedLoading.value = true
  feedError.value = null
  nextCursor.value = null
  applied = feedParams()
  try {
    const { data } = await getNewsItems(applied)
    if (my !== seq) return
    items.value = data.items
    nextCursor.value = data.next_cursor
    meta.value = data.meta || {}
  } catch (e) {
    if (my !== seq) return
    feedError.value = detailOf(e, 'Could not load news')
  } finally {
    if (my === seq) feedLoading.value = false
  }
}

async function loadMore() {
  const my = seq
  loadingMore.value = true
  try {
    const { data } = await getNewsItems({ ...applied, cursor: nextCursor.value })
    if (my !== seq) return
    items.value = [...items.value, ...data.items]
    nextCursor.value = data.next_cursor
    meta.value = data.meta || meta.value
  } catch (e) {
    if (my === seq) toast.error(detailOf(e, 'Could not load more news'))
  } finally {
    loadingMore.value = false
  }
}

async function loadQueue() {
  queueLoading.value = true
  queueError.value = null
  try {
    const { data } = await getReviewQueue(includeUpcoming.value)
    queue.value = data
    queueLoaded.value = true
  } catch (e) {
    queueError.value = detailOf(e, 'Could not load the review queue')
  } finally {
    queueLoading.value = false
  }
}

function openQueue() {
  tab.value = 'queue'
  if (!queueLoaded.value && !queueLoading.value) loadQueue()
}

function refresh() {
  if (tab.value === 'feed') loadFeed()
  else loadQueue()
}

async function openDetail(id) {
  const my = ++detailSeq
  showDetail.value = true
  detail.value = null
  detailError.value = null
  detailLoading.value = true
  try {
    const { data } = await getNewsItem(id)
    if (my === detailSeq) detail.value = data
  } catch (e) {
    if (my === detailSeq) detailError.value = detailOf(e, 'Could not load this news item')
  } finally {
    if (my === detailSeq) detailLoading.value = false
  }
}

function askReview(row, status) {
  review.row = row
  review.status = status
  review.note = ''
  showReview.value = true
}

async function confirmReview() {
  reviewing.value = true
  try {
    await reviewSymbol(review.row.id, { status: review.status, note: review.note.trim() || null })
    toast.success(`${review.row.symbol} marked ${review.status}`)
  } catch (e) {
    toast.error(detailOf(e, 'Review failed'))
  } finally {
    reviewing.value = false
    showReview.value = false
    loadQueue()
  }
}

onMounted(loadFeed)
</script>

<style scoped>
.head-actions { display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap; }
.tabs { align-self: flex-start; }
.toolbar-ctl { width: auto; padding: 8px 30px 8px 12px; font-size: 12px; }
.symbol-input { width: 130px; padding-right: 12px; text-transform: uppercase; }
.headline { display: inline-block; max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: bottom; }
.chips { display: inline-flex; flex-wrap: wrap; gap: 4px; }
.cell-time { color: var(--jd-text-muted); font-size: 12px; white-space: nowrap; }
.cell-muted { color: var(--jd-text-muted); font-size: 12px; }
.load-more { display: flex; justify-content: center; }
.upcoming { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--jd-text-muted); cursor: pointer; }
.row-actions { display: flex; gap: 6px; justify-content: flex-end; }
.confirm-text { margin: 0; font-size: 13.5px; line-height: 1.5; color: var(--jd-text); }
</style>
