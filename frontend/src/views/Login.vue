<template>
  <div class="jd-auth-bg">
    <div class="jd-auth-card">
      <!-- Logo and Title -->
      <div class="auth-header">
        <div class="auth-logo">
          <i class="pi pi-chart-line"></i>
        </div>
        <h1 class="jd-gradient-text auth-title">CloudAI Trading</h1>
        <p class="auth-subtitle">Sign in to your account</p>
      </div>

      <!-- Secure Badge -->
      <div class="secure-badge">
        <i class="pi pi-lock"></i>
        <span>Secure Connection</span>
      </div>

      <!-- Form -->
      <a-form
        :model="form"
        @finish="handleLogin"
        layout="vertical"
        class="auth-form"
      >
        <!-- Email Field -->
        <a-form-item
          label="Email"
          name="email"
          :rules="[{ required: true, message: 'Please enter your email' }]"
        >
          <a-input
            v-model:value="form.email"
            placeholder="your@email.com"
            type="email"
            class="jd-input"
          />
        </a-form-item>

        <!-- Password Field -->
        <a-form-item
          label="Password"
          name="password"
          :rules="[{ required: true, message: 'Please enter your password' }]"
        >
          <a-input-password
            v-model:value="form.password"
            placeholder="Enter your password"
            class="jd-input"
          />
        </a-form-item>

        <!-- Error Alert -->
        <a-alert
          v-if="error"
          :message="error"
          type="error"
          show-icon
          closable
          @close="error = ''"
          class="error-alert"
        />

        <!-- Sign In Button -->
        <a-form-item>
          <a-button
            type="primary"
            html-type="submit"
            block
            :loading="loading"
            class="jd-btn jd-btn-primary jd-btn-lg auth-button"
          >
            Sign In
          </a-button>
        </a-form-item>
      </a-form>

      <!-- Register Link -->
      <div class="auth-footer">
        <p>Don't have an account?
          <router-link to="/register" class="link">Sign Up</router-link>
        </p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const form = reactive({
  email: 'jiacong9@gmail.com',
  password: 'Abc1234#'
})

const loading = ref(false)
const error = ref('')

const handleLogin = async () => {
  loading.value = true
  try {
    await authStore.login(form.email, form.password)
    router.push('/')
  } catch (err) {
    error.value = err.response?.data?.detail || '登录失败，请检查邮箱和密码'
  } finally {
    loading.value = false
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

:deep(.ant-form-item) {
  margin-bottom: 16px;
}

:deep(.ant-form-item-label > label) {
  color: var(--jd-text-muted) !important;
  font-size: 12px !important;
  font-weight: 600 !important;
  text-transform: uppercase !important;
  letter-spacing: 0.05em !important;
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
