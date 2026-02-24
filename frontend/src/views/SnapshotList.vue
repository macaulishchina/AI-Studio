<template>
  <div>
    <n-space justify="space-between" align="center" style="margin-bottom: 16px" :wrap="true">
      <n-h3 style="margin: 0">📸 快照管理</n-h3>
      <n-button type="primary" @click="handleCreateSnapshot" :loading="creating" size="small">
        手动创建快照
      </n-button>
    </n-space>

    <n-spin :show="loading">
      <n-timeline v-if="snapshots.length">
        <n-timeline-item
          v-for="s in snapshots"
          :key="s.id"
          :type="s.is_healthy ? 'success' : 'error'"
          :title="`${s.description || s.git_tag}`"
          :content="`Commit: ${s.git_commit.slice(0, 8)} · Tag: ${s.git_tag}`"
          :time="formatDate(s.created_at)"
        >
          <template #footer>
            <n-space>
              <n-button size="tiny" @click="handleRollback(s)" :loading="rollingBack === s.id">
                🔄 回滚到此
              </n-button>
              <n-tag v-if="s.db_backup_path" size="small" :bordered="false" type="info">有数据库备份</n-tag>
            </n-space>
          </template>
        </n-timeline-item>
      </n-timeline>
      <n-empty v-else description="暂无快照" />
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useMessage, useDialog } from 'naive-ui'
import { snapshotApi } from '@/api'

const message = useMessage()
const dialog = useDialog()
const snapshots = ref<any[]>([])
const loading = ref(false)
const creating = ref(false)
const rollingBack = ref<number | null>(null)

async function fetchSnapshots() {
  loading.value = true
  try {
    const { data } = await snapshotApi.list()
    snapshots.value = data
  } finally {
    loading.value = false
  }
}

async function handleCreateSnapshot() {
  creating.value = true
  try {
    await snapshotApi.create({ description: `手动快照 ${new Date().toLocaleString('zh-CN')}` })
    message.success('快照已创建')
    fetchSnapshots()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '创建失败')
  } finally {
    creating.value = false
  }
}

function handleRollback(snapshot: any) {
  dialog.warning({
    title: '确认回滚',
    content: `将回滚到快照 "${snapshot.description || snapshot.git_tag}"，这将重新构建并部署主项目。确定继续？`,
    positiveText: '确定回滚',
    negativeText: '取消',
    onPositiveClick: async () => {
      rollingBack.value = snapshot.id
      try {
        const { data } = await snapshotApi.rollback(snapshot.id, { restore_db: false })
        if (data.success) {
          message.success('回滚成功')
        } else {
          message.error(data.error || '回滚失败')
        }
      } catch (e: any) {
        message.error(e.response?.data?.detail || '回滚失败')
      } finally {
        rollingBack.value = null
      }
    },
  })
}

function formatDate(d: string) {
  return new Date(d).toLocaleString('zh-CN')
}

onMounted(() => fetchSnapshots())
</script>
