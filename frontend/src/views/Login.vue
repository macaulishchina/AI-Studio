<template>
  <div class="login-container">
    <div class="login-card-wrapper">
      <n-card class="login-card" :bordered="false">
        <template #header>
          <div style="text-align: center">
            <div style="font-size: 48px; margin-bottom: 8px">🤖</div>
            <n-text style="font-size: 22px; font-weight: 700; color: #e94560">
              设计院 Studio
            </n-text>
            <div style="margin-top: 4px">
              <n-text depth="3" style="font-size: 13px">设计院 · AI 驱动迭代开发</n-text>
            </div>
          </div>
        </template>

        <!-- 自动检测主项目 session -->
        <div v-if="autoDetecting" style="text-align: center; padding: 24px 0">
          <n-spin size="medium" />
          <div style="margin-top: 12px">
            <n-text depth="3">正在检测登录状态...</n-text>
          </div>
        </div>

        <template v-else>
          <!-- 主项目快捷登录 -->
          <div v-if="mainProjectAvailable" style="margin-bottom: 20px">
            <n-button
              type="info"
              block
              size="large"
              :loading="mainProjectLoading"
              @click="loginViaMainProject"
            >
              🔗 使用主项目账户登录
            </n-button>
            <div style="text-align: center; margin-top: 6px">
              <n-text depth="3" style="font-size: 12px">
                检测到主项目登录态，点击一键进入
              </n-text>
            </div>
          </div>

          <n-divider v-if="mainProjectAvailable" title-placement="center">
            <n-text depth="3" style="font-size: 12px">或</n-text>
          </n-divider>

          <!-- 登录 / 注册 切换 -->
          <n-tabs v-model:value="authMode" type="segment" animated size="small" style="margin-bottom: 16px">
            <n-tab-pane name="login" tab="🔑 登录" />
            <n-tab-pane name="register" tab="📝 注册" />
          </n-tabs>

          <!-- 登录表单 -->
          <n-form v-if="authMode === 'login'" ref="formRef" :model="formData" :rules="rules" @submit.prevent="handleLogin">
            <n-form-item label="用户名" path="username">
              <n-input v-model:value="formData.username" placeholder="用户名" size="large" />
            </n-form-item>
            <n-form-item label="密码" path="password">
              <n-input
                v-model:value="formData.password"
                type="password"
                placeholder="密码"
                size="large"
                show-password-on="click"
                @keyup.enter="handleLogin"
              />
            </n-form-item>
            <n-button
              type="primary"
              block
              size="large"
              :loading="loginLoading"
              :disabled="!formData.username || !formData.password"
              @click="handleLogin"
            >
              🔑 登录
            </n-button>
          </n-form>

          <!-- 注册表单 -->
          <n-form v-else ref="regFormRef" :model="regData" :rules="regRules" @submit.prevent="handleRegister">
            <n-form-item label="用户名" path="username">
              <n-input v-model:value="regData.username" placeholder="设置用户名 (2~100字符)" size="large" />
            </n-form-item>
            <n-form-item label="昵称" path="nickname">
              <n-input v-model:value="regData.nickname" placeholder="显示昵称 (可选)" size="large" />
            </n-form-item>
            <n-form-item label="密码" path="password">
              <n-input
                v-model:value="regData.password"
                type="password"
                placeholder="设置密码 (≥4位)"
                size="large"
                show-password-on="click"
              />
            </n-form-item>
            <n-form-item label="确认密码" path="confirmPassword">
              <n-input
                v-model:value="regData.confirmPassword"
                type="password"
                placeholder="再次输入密码"
                size="large"
                show-password-on="click"
                @keyup.enter="handleRegister"
              />
            </n-form-item>
            <n-button
              type="success"
              block
              size="large"
              :loading="registerLoading"
              :disabled="!regData.username || !regData.password || !regData.confirmPassword"
              @click="handleRegister"
            >
              📝 提交注册
            </n-button>
            <n-text depth="3" style="font-size: 11px; display: block; text-align: center; margin-top: 8px">
              注册后需管理员审批激活，审批通过后方可登录
            </n-text>
          </n-form>

          <!-- 成功/错误消息 -->
          <n-alert v-if="successMsg" type="success" :title="successMsg" style="margin-top: 16px" closable @close="successMsg = ''" />
          <n-alert v-if="errorMsg" type="error" :title="errorMsg" style="margin-top: 16px" closable @close="errorMsg = ''" />
        </template>

        <template #footer>
          <div style="text-align: center">
            <n-text depth="3" style="font-size: 11px">
              管理员可通过环境变量 STUDIO_ADMIN_PASS 配置初始密码
            </n-text>
          </div>
        </template>
      </n-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { userApi } from '@/api'
import type { FormInst, FormRules } from 'naive-ui'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const formRef = ref<FormInst | null>(null)
const regFormRef = ref<FormInst | null>(null)
const autoDetecting = ref(true)
const mainProjectAvailable = ref(false)
const mainProjectLoading = ref(false)
const loginLoading = ref(false)
const registerLoading = ref(false)
const errorMsg = ref('')
const successMsg = ref('')
const authMode = ref<'login' | 'register'>('login')

const formData = ref({
  username: '',
  password: '',
})

const regData = ref({
  username: '',
  nickname: '',
  password: '',
  confirmPassword: '',
})

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

const regRules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 2, max: 100, message: '用户名 2~100 字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 4, message: '密码至少 4 位', trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (_rule: any, value: string) => {
        if (value !== regData.value.password) return new Error('两次密码不一致')
        return true
      },
      trigger: 'blur',
    },
  ],
}

// 目标跳转路径
const redirectTo = () => {
  const target = route.query.redirect as string
  return target || '/'
}

onMounted(async () => {
  // 如果已登录, 直接跳转
  if (authStore.isLoggedIn) {
    router.replace(redirectTo())
    return
  }

  // 自动检测
  try {
    const ok = await authStore.autoAuth()
    if (ok) {
      router.replace(redirectTo())
      return
    }
  } catch {}

  // 检测主项目 session 是否可用 (使用动态 SSO token key)
  const mainToken = localStorage.getItem(authStore.ssoTokenKey)
  mainProjectAvailable.value = !!mainToken

  autoDetecting.value = false
})

async function loginViaMainProject() {
  mainProjectLoading.value = true
  errorMsg.value = ''
  try {
    const mainToken = localStorage.getItem(authStore.ssoTokenKey)
    if (!mainToken) {
      errorMsg.value = '未检测到主项目登录信息，请先在主项目中登录'
      return
    }
    const ok = await authStore.autoAuth()
    if (ok) {
      router.replace(redirectTo())
    } else {
      errorMsg.value = '主项目 token 已失效，请重新登录主项目'
      mainProjectAvailable.value = false
    }
  } catch (e: any) {
    errorMsg.value = e.response?.data?.detail || e.message || '登录失败'
  } finally {
    mainProjectLoading.value = false
  }
}

async function handleLogin() {
  loginLoading.value = true
  errorMsg.value = ''
  try {
    // 先尝试 DB 用户登录, 失败再尝试管理员登录
    try {
      await authStore.dbUserLogin(formData.value.username, formData.value.password)
      router.replace(redirectTo())
      return
    } catch (e: any) {
      // 如果是 403 (待审批/已禁用), 直接显示
      if (e.response?.status === 403) {
        errorMsg.value = e.response.data.detail
        return
      }
      // 401 = 用户名密码错误, 回退到管理员登录
    }
    // 尝试管理员登录
    await authStore.adminLogin(formData.value.username, formData.value.password)
    router.replace(redirectTo())
  } catch (e: any) {
    errorMsg.value = e.response?.data?.detail || '用户名或密码错误'
  } finally {
    loginLoading.value = false
  }
}

async function handleRegister() {
  errorMsg.value = ''
  successMsg.value = ''
  if (regData.value.password !== regData.value.confirmPassword) {
    errorMsg.value = '两次输入的密码不一致'
    return
  }
  registerLoading.value = true
  try {
    await userApi.register({
      username: regData.value.username,
      password: regData.value.password,
      nickname: regData.value.nickname || undefined,
    })
    successMsg.value = '注册成功！请等待管理员审批后即可登录。'
    // 切换回登录 tab, 填入用户名
    authMode.value = 'login'
    formData.value.username = regData.value.username
    regData.value = { username: '', nickname: '', password: '', confirmPassword: '' }
  } catch (e: any) {
    errorMsg.value = e.response?.data?.detail || '注册失败'
  } finally {
    registerLoading.value = false
  }
}
</script>

<style scoped>
.login-container {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #0a1628 0%, #16213e 50%, #1a1a2e 100%);
}

.login-card-wrapper {
  width: 100%;
  max-width: 420px;
  padding: 16px;
}

.login-card {
  background: rgba(22, 33, 62, 0.95);
  border-radius: 16px;
  backdrop-filter: blur(10px);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
}
</style>
