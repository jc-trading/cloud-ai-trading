<template>
  <div class="jd-auth-bg">
    <div class="jd-auth-card">
      <!-- Logo and Title -->
      <div class="auth-header">
        <div class="auth-logo"><i class="pi pi-user-plus"></i></div>
        <h1 class="jd-gradient-text auth-title">CAT Scope</h1>
        <p class="auth-subtitle">Create your account</p>
      </div>

      <!-- Secure Badge -->
      <div class="secure-badge">
        <i class="pi pi-lock"></i><span>Secure Connection</span>
      </div>

      <!-- Form -->
      <form @submit.prevent="handleRegister" class="auth-form" novalidate>
        <div class="jd-form-group">
          <label class="jd-label" for="reg-name">Full Name</label>
          <input id="reg-name" v-model.trim="form.name" type="text" autocomplete="name"
                 placeholder="Your full name" class="jd-input" required />
        </div>

        <div class="jd-form-group">
          <label class="jd-label" for="reg-email">Email</label>
          <input id="reg-email" v-model.trim="form.email" type="email" autocomplete="email"
                 placeholder="your@email.com" class="jd-input" required />
        </div>

        <div class="jd-form-group">
          <label class="jd-label" for="reg-password">Password</label>
          <div class="pw-wrap">
            <input id="reg-password" v-model="form.password" :type="showPw ? 'text' : 'password'"
                   autocomplete="new-password" placeholder="Min 8 characters" class="jd-input" required />
            <button type="button" class="pw-toggle" @click="showPw = !showPw" :aria-label="showPw ? 'Hide password' : 'Show password'">
              <i :class="showPw ? 'pi pi-eye-slash' : 'pi pi-eye'"></i>
            </button>
          </div>
        </div>

        <div class="jd-form-group">
          <label class="jd-label" for="reg-tz">Timezone</label>
          <select id="reg-tz" v-model="form.timezone" class="jd-input jd-select">
            <option v-for="tz in timezones" :key="tz.value" :value="tz.value">{{ tz.label }}</option>
          </select>
        </div>

        <div v-if="errorMsg" class="jd-alert error" style="margin-bottom:16px">
          <i class="pi pi-exclamation-circle"></i><span>{{ errorMsg }}</span>
        </div>

        <button type="submit" class="jd-btn jd-btn-primary jd-btn-lg" style="width:100%" :disabled="loading">
          <i v-if="loading" class="pi pi-spinner animate-spin"></i>
          {{ loading ? 'Creating…' : 'Create Account' }}
        </button>
      </form>

      <div class="auth-footer">
        <p>Already have an account? <router-link to="/login" class="link">Sign In</router-link></p>
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
const loading = ref(false)
const showPw = ref(false)

const form = reactive({
  name: '', email: '', password: '',
  timezone: 'Asia/Kuala_Lumpur', country: 'Malaysia', language: 'en', currency: 'USD',
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
    errorMsg.value = 'All fields are required.'
    return
  }
  if (form.password.length < 8) {
    errorMsg.value = 'Password must be at least 8 characters.'
    return
  }
  loading.value = true
  try {
    await auth.register(form)
    router.push('/')
  } catch (err) {
    errorMsg.value = err.response?.data?.detail || 'Registration failed.'
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
