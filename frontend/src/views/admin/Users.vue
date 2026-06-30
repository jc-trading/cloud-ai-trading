<template>
  <div class="min-h-screen bg-gray-950 text-white p-6">
    <div class="max-w-7xl mx-auto">
      <!-- Header -->
      <div class="mb-8">
        <router-link to="/admin" class="text-blue-400 hover:text-blue-300 mb-4 inline-block">← Back to Admin</router-link>
        <h1 class="text-4xl font-bold text-white mb-2">User Management</h1>
        <p class="text-gray-400">Manage user accounts and permissions</p>
      </div>

      <!-- User Controls -->
      <div class="bg-gray-900 border border-gray-800 rounded-lg p-6 mb-8">
        <div class="flex flex-col md:flex-row gap-4 items-start md:items-center justify-between">
          <div class="flex gap-4 flex-1">
            <InputText
              v-model="searchQuery"
              placeholder="Search users by email or name..."
              class="flex-1"
            />
            <Dropdown
              v-model="filterRole"
              :options="roles"
              optionLabel="label"
              optionValue="value"
              placeholder="Filter by role"
              class="w-40"
            />
            <Dropdown
              v-model="filterStatus"
              :options="statusOptions"
              optionLabel="label"
              optionValue="value"
              placeholder="Filter by status"
              class="w-40"
            />
          </div>
          <Button label="Create User" icon="pi pi-plus"></Button>
        </div>
      </div>

      <!-- Users Table -->
      <div class="bg-gray-900 border border-gray-800 rounded-lg p-6 mb-8">
        <h2 class="text-xl font-bold text-white mb-4">All Users</h2>
        <DataTable :value="users" stripedRows responsiveLayout="scroll" paginator :rows="10" class="p-datatable-sm">
          <Column field="id" header="ID"></Column>
          <Column field="email" header="Email"></Column>
          <Column field="name" header="Name"></Column>
          <Column field="role" header="Role">
            <template #body="slotProps">
              <Tag
                :value="slotProps.data.role"
                :severity="getRoleSeverity(slotProps.data.role)"
              ></Tag>
            </template>
          </Column>
          <Column field="status" header="Status">
            <template #body="slotProps">
              <Tag
                :value="slotProps.data.status"
                :severity="slotProps.data.status === 'Active' ? 'success' : 'warning'"
              ></Tag>
            </template>
          </Column>
          <Column field="joinedDate" header="Joined"></Column>
          <Column field="lastLogin" header="Last Login"></Column>
          <Column header="Actions" :style="{ width: '180px' }">
            <template #body="slotProps">
              <div class="flex gap-2">
                <Button
                  icon="pi pi-pencil"
                  class="p-button-sm p-button-outlined"
                  @click="editUser(slotProps.data)"
                ></Button>
                <Button
                  icon="pi pi-trash"
                  class="p-button-sm p-button-danger p-button-outlined"
                  @click="deleteUser(slotProps.data.id)"
                ></Button>
              </div>
            </template>
          </Column>
        </DataTable>
        <div class="mt-6 text-center text-gray-400">
          <p>No users found</p>
        </div>
      </div>

      <!-- User Statistics -->
      <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div class="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <p class="text-gray-400 text-sm mb-2">Total Users</p>
          <p class="text-2xl font-bold">0</p>
        </div>
        <div class="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <p class="text-gray-400 text-sm mb-2">Active Users</p>
          <p class="text-2xl font-bold text-green-400">0</p>
        </div>
        <div class="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <p class="text-gray-400 text-sm mb-2">Inactive Users</p>
          <p class="text-2xl font-bold text-yellow-400">0</p>
        </div>
        <div class="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <p class="text-gray-400 text-sm mb-2">Admins</p>
          <p class="text-2xl font-bold">0</p>
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
  </div>
</template>

<script setup>
import { ref } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Dropdown from 'primevue/dropdown'
import Tag from 'primevue/tag'
import Dialog from 'primevue/dialog'

const searchQuery = ref('')
const filterRole = ref('')
const filterStatus = ref('')
const showEditDialog = ref(false)
const selectedUser = ref(null)

const users = ref([])

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

const getRoleSeverity = (role) => {
  switch (role) {
    case 'admin':
      return 'danger'
    case 'trader':
      return 'info'
    default:
      return 'success'
  }
}

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
:deep(.p-datatable) {
  background-color: transparent;
  color: #f3f4f6;
}

:deep(.p-datatable .p-datatable-thead > tr > th) {
  background-color: rgba(75, 85, 99, 0.5);
  color: #f3f4f6;
  border-color: #1f2937;
}

:deep(.p-datatable .p-datatable-tbody > tr) {
  border-color: #1f2937;
}

:deep(.p-datatable .p-datatable-tbody > tr > td) {
  border-color: #1f2937;
  color: #e5e7eb;
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

a {
  text-decoration: none;
}
</style>
