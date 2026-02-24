import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { studioAuthApi, userApi } from '@/api'

interface StudioUser {
  username: string
  nickname: string
  source: 'admin' | 'main_project' | 'db_user'
  main_user_id?: number | null
  db_user_id?: number | null
  role?: string | null
  permissions?: string[]
}

const USER_STORAGE_KEY = 'studio_user'

function loadStoredUser(): StudioUser | null {
  try {
    const raw = localStorage.getItem(USER_STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export const useAuthStore = defineStore('studioAuth', () => {
  const token = ref<string | null>(localStorage.getItem('studio_token'))
  const user = ref<StudioUser | null>(loadStoredUser())
  const loading = ref(false)
  // SSO token 在 localStorage 中的 key (从后端 /auth/check 动态获取，默认 'token')
  const ssoTokenKey = ref<string>('token')

  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() =>
    user.value?.source === 'admin' || user.value?.role === 'admin'
  )
  const userRole = computed(() => {
    if (user.value?.source === 'admin') return 'admin'
    return user.value?.role || 'viewer'
  })
  const userPermissions = computed(() => {
    if (user.value?.source === 'admin') return ['*']  // admin has all
    return user.value?.permissions || []
  })

  /** 检查当前用户是否拥有某个权限 */
  function hasPermission(perm: string): boolean {
    if (isAdmin.value) return true
    return userPermissions.value.includes(perm)
  }

  function setToken(newToken: string, userInfo: StudioUser) {
    token.value = newToken
    user.value = userInfo
    localStorage.setItem('studio_token', newToken)
    localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(userInfo))
  }

  function logout() {
    token.value = null
    user.value = null
    localStorage.removeItem('studio_token')
    localStorage.removeItem(USER_STORAGE_KEY)
  }

  /**
   * 自动认证流程:
   * 1. 检查已有 studio token
   * 2. 尝试复用主项目 session (localStorage 动态 key)
   * 3. 都没有 → 需要登录
   */
  async function autoAuth(): Promise<boolean> {
    // 1. 已有 studio token → 验证
    if (token.value) {
      try {
        const { data } = await studioAuthApi.me()
        user.value = data
        localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(data))
        return true
      } catch {
        // token 无效, 清除
        logout()
      }
    }

    // 2. 从后端获取 SSO 配置 (动态 token key)
    try {
      const { data: checkData } = await studioAuthApi.check()
      if (checkData.sso_token_key) {
        ssoTokenKey.value = checkData.sso_token_key
      }
    } catch {
      // 获取配置失败，使用默认值
    }

    // 3. 尝试主项目 session
    const mainToken = localStorage.getItem(ssoTokenKey.value)
    if (mainToken) {
      try {
        const { data } = await studioAuthApi.verifyMainToken(mainToken)
        setToken(data.access_token, {
          username: data.username,
          nickname: data.nickname,
          source: data.source as StudioUser['source'],
        })
        return true
      } catch {
        // 主项目 token 也无效
      }
    }

    // 4. 需要登录
    return false
  }

  /**
   * 管理员登录 (env 账户)
   */
  async function adminLogin(username: string, password: string) {
    loading.value = true
    try {
      const { data } = await studioAuthApi.login(username, password)
      setToken(data.access_token, {
        username: data.username,
        nickname: data.nickname,
        source: data.source as StudioUser['source'],
        role: data.role,
        permissions: data.permissions,
      })
      return true
    } finally {
      loading.value = false
    }
  }

  /**
   * DB 注册用户登录
   */
  async function dbUserLogin(username: string, password: string) {
    loading.value = true
    try {
      const { data } = await userApi.login(username, password)
      setToken(data.access_token, {
        username: data.username,
        nickname: data.nickname,
        source: 'db_user',
        db_user_id: data.db_user_id,
        role: data.role,
        permissions: data.permissions,
      })
      return true
    } finally {
      loading.value = false
    }
  }

  return {
    token, user, loading, ssoTokenKey,
    isLoggedIn, isAdmin, userRole, userPermissions,
    hasPermission,
    setToken, logout, autoAuth, adminLogin, dbUserLogin,
  }
})
