<template>
  <div class="jd-auth-bg">
    <div class="jd-auth-card">
      <!-- Logo and Title -->
      <div class="auth-header">
        <div class="auth-logo">
          <i class="pi pi-user-plus"></i>
        </div>
        <h1 class="jd-gradient-text auth-title">CloudAI Trading</h1>
        <p class="auth-subtitle">Create your account</p>
      </div>

      <!-- Secure Badge -->
      <div class="secure-badge">
        <i class="pi pi-lock"></i>
        <span>Secure Connection</span>
      </div>

      <!-- Form -->
      <form @submit.prevent="handleRegister" class="auth-form">
        <!-- Name Field -->
        <div class="jd-form-group">
          <label class="jd-label">Full Name</label>
          <a-input
            v-model:value="form.name"
            placeholder="Your full name"
            class="jd-input"
          />
        </div>

        <!-- Email Field -->
        <div class="jd-form-group">
          <label class="jd-label">Email</label>
          <a-input
            v-model:value="form.email"
            placeholder="your@email.com"
            type="email"
            class="jd-input"
          />
        </div>

        <!-- Password Field -->
        <div class="jd-form-group">
          <label class="jd-label">Password</label>
          <a-input-password
            v-model:value="form.password"
            placeholder="Min 8 characters"
            class="jd-input"
          />
        </div>

        <!-- Timezone Field -->
        <div class="jd-form-group">
          <label class="jd-label">Timezone</label>
          <a-select
            v-model:value="form.timezone"
            :options="timezones"
            placeholder="Select your timezone"
            class="jd-input"
          />
        </div>

        <!-- Error Alert -->
        <a-alert
          v-if="errorMsg"
          :message="errorMsg"
          type="error"
          show-icon
          closable
          @close="errorMsg = ''"
          class="error-alert"
        />

        <!-- Sign Up Button -->
        <a-button
          type="primary"
          html-type="submit"
          block
          class="jd-btn jd-btn-primary jd-btn-lg auth-button"
        >
          Create Account
        </a-button>
      </form>

      <!-- Sign In Link -->
      <div class="auth-footer">
        <p>Already have an account?
          <router-link to="/login" class="link">Sign In</router-link>
        </p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const errorMsg = ref('')

const form = reactive({
  name: '',
  email: '',
  password: '',
  timezone: 'Asia/Kuala_Lumpur',
  country: 'Malaysia',
  language: 'en',
  currency: 'USD',
})

const timezones = [
  { label: 'Malaysia (GMT+8)', value: 'Asia/Kuala_Lumpur' },
  { label: 'Singapore (GMT+8)', value: 'Asia/Singapore' },
  { label: 'Japan (GMT+9)', value: 'Asia/Tokyo' },
  { label: 'China (GMT+8)', value: 'Asia/Shanghai' },
  { label: 'Hong Kong (GMT+8)', value: 'Asia/Hong_Kong' },
  { label: 'Taiwan (GMT+8)', value: 'Asia/Taipei' },
  { label: 'Thailand (GMT+7)', value: 'Asia/Bangkok' },
  { label: 'Indonesia (GMT+7)', value: 'Asia/Jakarta' },
  { label: 'US Eastern (GMT-5)', value: 'America/New_York' },
  { label: 'US Pacific (GMT-8)', value: 'America/Los_Angeles' },
  { label: 'UK (GMT+0)', value: 'Europe/London' },
  { label: 'UTC', value: 'UTC' },
]

async function handleRegister() {
  errorMsg.value = ''
  if (!form.name || !form.email || !form.password) {
    errorMsg.value = 'All fields are required'
    return
  }
  if (form.password.length < 8) {
    errorMsg.value = 'Password must be at least 8 characters'
    return
  }

  try {
    await auth.register(form)
    router.push('/')
  } catch (err) {
    errorMsg.value = err.response?.data?.detail || 'Registration failed.'
  }
}
</script>

<style scoped>
.auth-header {
  text-align: center;
  margin-bottom: 32px;
}

.auth-logo {
  display: flex;
  justify-content: center;
  margin-bottom: 16px;
}

.auth-logo i {
  font-size: 42px;
  background: linear-gradient(135deg, var(--jd-blue), var(--jd-purple));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.auth-title {
  font-size: 28px;
  font-weight: 800;
  margin: 0 0 8px 0;
  letter-spacing: -0.5px;
}

.auth-subtitle {
  font-size: 13px;
  color: var(--jd-text-muted);
  margin: 0;
  letter-spacing: 0.3px;
}

.secure-badge {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 16px;
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid rgba(16, 185, 129, 0.2);
  border-radius: 6px;
  margin-bottom: 24px;
  font-size: 12px;
  color: var(--jd-green);
  font-weight: 500;
}

.secure-badge i {
  font-size: 14px;
}

.auth-form {
  margin-bottom: 20px;
}

.jd-form-group {
  margin-bottom: 16px;
}

:deep(.ant-input) {
  background: var(--jd-input) !important;
  border: 1px solid var(--jd-border) !important;
  color: var(--jd-text) !important;
  height: 40px !important;
  padding: 8px 12px !important;
  border-radius: 8px !important;
  transition: all var(--jd-trans) !important;
  font-size: 14px !important;
}

:deep(.ant-input::placeholder) {
  color: var(--jd-text-faint) !important;
}

:deep(.ant-input:hover) {
  border-color: var(--jd-border-hover) !important;
}

:deep(.ant-input:focus) {
  border-color: var(--jd-blue) !important;
  background: var(--jd-input) !important;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.1) !important;
}

:deep(.ant-input-password) {
  background: var(--jd-input) !important;
  border: 1px solid var(--jd-border) !important;
  border-radius: 8px !important;
}

:deep(.ant-input-password input) {
  background: transparent !important;
  color: var(--jd-text) !important;
  height: 40px !important;
  padding: 8px 12px !important;
  font-size: 14px !important;
}

:deep(.ant-input-password-icon) {
  color: var(--jd-text-muted) !important;
}

:deep(.ant-select) {
  width: 100% !important;
}

:deep(.ant-select-selector) {
  background: var(--jd-input) !important;
  border: 1px solid var(--jd-border) !important;
  border-radius: 8px !important;
  height: 40px !important;
}

:deep(.ant-select-arrow) {
  color: var(--jd-text-muted) !important;
}

:deep(.ant-select-selection-item) {
  color: var(--jd-text) !important;
}

:deep(.ant-select:hover .ant-select-selector) {
  border-color: var(--jd-border-hover) !important;
}

:deep(.ant-select-focused .ant-select-selector) {
  border-color: var(--jd-blue) !important;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.1) !important;
}

:deep(.ant-select-dropdown) {
  background: var(--jd-card) !important;
}

:deep(.ant-select-item) {
  color: var(--jd-text) !important;
}

:deep(.ant-select-item:hover) {
  background: rgba(59, 130, 246, 0.1) !important;
}

:deep(.ant-select-item-option-selected) {
  background: rgba(59, 130, 246, 0.15) !important;
  color: var(--jd-blue) !important;
}

.auth-button {
  height: 40px !important;
  font-size: 14px !important;
  font-weight: 600 !important;
  letter-spacing: 0.3px !important;
}

.error-alert {
  margin-bottom: 16px !important;
}

:deep(.ant-alert) {
  background: rgba(239, 68, 68, 0.08) !important;
  border: 1px solid rgba(239, 68, 68, 0.2) !important;
  border-radius: 8px !important;
  color: var(--jd-red) !important;
}

:deep(.ant-alert-message) {
  color: var(--jd-red) !important;
}

:deep(.ant-alert-close-icon) {
  color: var(--jd-red) !important;
}

.auth-footer {
  text-align: center;
  border-top: 1px solid var(--jd-border);
  padding-top: 20px;
}

.auth-footer p {
  margin: 0;
  font-size: 13px;
  color: var(--jd-text-muted);
}

.link {
  color: var(--jd-blue);
  text-decoration: none;
  font-weight: 600;
  margin-left: 4px;
  transition: all var(--jd-trans);
}

.link:hover {
  color: #60a5fa;
}
</style>
