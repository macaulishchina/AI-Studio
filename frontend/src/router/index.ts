import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory('/studio/'),
  routes: [
    {
      path: '/login',
      name: 'Login',
      component: () => import('@/views/Login.vue'),
      meta: { public: true },
    },
    {
      path: '/',
      component: () => import('@/views/StudioLayout.vue'),
      children: [
        {
          path: '',
          name: 'ChatHome',
          component: () => import('@/views/ChatHome.vue'),
        },
        {
          path: 'c/:id',
          name: 'ChatView',
          component: () => import('@/views/ChatHome.vue'),
          props: true,
        },
        {
          path: 'projects',
          name: 'ProjectList',
          component: () => import('@/views/Dashboard.vue'),
        },
        {
          path: 'projects/:id',
          name: 'ProjectDetail',
          component: () => import('@/views/ProjectDetail.vue'),
          props: true,
        },
        {
          path: 'snapshots',
          name: 'Snapshots',
          component: () => import('@/views/SnapshotList.vue'),
        },
        {
          path: 'settings/:tab?',
          name: 'Settings',
          component: () => import('@/views/Settings.vue'),
          props: true,
        },
        {
          path: 'device-debug',
          name: 'DeviceDebug',
          component: () => import('@/views/VoiceTest.vue'),
        },
      ],
    },
  ],
})

// 路由守卫: 自动认证 + 重定向
router.beforeEach(async (to, _from, next) => {
  if (to.meta.public) return next()

  const { useAuthStore } = await import('@/stores/auth')
  const authStore = useAuthStore()

  if (authStore.isLoggedIn && authStore.user) return next()

  // 有 token 但用户信息缺失/陈旧时，补一次自动认证
  if (authStore.isLoggedIn && !authStore.user) {
    const ok = await authStore.autoAuth()
    if (ok) return next()
  }

  // 尝试自动认证 (studio token 或主项目 session)
  const ok = await authStore.autoAuth()
  if (ok) return next()

  // 需要登录
  next({ name: 'Login', query: { redirect: to.fullPath } })
})

export default router
