<template>
  <div class="jd-page">
    <!-- Header -->
    <div>
      <h1 class="jd-section-title" style="margin-bottom: 8px;">Settings</h1>
      <p style="color: var(--jd-text-muted); font-size: 14px;">Your account information</p>
    </div>

    <!-- Profile Card (read-only, real logged-in user) -->
    <div class="jd-card">
      <div class="jd-card-header">
        <h2 class="jd-card-title">Profile Information</h2>
      </div>
      <div class="jd-card-body">
        <div class="profile-grid">
          <div class="profile-field">
            <div class="jd-label">Full Name</div>
            <div class="profile-value">{{ user?.name || '—' }}</div>
          </div>
          <div class="profile-field">
            <div class="jd-label">Email Address</div>
            <div class="profile-value">{{ user?.email || '—' }}</div>
          </div>
          <div class="profile-field">
            <div class="jd-label">Role</div>
            <div class="profile-value" style="text-transform: capitalize;">
              {{ (user?.role || 'user').replace('_', ' ') }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- API Keys Card -->
    <div class="jd-card">
      <div class="jd-card-header">
        <h2 class="jd-card-title">API Keys</h2>
      </div>
      <div class="jd-card-body">
        <p style="font-size: 13px; color: var(--jd-text-muted); margin-bottom: 12px;">
          Connect your Alpaca account for US stock market data.
        </p>
        <router-link to="/settings/exchange" class="jd-btn jd-btn-primary jd-btn-sm" style="display: inline-block; text-decoration: none;">
          Manage API Keys
        </router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()
const user = computed(() => authStore.user)
</script>

<style scoped>
.profile-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 20px;
}

.jd-section-title {
  font-size: 28px;
  font-weight: 700;
  color: var(--jd-text);
}

.jd-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--jd-text-muted);
  margin-bottom: 6px;
}

.profile-value {
  font-size: 14px;
  color: var(--jd-text);
}

.jd-btn {
  padding: 8px 16px;
  font-size: 13px;
  border-radius: 4px;
  font-weight: 500;
  cursor: pointer;
}

.jd-btn-primary {
  background-color: var(--jd-blue);
  color: white;
  border: 1px solid var(--jd-blue);
}

.jd-btn-primary:hover {
  background-color: #2563eb;
  border-color: #2563eb;
}

.jd-btn-sm {
  padding: 6px 12px;
  font-size: 12px;
}
</style>
