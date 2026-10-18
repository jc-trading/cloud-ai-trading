<template>
  <div class="notif">
    <!-- Channels -->
    <DataTable
      :columns="channelColumns"
      :data="channels"
      row-key="id"
      :page-size="10"
      :loading="loading"
      :error="loadError"
    >
      <template #toolbar-left>
        <h2 class="jd-card-title" style="margin-right: auto;">Notification channels</h2>
      </template>
      <template #toolbar-right>
        <button class="jd-btn jd-btn-primary jd-btn-sm" @click="openChannel()">
          <i class="pi pi-plus"></i> Add channel
        </button>
        <button class="jd-btn jd-btn-ghost jd-btn-sm" :disabled="loading" title="Refresh" @click="loadAll">
          <i class="pi pi-refresh" :class="{ 'pi-spin': loading }"></i>
        </button>
      </template>
      <template #cell:name="{ value }">
        <span class="font-semibold">{{ value }}</span>
      </template>
      <template #cell:type="{ value }">
        <span :class="['jd-badge', value === 'telegram' ? 'cyan' : 'purple']">{{ value }}</span>
      </template>
      <template #cell:enabled="{ row }">
        <label class="jd-toggle" :title="row.enabled ? 'Enabled' : 'Disabled'">
          <input type="checkbox" :checked="row.enabled" :disabled="busyId === row.id"
                 @change="toggleChannel(row, $event.target.checked)" />
          <span class="jd-toggle-slider"></span>
        </label>
      </template>
      <template #cell:secret_env_key="{ row }">
        <span class="jd-mono" style="font-size:12px">{{ row.secret_env_key }}</span>
        <span :class="['jd-badge', row.secret_present ? 'green' : 'red']" style="margin-left:6px">
          {{ row.secret_present ? 'set' : 'missing' }}
        </span>
      </template>
      <template #cell:target="{ row }">
        <span class="jd-mono" style="font-size:12px; color: var(--jd-text-muted)">{{ targetOf(row) }}</span>
      </template>
      <template #row-actions="{ row }">
        <div class="flex gap-2" style="justify-content:flex-end">
          <button class="jd-btn jd-btn-ghost jd-btn-sm" title="Send a test message"
                  :disabled="testingId === row.id" @click="sendTest(row)">
            <i class="pi" :class="testingId === row.id ? 'pi-spin pi-spinner' : 'pi-send'"></i> Test
          </button>
          <button class="jd-btn jd-btn-ghost jd-btn-sm" title="Edit" @click="openChannel(row)">
            <i class="pi pi-pencil"></i>
          </button>
          <button class="jd-btn jd-btn-danger jd-btn-sm" title="Delete"
                  :disabled="busyId === row.id" @click="removeChannel(row)">
            <i class="pi pi-trash"></i>
          </button>
        </div>
      </template>
      <template #empty>
        <div class="jd-empty"><i class="pi pi-bell"></i><p>No channels yet.</p></div>
      </template>
    </DataTable>

    <!-- Rules -->
    <DataTable
      :columns="ruleColumns"
      :data="rules"
      row-key="id"
      :page-size="15"
      :loading="loading"
    >
      <template #toolbar-left>
        <h2 class="jd-card-title" style="margin-right: auto;">Rules</h2>
      </template>
      <template #toolbar-right>
        <button class="jd-btn jd-btn-primary jd-btn-sm" :disabled="!channels.length" @click="openRule()">
          <i class="pi pi-plus"></i> Add rule
        </button>
      </template>
      <template #cell:event_type="{ value }">
        <span class="jd-mono" style="font-size:12px" :title="eventLabel(value)">{{ value }}</span>
      </template>
      <template #cell:min_severity="{ value }">
        <span :class="['jd-badge', severityColor(value)]">{{ value }}</span>
      </template>
      <template #cell:quiet_hours="{ value }">
        <span v-if="value" class="jd-mono" style="font-size:12px">{{ value.start }}–{{ value.end }} {{ value.tz }}</span>
        <span v-else style="color: var(--jd-text-faint)">—</span>
      </template>
      <template #cell:enabled="{ row }">
        <label class="jd-toggle" :title="row.enabled ? 'Enabled' : 'Disabled'">
          <input type="checkbox" :checked="row.enabled" :disabled="busyId === row.id"
                 @change="toggleRule(row, $event.target.checked)" />
          <span class="jd-toggle-slider"></span>
        </label>
      </template>
      <template #row-actions="{ row }">
        <div class="flex gap-2" style="justify-content:flex-end">
          <button class="jd-btn jd-btn-ghost jd-btn-sm" title="Edit" @click="openRule(row)">
            <i class="pi pi-pencil"></i>
          </button>
          <button class="jd-btn jd-btn-danger jd-btn-sm" title="Delete"
                  :disabled="busyId === row.id" @click="removeRule(row)">
            <i class="pi pi-trash"></i>
          </button>
        </div>
      </template>
      <template #empty>
        <div class="jd-empty"><i class="pi pi-sitemap"></i><p>No rules — no event will be delivered.</p></div>
      </template>
    </DataTable>

    <!-- Recent deliveries -->
    <DataTable
      :columns="logColumns"
      :data="log"
      row-key="id"
      :page-size="10"
      :loading="loading"
    >
      <template #toolbar-left>
        <h2 class="jd-card-title" style="margin-right: auto;">Recent deliveries</h2>
      </template>
      <template #cell:sent_at="{ value }">
        <span class="jd-mono" style="color: var(--jd-text-muted)">{{ fmtTime(value) }}</span>
      </template>
      <template #cell:severity="{ value }">
        <span :class="['jd-badge', severityColor(value)]">{{ value }}</span>
      </template>
      <template #cell:status="{ row }">
        <span :class="['jd-badge', statusColor(row.status)]" :title="row.error || ''">{{ row.status }}</span>
      </template>
      <template #cell:message_preview="{ row }">
        <span style="font-size:12px" :title="row.error || ''">{{ row.error || row.message_preview }}</span>
      </template>
      <template #empty>
        <div class="jd-empty"><i class="pi pi-inbox"></i><p>No deliveries yet.</p></div>
      </template>
    </DataTable>

    <!-- Channel modal -->
    <Modal v-model="showChannel" :title="channelForm.id ? 'Edit channel' : 'Add channel'" width="560px">
      <div class="jd-form-group">
        <div class="jd-label">Name</div>
        <input v-model.trim="channelForm.name" class="jd-input" maxlength="100" />
      </div>
      <div class="jd-form-group">
        <div class="jd-label">Type</div>
        <select v-model="channelForm.type" class="jd-select" :disabled="!!channelForm.id"
                @change="onTypeChange">
          <option v-for="t in channelTypes" :key="t" :value="t">{{ t }}</option>
        </select>
      </div>
      <div class="jd-form-group">
        <div class="jd-label">Secret env key</div>
        <input v-model.trim="channelForm.secret_env_key" class="jd-input jd-mono"
               :placeholder="channelForm.type === 'smtp' ? 'SMTP_APP_PASSWORD' : 'TELEGRAM_BOT_TOKEN'" />
      </div>

      <template v-if="channelForm.type === 'telegram'">
        <div class="jd-form-group">
          <div class="jd-label">Chat</div>
          <div class="row-2">
            <select v-model="channelForm.chatMode" class="jd-select">
              <option value="env">From env key</option>
              <option value="id">Chat id</option>
            </select>
            <input v-if="channelForm.chatMode === 'env'" v-model.trim="channelForm.chat_id_env"
                   class="jd-input jd-mono" placeholder="TELEGRAM_CHAT_ID" />
            <input v-else v-model.trim="channelForm.chat_id" class="jd-input jd-mono"
                   placeholder="-1001234567890 or @channel" />
          </div>
        </div>
      </template>
      <template v-else>
        <div class="jd-form-group row-2">
          <div>
            <div class="jd-label">SMTP host</div>
            <input v-model.trim="channelForm.host" class="jd-input jd-mono" placeholder="smtp.gmail.com" />
          </div>
          <div>
            <div class="jd-label">Port</div>
            <input v-model.number="channelForm.port" type="number" min="1" max="65535" class="jd-input jd-mono" />
          </div>
        </div>
        <div class="jd-form-group">
          <div class="jd-label">Username env key</div>
          <input v-model.trim="channelForm.username_env" class="jd-input jd-mono" placeholder="SMTP_USER" />
        </div>
        <div class="jd-form-group">
          <div class="jd-label">Recipients (comma-separated; empty = the SMTP user)</div>
          <input v-model="channelForm.to" class="jd-input" placeholder="me@example.com" />
        </div>
      </template>

      <div class="jd-form-group" style="display:flex; align-items:center; gap:10px">
        <label class="jd-toggle">
          <input v-model="channelForm.enabled" type="checkbox" />
          <span class="jd-toggle-slider"></span>
        </label>
        <span class="jd-label" style="margin:0">Enabled</span>
      </div>
      <p class="hint">
        <i class="pi pi-lock"></i>
        Secrets never go in the database — put the value in .env under this key, then recreate backend + worker.
      </p>
      <template #footer>
        <button class="jd-btn jd-btn-ghost jd-btn-sm" @click="showChannel = false">Cancel</button>
        <button class="jd-btn jd-btn-primary jd-btn-sm" :disabled="saving" @click="saveChannel">
          <i v-if="saving" class="pi pi-spin pi-spinner"></i> Save
        </button>
      </template>
    </Modal>

    <!-- Rule modal -->
    <Modal v-model="showRule" :title="ruleForm.id ? 'Edit rule' : 'Add rule'" width="560px">
      <div class="jd-form-group">
        <div class="jd-label">Event</div>
        <select v-model="ruleForm.event_type" class="jd-select" :disabled="!!ruleForm.id">
          <option v-for="e in eventTypes" :key="e.key" :value="e.key">{{ e.key }} — {{ e.description }}</option>
        </select>
      </div>
      <div class="jd-form-group">
        <div class="jd-label">Channel</div>
        <select v-model="ruleForm.channel_id" class="jd-select" :disabled="!!ruleForm.id">
          <option v-for="c in channels" :key="c.id" :value="c.id">{{ c.name }} ({{ c.type }})</option>
        </select>
      </div>
      <div class="jd-form-group">
        <div class="jd-label">Minimum severity</div>
        <select v-model="ruleForm.min_severity" class="jd-select">
          <option v-for="s in severities" :key="s" :value="s">{{ s }}</option>
        </select>
      </div>
      <div class="jd-form-group">
        <label style="display:flex; align-items:center; gap:8px; cursor:pointer">
          <input v-model="ruleForm.quiet" type="checkbox" />
          <span class="jd-label" style="margin:0">Quiet hours (critical still goes through)</span>
        </label>
        <div v-if="ruleForm.quiet" class="row-3" style="margin-top:10px">
          <input v-model="ruleForm.start" type="time" class="jd-input jd-mono" />
          <input v-model="ruleForm.end" type="time" class="jd-input jd-mono" />
          <input v-model.trim="ruleForm.tz" class="jd-input jd-mono" placeholder="Asia/Kuala_Lumpur" />
        </div>
      </div>
      <div class="jd-form-group" style="display:flex; align-items:center; gap:10px">
        <label class="jd-toggle">
          <input v-model="ruleForm.enabled" type="checkbox" />
          <span class="jd-toggle-slider"></span>
        </label>
        <span class="jd-label" style="margin:0">Enabled</span>
      </div>
      <template #footer>
        <button class="jd-btn jd-btn-ghost jd-btn-sm" @click="showRule = false">Cancel</button>
        <button class="jd-btn jd-btn-primary jd-btn-sm" :disabled="saving" @click="saveRule">
          <i v-if="saving" class="pi pi-spin pi-spinner"></i> Save
        </button>
      </template>
    </Modal>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import DataTable from '@/components/common/DataTable.vue'
import Modal from '@/components/common/Modal.vue'
import { useToast } from '@/composables/useToast'
import {
  getCatalog, listChannels, createChannel, updateChannel, deleteChannel, testChannel,
  listRules, createRule, updateRule, deleteRule, getNotificationLog,
} from '@/api/notifications'

const toast = useToast()

const catalog = ref({ event_types: [], severities: [], channel_types: [] })
const channels = ref([])
const rules = ref([])
const log = ref([])
const loading = ref(false)
const loadError = ref(null)
const saving = ref(false)
const busyId = ref(null)
const testingId = ref(null)

const eventTypes = computed(() => catalog.value.event_types || [])
const severities = computed(() => catalog.value.severities || [])
const channelTypes = computed(() => catalog.value.channel_types || [])

const channelColumns = [
  { key: 'name', header: 'Name', sortable: true },
  { key: 'type', header: 'Type' },
  { key: 'enabled', header: 'On', align: 'center' },
  { key: 'secret_env_key', header: 'Secret env key' },
  { key: 'target', header: 'Target', accessor: (r) => targetOf(r) },
]
const ruleColumns = [
  { key: 'event_type', header: 'Event', sortable: true, filterable: true, filterLabel: 'Event' },
  { key: 'channel_name', header: 'Channel', sortable: true, filterable: true, filterLabel: 'Channel' },
  { key: 'min_severity', header: 'Min severity' },
  { key: 'quiet_hours', header: 'Quiet hours' },
  { key: 'enabled', header: 'On', align: 'center' },
]
const logColumns = [
  { key: 'sent_at', header: 'Time', sortable: true },
  { key: 'channel_name', header: 'Channel', filterable: true, filterLabel: 'Channel' },
  { key: 'event_type', header: 'Event', filterable: true, filterLabel: 'Event' },
  { key: 'severity', header: 'Severity' },
  { key: 'status', header: 'Status', filterable: true, filterLabel: 'Status' },
  { key: 'retry_count', header: 'Retries', align: 'right' },
  { key: 'message_preview', header: 'Message / error' },
]

const detailOf = (e, fallback) => {
  const d = e?.response?.data?.detail
  if (!d) return fallback
  return Array.isArray(d) ? d.map((x) => x.msg).join('; ') : d
}

const targetOf = (c) => {
  const cfg = c.config_json || {}
  if (c.type === 'telegram') return cfg.chat_id || (cfg.chat_id_env ? `env:${cfg.chat_id_env}` : '—')
  const to = (cfg.to || []).join(', ') || (cfg.username_env ? `env:${cfg.username_env}` : '—')
  return `${cfg.host || '?'}:${cfg.port || '?'} → ${to}`
}
const eventLabel = (key) => eventTypes.value.find((e) => e.key === key)?.description || key
const severityColor = (s) => ({ info: 'blue', warning: 'yellow', critical: 'red' }[s] || 'gray')
const statusColor = (s) => ({ sent: 'green', failed: 'red' }[s] || 'gray')
const fmtTime = (iso) => {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

async function loadAll() {
  loading.value = true
  loadError.value = null
  try {
    const [cat, ch, ru, lg] = await Promise.all([
      getCatalog(), listChannels(), listRules(), getNotificationLog(50),
    ])
    catalog.value = cat.data
    channels.value = ch.data
    rules.value = ru.data
    log.value = lg.data
  } catch (e) {
    loadError.value = detailOf(e, 'Failed to load notification settings')
  } finally {
    loading.value = false
  }
}

async function refreshLog() {
  try {
    log.value = (await getNotificationLog(50)).data
  } catch { /* the table keeps its last rows */ }
}

// ── Channels ────────────────────────────────────────────────────
const showChannel = ref(false)
const channelForm = reactive({})

function openChannel(row = null) {
  const cfg = row?.config_json || {}
  Object.assign(channelForm, {
    id: row?.id || null,
    name: row?.name || '',
    type: row?.type || 'telegram',
    secret_env_key: row?.secret_env_key || 'TELEGRAM_BOT_TOKEN',
    enabled: row ? row.enabled : true,
    chatMode: cfg.chat_id ? 'id' : 'env',
    chat_id: cfg.chat_id || '',
    chat_id_env: cfg.chat_id_env || 'TELEGRAM_CHAT_ID',
    host: cfg.host || 'smtp.gmail.com',
    port: cfg.port || 587,
    username_env: cfg.username_env || 'SMTP_USER',
    to: (cfg.to || []).join(', '),
  })
  showChannel.value = true
}

function onTypeChange() {
  channelForm.secret_env_key = channelForm.type === 'smtp' ? 'SMTP_APP_PASSWORD' : 'TELEGRAM_BOT_TOKEN'
}

function channelConfig() {
  if (channelForm.type === 'telegram') {
    return channelForm.chatMode === 'id'
      ? { chat_id: channelForm.chat_id }
      : { chat_id_env: channelForm.chat_id_env }
  }
  return {
    host: channelForm.host,
    port: channelForm.port,
    username_env: channelForm.username_env,
    to: channelForm.to.split(',').map((s) => s.trim()).filter(Boolean),
  }
}

async function saveChannel() {
  saving.value = true
  const body = {
    name: channelForm.name,
    config_json: channelConfig(),
    secret_env_key: channelForm.secret_env_key,
    enabled: channelForm.enabled,
  }
  try {
    if (channelForm.id) await updateChannel(channelForm.id, body)
    else await createChannel({ ...body, type: channelForm.type })
    showChannel.value = false
    toast.success(`Channel ${channelForm.name} saved`)
    await loadAll()
  } catch (e) {
    toast.error(detailOf(e, 'Failed to save channel'))
  } finally {
    saving.value = false
  }
}

async function toggleChannel(row, enabled) {
  busyId.value = row.id
  try {
    const { data } = await updateChannel(row.id, { enabled })
    Object.assign(row, data)
  } catch (e) {
    toast.error(detailOf(e, 'Failed to update channel'))
    await loadAll()
  } finally {
    busyId.value = null
  }
}

async function removeChannel(row) {
  if (!window.confirm(`Delete channel "${row.name}" and all its rules?`)) return
  busyId.value = row.id
  try {
    await deleteChannel(row.id)
    toast.success(`Channel ${row.name} deleted`)
    await loadAll()
  } catch (e) {
    toast.error(detailOf(e, 'Failed to delete channel'))
  } finally {
    busyId.value = null
  }
}

async function sendTest(row) {
  testingId.value = row.id
  try {
    const { data } = await testChannel(row.id)
    if (data.status === 'sent') toast.success(`Test message sent via ${row.name}`)
    else toast.error(data.error || 'Test failed', `Test via ${row.name} failed`)
  } catch (e) {
    toast.error(detailOf(e, 'Test request failed'))
  } finally {
    testingId.value = null
    await refreshLog()
  }
}

// ── Rules ───────────────────────────────────────────────────────
const showRule = ref(false)
const ruleForm = reactive({})
const browserTz = () => Intl.DateTimeFormat().resolvedOptions().timeZone

function openRule(row = null) {
  const qh = row?.quiet_hours
  Object.assign(ruleForm, {
    id: row?.id || null,
    event_type: row?.event_type || eventTypes.value[0]?.key || '',
    channel_id: row?.channel_id || channels.value[0]?.id || '',
    min_severity: row?.min_severity || 'info',
    enabled: row ? row.enabled : true,
    quiet: !!qh,
    start: qh?.start || '23:00',
    end: qh?.end || '07:00',
    tz: qh?.tz || browserTz(),
  })
  showRule.value = true
}

async function saveRule() {
  saving.value = true
  const body = {
    min_severity: ruleForm.min_severity,
    quiet_hours: ruleForm.quiet ? { start: ruleForm.start, end: ruleForm.end, tz: ruleForm.tz } : null,
    enabled: ruleForm.enabled,
  }
  try {
    if (ruleForm.id) await updateRule(ruleForm.id, body)
    else await createRule({ ...body, event_type: ruleForm.event_type, channel_id: ruleForm.channel_id })
    showRule.value = false
    toast.success('Rule saved')
    await loadAll()
  } catch (e) {
    toast.error(detailOf(e, 'Failed to save rule'))
  } finally {
    saving.value = false
  }
}

async function toggleRule(row, enabled) {
  busyId.value = row.id
  try {
    const { data } = await updateRule(row.id, { enabled })
    Object.assign(row, data)
  } catch (e) {
    toast.error(detailOf(e, 'Failed to update rule'))
    await loadAll()
  } finally {
    busyId.value = null
  }
}

async function removeRule(row) {
  if (!window.confirm(`Delete the ${row.event_type} rule on "${row.channel_name}"?`)) return
  busyId.value = row.id
  try {
    await deleteRule(row.id)
    await loadAll()
  } catch (e) {
    toast.error(detailOf(e, 'Failed to delete rule'))
  } finally {
    busyId.value = null
  }
}

onMounted(loadAll)
</script>

<style scoped>
.notif { display: flex; flex-direction: column; gap: 20px; }
.row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.row-3 { display: grid; grid-template-columns: 1fr 1fr 1.4fr; gap: 10px; }
.hint {
  display: flex; gap: 8px; align-items: flex-start;
  font-size: 12px; color: var(--jd-text-muted); margin: 4px 0 0;
}
@media (max-width: 560px) {
  .row-2, .row-3 { grid-template-columns: 1fr; }
}
</style>
