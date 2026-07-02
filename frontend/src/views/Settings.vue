<template>
  <div class="jd-page">
    <!-- Header -->
    <div>
      <h1 class="jd-section-title" style="margin-bottom: 8px;">Settings</h1>
      <p style="color: var(--jd-text-muted); font-size: 14px;">Manage your account preferences and settings</p>
    </div>

    <!-- Profile Card -->
    <div class="jd-card">
      <div class="jd-card-header">
        <h2 class="jd-card-title">Profile Information</h2>
      </div>
      <div class="jd-card-body">
        <div class="form-grid">
          <!-- Name -->
          <div class="jd-form-group">
            <label class="jd-label">Full Name</label>
            <input class="jd-input" v-model="settings.name" placeholder="Enter your name" />
          </div>

          <!-- Email -->
          <div class="jd-form-group">
            <label class="jd-label">Email Address</label>
            <input class="jd-input" v-model="settings.email" placeholder="your@email.com" disabled />
            <p style="color: var(--jd-text-muted); font-size: 12px; margin-top: 4px;">Email cannot be changed</p>
          </div>

          <!-- Phone -->
          <div class="jd-form-group">
            <label class="jd-label">Phone Number</label>
            <input class="jd-input" v-model="settings.phone" placeholder="+1 (555) 000-0000" />
          </div>

          <!-- Timezone -->
          <div class="jd-form-group">
            <label class="jd-label">Timezone</label>
            <select class="jd-input jd-select" v-model="settings.timezone">
              <option v-for="o in timezones" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
          </div>

          <!-- Country -->
          <div class="jd-form-group">
            <label class="jd-label">Country</label>
            <input class="jd-input" v-model="settings.country" placeholder="United States" />
          </div>

          <!-- Language -->
          <div class="jd-form-group">
            <label class="jd-label">Language</label>
            <select class="jd-input jd-select" v-model="settings.language">
              <option v-for="o in languages" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
          </div>

          <!-- Currency -->
          <div class="jd-form-group">
            <label class="jd-label">Default Currency</label>
            <select class="jd-input jd-select" v-model="settings.currency">
              <option v-for="o in currencies" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
          </div>
        </div>

        <!-- Actions -->
        <div style="display: flex; gap: 8px; margin-top: 24px; border-top: 1px solid var(--jd-border); padding-top: 16px;">
          <button class="jd-btn jd-btn-primary">Save Changes</button>
          <button class="jd-btn jd-btn-ghost">Cancel</button>
        </div>
      </div>
    </div>

    <!-- Preferences Card -->
    <div class="jd-card">
      <div class="jd-card-header">
        <h2 class="jd-card-title">Preferences</h2>
      </div>
      <div class="jd-card-body">
        <!-- Theme Section -->
        <div style="margin-bottom: 24px;">
          <h3 style="font-weight: 600; margin-bottom: 12px; color: var(--jd-text);">Theme</h3>
          <div style="display: flex; flex-direction: column; gap: 8px;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <input type="radio" v-model="preferences.theme" name="theme" value="dark" id="dark" />
              <label for="dark" style="cursor: pointer; color: var(--jd-text);">Dark (Current)</label>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
              <input type="radio" v-model="preferences.theme" name="theme" value="light" id="light" />
              <label for="light" style="cursor: pointer; color: var(--jd-text);">Light</label>
            </div>
          </div>
        </div>

        <!-- Notifications Section -->
        <div style="margin-bottom: 24px; border-bottom: 1px solid var(--jd-border); padding-bottom: 16px;">
          <h3 style="font-weight: 600; margin-bottom: 12px; color: var(--jd-text);">Notifications</h3>
          <div style="display: flex; flex-direction: column; gap: 8px;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <input type="checkbox" v-model="preferences.emailNotifications" id="email-notif" />
              <label for="email-notif" style="cursor: pointer; color: var(--jd-text);">Email Notifications</label>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
              <input type="checkbox" v-model="preferences.pushNotifications" id="push-notif" />
              <label for="push-notif" style="cursor: pointer; color: var(--jd-text);">Push Notifications</label>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
              <input type="checkbox" v-model="preferences.tradeAlerts" id="trade-alerts" />
              <label for="trade-alerts" style="cursor: pointer; color: var(--jd-text);">Trade Alerts</label>
            </div>
          </div>
        </div>

        <!-- Display Section -->
        <div style="margin-bottom: 24px;">
          <h3 style="font-weight: 600; margin-bottom: 12px; color: var(--jd-text);">Display Options</h3>
          <div style="display: flex; flex-direction: column; gap: 12px;">
            <div style="display: flex; align-items: center; justify-content: space-between;">
              <label style="color: var(--jd-text);">Compact View</label>
              <label class="jd-toggle">
                <input type="checkbox" v-model="preferences.compactView" />
                <span class="jd-toggle-slider"></span>
              </label>
            </div>
            <div style="display: flex; align-items: center; justify-content: space-between;">
              <label style="color: var(--jd-text);">Show Charts by Default</label>
              <label class="jd-toggle">
                <input type="checkbox" v-model="preferences.showCharts" />
                <span class="jd-toggle-slider"></span>
              </label>
            </div>
          </div>
        </div>

        <!-- Actions -->
        <div style="display: flex; gap: 8px; margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--jd-border);">
          <button class="jd-btn jd-btn-primary">Save Preferences</button>
          <button class="jd-btn jd-btn-ghost">Reset to Default</button>
        </div>
      </div>
    </div>

    <!-- Security Card -->
    <div class="jd-card">
      <div class="jd-card-header">
        <h2 class="jd-card-title">Security</h2>
      </div>
      <div class="jd-card-body">
        <!-- Two-Factor Authentication -->
        <div style="margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--jd-border);">
          <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px;">
            <div>
              <h3 style="font-weight: 600; color: var(--jd-text); margin-bottom: 4px;">Two-Factor Authentication</h3>
              <p style="font-size: 12px; color: var(--jd-text-muted);">Add an extra layer of security</p>
            </div>
            <span class="jd-badge yellow">Disabled</span>
          </div>
          <button class="jd-btn jd-btn-primary jd-btn-sm">Enable 2FA</button>
        </div>

        <!-- API Keys -->
        <div style="margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--jd-border);">
          <h3 style="font-weight: 600; margin-bottom: 12px; color: var(--jd-text);">API Keys</h3>
          <button class="jd-btn jd-btn-primary jd-btn-sm">Manage API Keys</button>
        </div>

        <!-- Active Sessions -->
        <div style="margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--jd-border);">
          <h3 style="font-weight: 600; margin-bottom: 12px; color: var(--jd-text);">Active Sessions</h3>
          <p style="font-size: 12px; color: var(--jd-text-muted); margin-bottom: 12px;">You are currently signed in on 1 device</p>
          <button class="jd-btn jd-btn-primary jd-btn-sm">View All Sessions</button>
        </div>

        <!-- Change Password -->
        <div>
          <h3 style="font-weight: 600; margin-bottom: 12px; color: var(--jd-text);">Change Password</h3>
          <div style="display: flex; flex-direction: column; gap: 12px;">
            <div class="jd-form-group">
              <label class="jd-label">Current Password</label>
              <input type="password" class="jd-input" placeholder="••••••••" />
            </div>
            <div class="jd-form-group">
              <label class="jd-label">New Password</label>
              <input type="password" class="jd-input" placeholder="••••••••" />
            </div>
            <div class="jd-form-group">
              <label class="jd-label">Confirm Password</label>
              <input type="password" class="jd-input" placeholder="••••••••" />
            </div>
            <div style="display: flex; gap: 8px; margin-top: 12px; padding-top: 12px;">
              <button class="jd-btn jd-btn-primary">Update Password</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const settings = ref({
  name: '',
  email: 'user@example.com',
  phone: '',
  timezone: 'America/New_York',
  country: '',
  language: 'en',
  currency: 'USD'
})

const preferences = ref({
  theme: 'dark',
  emailNotifications: true,
  pushNotifications: false,
  tradeAlerts: true,
  compactView: false,
  showCharts: true
})

const timezones = ref([
  { label: 'Eastern Time (ET)', value: 'America/New_York' },
  { label: 'Central Time (CT)', value: 'America/Chicago' },
  { label: 'Mountain Time (MT)', value: 'America/Denver' },
  { label: 'Pacific Time (PT)', value: 'America/Los_Angeles' },
  { label: 'UTC', value: 'UTC' },
  { label: 'London (GMT)', value: 'Europe/London' },
  { label: 'Singapore (SGT)', value: 'Asia/Singapore' },
  { label: 'Tokyo (JST)', value: 'Asia/Tokyo' },
  { label: 'Hong Kong (HKT)', value: 'Asia/Hong_Kong' }
])

const languages = ref([
  { label: 'English', value: 'en' },
  { label: 'Spanish', value: 'es' },
  { label: 'French', value: 'fr' },
  { label: 'German', value: 'de' },
  { label: 'Chinese', value: 'zh' },
  { label: 'Japanese', value: 'ja' }
])

const currencies = ref([
  { label: 'USD - US Dollar', value: 'USD' },
  { label: 'EUR - Euro', value: 'EUR' },
  { label: 'GBP - British Pound', value: 'GBP' },
  { label: 'JPY - Japanese Yen', value: 'JPY' },
  { label: 'CNY - Chinese Yuan', value: 'CNY' },
  { label: 'SGD - Singapore Dollar', value: 'SGD' }
])
</script>

<style scoped>
.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
}

.jd-section-title {
  font-size: 28px;
  font-weight: 700;
  color: var(--jd-text);
}

.jd-label {
  display: block;
  font-size: 13px;
  font-weight: 500;
  color: var(--jd-text-muted);
  margin-bottom: 6px;
}

.jd-form-group {
  display: flex;
  flex-direction: column;
}

.jd-toggle {
  position: relative;
  display: inline-flex;
  width: 44px;
  height: 24px;
  cursor: pointer;
}

.jd-toggle input {
  display: none;
}

.jd-toggle-slider {
  position: absolute;
  cursor: pointer;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: #4b5563;
  transition: 0.3s;
  border-radius: 24px;
  border: 1px solid var(--jd-border);
}

.jd-toggle-slider:before {
  position: absolute;
  content: "";
  height: 18px;
  width: 18px;
  left: 3px;
  bottom: 2px;
  background-color: white;
  transition: 0.3s;
  border-radius: 50%;
}

.jd-toggle input:checked + .jd-toggle-slider {
  background-color: var(--jd-blue);
  border-color: var(--jd-blue);
}

.jd-toggle input:checked + .jd-toggle-slider:before {
  transform: translateX(20px);
}

.jd-input {
  background-color: var(--jd-card);
  border: 1px solid var(--jd-border);
  color: var(--jd-text);
  width: 100%;
  padding: 8px 12px;
  border-radius: 4px;
  font-size: 13px;
}

.jd-input:focus {
  outline: none;
  border-color: var(--jd-blue);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
}

.jd-input:disabled {
  background-color: rgba(75, 85, 99, 0.3);
  color: var(--jd-text-muted);
  cursor: not-allowed;
}

.jd-select {
  appearance: none;
  cursor: pointer;
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

.jd-btn-ghost {
  background-color: transparent;
  color: var(--jd-text);
  border: 1px solid var(--jd-border);
}

.jd-btn-ghost:hover {
  background-color: var(--jd-card);
  border-color: var(--jd-text-muted);
}

.jd-btn-sm {
  padding: 6px 12px;
  font-size: 12px;
}

.jd-badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
}

.jd-badge.yellow {
  background-color: #fbbf24;
  color: #000;
}

.jd-badge.green {
  background-color: #10b981;
  color: white;
}

.jd-badge.red {
  background-color: #ef4444;
  color: white;
}

.jd-badge.blue {
  background-color: var(--jd-blue);
  color: white;
}
</style>
