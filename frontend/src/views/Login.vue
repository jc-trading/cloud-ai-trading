<template>
  <div class="jd-auth-bg">
    <div class="jd-auth-card">
      <!-- Logo and Title -->
      <div class="auth-header">
        <div class="auth-logo"><i class="pi pi-chart-line"></i></div>
        <h1 class="jd-gradient-text auth-title">CAT Scope</h1>
        <p class="auth-subtitle">Sign in to your account</p>
      </div>

      <!-- Secure Badge -->
      <div class="secure-badge">
        <i class="pi pi-lock"></i><span>Secure Connection</span>
      </div>

      <!-- Form -->
      <form @submit.prevent="handleLogin" class="auth-form" novalidate>
        <div class="jd-form-group">
          <label class="jd-label" for="login-email">Email</label>
          <input id="login-email" v-model.trim="form.email" type="email" autocomplete="email"
                 placeholder="your@email.com" class="jd-input" required />
        </div>

        <div class="jd-form-group">
          <label class="jd-label" for="login-password">Password</label>
          <div class="pw-wrap">
            <input id="login-password" v-model="form.password" :type="showPw ? 'text' : 'password'"
                   autocomplete="current-password" placeholder="Enter your password" class="jd-input" required />
            <button type="button" class="pw-toggle" @click="showPw = !showPw" :aria-label="showPw ? 'Hide password' : 'Show password'">
              <i :class="showPw ? 'pi pi-eye-slash' : 'pi pi-eye'"></i>
            </button>
          </div>
        </div>

        <div v-if="error" class="jd-alert error" style="margin-bottom:16px">
          <i class="pi pi-exclamation-circle"></i><span>{{ error }}</span>
        </div>

        <button type="submit" class="jd-btn jd-btn-primary jd-btn-lg" style="width:100%" :disabled="loading">
          <i v-if="loading" class="pi pi-spinner animate-spin"></i>
          {{ loading ? 'Signing in…' : 'Sign In' }}
        </button>
      </form>

      <div class="auth-footer">
        <p>Don't have an account? <router-link to="/register" class="link">Sign Up</router-link></p>
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

const form = reactive({ email: '', password: '' })
const loading = ref(false)
const error = ref('')
const showPw = ref(false)

const handleLogin = async () => {
  error.value = ''
  if (!form.email || !form.password) {
    error.value = 'Please enter your email and password.'
    return
  }
  loading.value = true
  try {
    await authStore.login(form.email, form.password)
    router.push('/')
  } catch (err) {
    error.value = err.response?.data?.detail || 'Login failed — check your email and password.'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-header { text-align: center; margin-bottom: 32px; }
.auth-logo { display: flex; justify-content: center; margin-bottom: 16px; }
.auth-logo i {
  font-size: 40px;
  background: linear-gradient(135deg, var(--jd-cyan), var(--jd-purple));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
}
.auth-title { font-size: 26px; font-weight: 700; margin: 0 0 8px 0; letter-spacing: -0.02em; }
.auth-subtitle { font-size: 13px; color: var(--jd-text-muted); margin: 0; }
.secure-badge {
  display: flex; align-items: center; justify-content: center; gap: 6px;
  padding: 8px 16px; background: var(--jd-green-glow);
  border: 1px solid rgba(46,224,138,0.22); border-radius: 8px;
  margin-bottom: 24px; font-size: 12px; color: var(--jd-green); font-weight: 500;
}
.secure-badge i { font-size: 14px; }
.auth-form { margin-bottom: 20px; }
.pw-wrap { position: relative; }
.pw-toggle {
  position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
  background: none; border: none; color: var(--jd-text-muted); cursor: pointer;
  padding: 4px 8px; font-size: 14px;
}
.pw-toggle:hover { color: var(--jd-cyan); }
.auth-footer { text-align: center; border-top: 1px solid var(--jd-border); padding-top: 20px; }
.auth-footer p { margin: 0; font-size: 13px; color: var(--jd-text-muted); }
.link { color: var(--jd-cyan); text-decoration: none; font-weight: 600; margin-left: 4px; }
.link:hover { text-decoration: underline; }
</style>
