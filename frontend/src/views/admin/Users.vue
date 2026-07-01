<template>
  <div class="jd-page">
    <!-- Header -->
    <div>
      <router-link to="/admin" class="jd-back-link">← Back to Admin</router-link>
      <h1 class="jd-page-title" style="font-size: 28px; margin-top: 12px;">User Management</h1>
      <p class="jd-page-desc">Manage user accounts and permissions</p>
    </div>

    <!-- Users Table -->
    <DataTable
      :columns="userColumns"
      :data="users"
      :searchable="['email', 'name']"
      search-placeholder="Search users by email or name…"
      :page-size="10"
      empty-text="No users found"
    >
      <template #toolbar-right>
        <button class="jd-btn jd-btn-primary jd-btn-sm">
          <i class="pi pi-plus"></i> Create User
        </button>
      </template>
      <template #cell:role="{ value }">
        <span class="jd-badge" :class="roleColor(value)">{{ value }}</span>
      </template>
      <template #cell:status="{ value }">
        <span class="jd-badge" :class="statusColor(value)">{{ value }}</span>
      </template>
      <template #row-actions="{ row }">
        <div class="jd-row-actions">
          <button class="jd-btn jd-btn-ghost jd-btn-sm" title="Edit user" @click="editUser(row)">
            <i class="pi pi-pencil"></i>
          </button>
          <button class="jd-btn jd-btn-danger jd-btn-sm" title="Delete user" @click="deleteUser(row.id)">
            <i class="pi pi-trash"></i>
          </button>
        </div>
      </template>
    </DataTable>

    <!-- User Statistics -->
    <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
      <div class="jd-stat-card cyan">
        <div class="jd-stat-label">Total Users</div>
        <div class="jd-stat-value">0</div>
      </div>
      <div class="jd-stat-card green">
        <div class="jd-stat-label">Active Users</div>
        <div class="jd-stat-value" style="color: var(--jd-green)">0</div>
      </div>
      <div class="jd-stat-card yellow">
        <div class="jd-stat-label">Inactive Users</div>
        <div class="jd-stat-value" style="color: var(--jd-yellow)">0</div>
      </div>
      <div class="jd-stat-card purple">
        <div class="jd-stat-label">Admins</div>
        <div class="jd-stat-value">0</div>
      </div>
    </div>

    <!-- Edit User Dialog (Placeholder) -->
    <Dialog
      v-model:visible="showEditDialog"
      header="Edit User"
      :modal="true"
      class="p-dialog"
    >
      <div v-if="selectedUser" class="space-y-4">
        <div>
          <label class="block text-gray-400 text-sm mb-2">Email</label>
          <InputText v-model="selectedUser.email" class="w-full" />
        </div>
        <div>
          <label class="block text-gray-400 text-sm mb-2">Name</label>
          <InputText v-model="selectedUser.name" class="w-full" />
        </div>
        <div>
          <label class="block text-gray-400 text-sm mb-2">Role</label>
          <Dropdown
            v-model="selectedUser.role"
            :options="roles"
            optionLabel="label"
            optionValue="value"
            class="w-full"
          />
        </div>
        <div>
          <label class="block text-gray-400 text-sm mb-2">Status</label>
          <Dropdown
            v-model="selectedUser.status"
            :options="statusOptions"
            optionLabel="label"
            optionValue="value"
            class="w-full"
          />
        </div>
      </div>
      <template #footer>
        <Button label="Cancel" icon="pi pi-times" class="p-button-outlined" @click="showEditDialog = false"></Button>
        <Button label="Save" icon="pi pi-check" @click="saveUser"></Button>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import DataTable from '@/components/common/DataTable.vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Dropdown from 'primevue/dropdown'
import Dialog from 'primevue/dialog'

const showEditDialog = ref(false)
const selectedUser = ref(null)

const users = ref([])

const userColumns = [
  { key: 'id', header: 'ID', sortable: true, align: 'right' },
  { key: 'email', header: 'Email', sortable: true },
  { key: 'name', header: 'Name', sortable: true },
  { key: 'role', header: 'Role', sortable: true, filterable: true, filterLabel: 'Role' },
  { key: 'status', header: 'Status', sortable: true, filterable: true, filterLabel: 'Status' },
  { key: 'joinedDate', header: 'Joined', sortable: true },
  { key: 'lastLogin', header: 'Last Login', sortable: true },
]

const roles = ref([
  { label: 'User', value: 'user' },
  { label: 'Trader', value: 'trader' },
  { label: 'Admin', value: 'admin' }
])

const statusOptions = ref([
  { label: 'Active', value: 'Active' },
  { label: 'Inactive', value: 'Inactive' },
  { label: 'Suspended', value: 'Suspended' }
])

const roleColor = (role) => ({ admin: 'purple', trader: 'blue', user: 'gray' }[role] || 'gray')
const statusColor = (status) =>
  ({ Active: 'green', Inactive: 'yellow', Suspended: 'red' }[status] || 'gray')

const editUser = (user) => {
  selectedUser.value = { ...user }
  showEditDialog.value = true
}

const deleteUser = (id) => {
  console.log('Delete user:', id)
}

const saveUser = () => {
  console.log('Save user:', selectedUser.value)
  showEditDialog.value = false
}
</script>

<style scoped>
.jd-back-link { color: var(--jd-cyan); font-size: 13px; text-decoration: none; }
.jd-back-link:hover { color: var(--jd-text); }
.jd-row-actions { display: flex; gap: 8px; }

:deep(.p-dialog) {
  background-color: #1f2937;
}

:deep(.p-dialog .p-dialog-header) {
  background-color: #111827;
  border-color: #374151;
  color: #f3f4f6;
}

:deep(.p-dialog .p-dialog-content) {
  background-color: #111827;
  color: #f3f4f6;
}

:deep(.p-inputtext) {
  background-color: rgba(75, 85, 99, 0.5);
  border-color: #374151;
  color: #f3f4f6;
}

:deep(.p-inputtext:focus) {
  border-color: #3b82f6;
  box-shadow: 0 0 0 0.2rem rgba(59, 130, 246, 0.25);
}

:deep(.p-dropdown) {
  background-color: rgba(75, 85, 99, 0.5);
  border-color: #374151;
  color: #f3f4f6;
}

:deep(.p-dropdown .p-dropdown-trigger) {
  color: #9ca3af;
}

:deep(.p-button) {
  background-color: #3b82f6;
  border-color: #3b82f6;
}

:deep(.p-button:hover) {
  background-color: #2563eb;
}

a {
  text-decoration: none;
}
</style>
