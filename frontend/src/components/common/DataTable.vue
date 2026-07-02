<!--
  DataTable — reusable table with a toolbar (keyword search + faceted filters),
  sortable columns, and pagination. Powered by @tanstack/vue-table (headless);
  rendered in the Oscilloscope skin via global .jd-* classes. The public API is
  engine-agnostic so the engine can be swapped without touching call sites.

  columns: Array<{
    key: string            // unique id + accessor key
    header: string
    accessor?: (row)=>any  // custom accessor (overrides key lookup)
    sortable?: boolean
    align?: 'left'|'right'|'center'
    filterable?: boolean   // include in faceted filter bar
    filterLabel?: string   // label for its filter select
    filterOptions?: {label,value}[]  // else derived from distinct values
  }>
  Slots: cell:<key> ({row,value}) · row-actions ({row}) · toolbar-left · toolbar-right · empty · loading
-->
<template>
  <div class="jd-card jd-datatable">
    <!-- toolbar -->
    <div class="jd-toolbar" v-if="searchable?.length || facetCols.length || slots['toolbar-left'] || slots['toolbar-right']">
      <slot name="toolbar-left" />
      <label class="jd-search" v-if="searchable?.length">
        <i class="pi pi-search ic"></i>
        <input :value="globalFilter" @input="globalFilter = $event.target.value" :placeholder="searchPlaceholder" />
      </label>
      <select
        v-for="f in facetCols" :key="f.key"
        class="jd-select jd-facet"
        :value="columnFilter(f.key) ?? ''"
        @change="setColumnFilter(f.key, $event.target.value)"
      >
        <option value="">{{ f.filterLabel || f.header }} · All</option>
        <option v-for="o in facetOptions(f)" :key="String(o.value)" :value="o.value">{{ o.label }}</option>
      </select>
      <slot name="toolbar-right" />
      <button v-if="hasActiveFilters" class="jd-btn jd-btn-ghost jd-btn-sm" @click="resetFilters">Reset</button>
    </div>

    <!-- loading -->
    <div v-if="loading" class="jd-dt-state">
      <slot name="loading"><span class="judia-spinner" style="width:26px;height:26px"></span></slot>
    </div>

    <!-- error -->
    <div v-else-if="error" class="jd-dt-state jd-dt-error">{{ error }}</div>

    <!-- table -->
    <div v-else class="jd-dt-scroll">
      <table class="jd-table">
        <thead>
          <tr>
            <th
              v-for="col in columns" :key="col.key"
              :class="thClass(col)"
              @click="col.sortable && toggleSort(col.key)"
            >
              {{ col.header }}
              <span v-if="col.sortable" class="jd-sort">{{ sortIcon(col.key) }}</span>
            </th>
            <th v-if="slots['row-actions']" class="th-right">&nbsp;</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows" :key="rowKeyOf(row.original)"
            :class="{ clickable: clickableRows }"
            @click="clickableRows && $emit('row-click', row.original)"
          >
            <td v-for="col in columns" :key="col.key" :class="tdClass(col)">
              <slot :name="`cell:${col.key}`" :row="row.original" :value="row.getValue(col.key)">
                {{ formatCell(row.getValue(col.key)) }}
              </slot>
            </td>
            <td v-if="slots['row-actions']" class="td-right" @click.stop>
              <slot name="row-actions" :row="row.original" />
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td :colspan="colSpan" class="jd-dt-empty">
              <slot name="empty"><span>{{ emptyText }}</span></slot>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- pager -->
    <div v-if="pagination && !loading && !error && total > 0" class="jd-pager">
      <span>Showing {{ rangeStart }}–{{ rangeEnd }} of {{ total }}</span>
      <div class="pg">
        <select class="jd-select jd-pagesize" :value="pageSizeState" @change="setPageSize($event.target.value)">
          <option v-for="s in pageSizeOptions" :key="s" :value="s">{{ s }} / page</option>
        </select>
        <button :disabled="!table.getCanPreviousPage()" @click="table.previousPage()">‹</button>
        <button
          v-for="p in pageWindow" :key="p"
          :class="{ on: p - 1 === pageIndex }"
          @click="table.setPageIndex(p - 1)"
        >{{ p }}</button>
        <button :disabled="!table.getCanNextPage()" @click="table.nextPage()">›</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, useSlots } from 'vue'
import {
  useVueTable,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  getPaginationRowModel,
} from '@tanstack/vue-table'

const props = defineProps({
  columns: { type: Array, required: true },
  data: { type: Array, default: () => [] },
  rowKey: { type: [String, Function], default: 'id' },
  searchable: { type: Array, default: () => [] },
  searchPlaceholder: { type: String, default: 'Search…' },
  pagination: { type: Boolean, default: true },
  pageSize: { type: Number, default: 10 },
  pageSizeOptions: { type: Array, default: () => [10, 25, 50] },
  clickableRows: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  error: { type: [String, null], default: null },
  emptyText: { type: String, default: 'No records' },
})
defineEmits(['row-click'])
const slots = useSlots()

const globalFilter = ref('')
const sorting = ref([])
const columnFilters = ref([])
const pageState = ref({ pageIndex: 0, pageSize: props.pageSize })

const searchableSet = computed(() => new Set(props.searchable))

const tanstackColumns = computed(() =>
  props.columns.map((c) => {
    const base = {
      id: c.key,
      header: c.header,
      enableSorting: !!c.sortable,
      enableGlobalFilter: searchableSet.value.has(c.key),
      enableColumnFilter: !!c.filterable,
      filterFn: 'equalsString',
    }
    return c.accessor ? { ...base, accessorFn: c.accessor } : { ...base, accessorKey: c.key }
  })
)

const table = useVueTable({
  get data() { return props.data },
  get columns() { return tanstackColumns.value },
  state: {
    get globalFilter() { return globalFilter.value },
    get sorting() { return sorting.value },
    get columnFilters() { return columnFilters.value },
    get pagination() { return pageState.value },
  },
  globalFilterFn: 'includesString',
  onGlobalFilterChange: (u) => { globalFilter.value = typeof u === 'function' ? u(globalFilter.value) : u },
  onSortingChange: (u) => { sorting.value = typeof u === 'function' ? u(sorting.value) : u },
  onColumnFiltersChange: (u) => { columnFilters.value = typeof u === 'function' ? u(columnFilters.value) : u },
  onPaginationChange: (u) => { pageState.value = typeof u === 'function' ? u(pageState.value) : u },
  getCoreRowModel: getCoreRowModel(),
  getFilteredRowModel: getFilteredRowModel(),
  getSortedRowModel: getSortedRowModel(),
  getPaginationRowModel: props.pagination ? getPaginationRowModel() : undefined,
})

const rows = computed(() => table.getRowModel().rows)
const total = computed(() => table.getFilteredRowModel().rows.length)
const pageIndex = computed(() => pageState.value.pageIndex)
const pageSizeState = computed(() => pageState.value.pageSize)
const rangeStart = computed(() => (total.value === 0 ? 0 : pageIndex.value * pageSizeState.value + 1))
const rangeEnd = computed(() => Math.min(total.value, (pageIndex.value + 1) * pageSizeState.value))

// compact page-number window (max 5)
const pageWindow = computed(() => {
  const count = table.getPageCount()
  const cur = pageIndex.value + 1
  const win = []
  const start = Math.max(1, Math.min(cur - 2, count - 4))
  const end = Math.min(count, start + 4)
  for (let p = start; p <= end; p++) win.push(p)
  return win
})

const facetCols = computed(() => props.columns.filter((c) => c.filterable))
const facetOptions = (col) => {
  if (col.filterOptions?.length) return col.filterOptions
  const seen = new Set()
  const opts = []
  for (const r of props.data) {
    const v = col.accessor ? col.accessor(r) : r[col.key]
    if (v === null || v === undefined || v === '') continue
    const key = String(v)
    if (seen.has(key)) continue
    seen.add(key)
    opts.push({ label: key, value: key })
  }
  return opts.sort((a, b) => a.label.localeCompare(b.label))
}

const columnFilter = (key) => table.getColumn(key)?.getFilterValue()
const setColumnFilter = (key, val) => table.getColumn(key)?.setFilterValue(val === '' ? undefined : val)
const toggleSort = (key) => table.getColumn(key)?.toggleSorting()
const sortIcon = (key) => {
  const s = table.getColumn(key)?.getIsSorted()
  return s === 'asc' ? '▲' : s === 'desc' ? '▼' : '↕'
}
const setPageSize = (v) => table.setPageSize(Number(v))

const hasActiveFilters = computed(() => globalFilter.value !== '' || columnFilters.value.length > 0)
const resetFilters = () => { globalFilter.value = ''; columnFilters.value = [] }

const rowKeyOf = (row) => (typeof props.rowKey === 'function' ? props.rowKey(row) : row[props.rowKey])
const colSpan = computed(() => props.columns.length + (slots['row-actions'] ? 1 : 0))

const thClass = (col) => [alignClass(col.align), { sortable: col.sortable }]
const tdClass = (col) => alignClass(col.align)
const alignClass = (align) => (align === 'right' ? 'td-right th-right' : align === 'center' ? 'td-center th-center' : '')

const formatCell = (v) => (v === null || v === undefined ? '—' : v)
</script>

<style scoped>
.jd-datatable { overflow: hidden; }
.jd-dt-scroll { overflow-x: auto; }
.jd-facet { padding: 8px 30px 8px 12px; font-size: 12px; }
.jd-pagesize { padding: 4px 26px 4px 10px; font-size: 11px; font-family: var(--jd-mono); }
.jd-sort { color: var(--jd-cyan); font-size: 9px; margin-left: 3px; }
.jd-table th.sortable:hover { color: var(--jd-text); }
tr.clickable { cursor: pointer; }
.jd-dt-state { padding: 40px; display: flex; justify-content: center; color: var(--jd-text-muted); }
.jd-dt-error { color: var(--jd-red); font-size: 13px; }
.jd-dt-empty { text-align: center; padding: 36px 16px; color: var(--jd-text-muted); }
</style>
