<template>
  <n-space vertical :size="16">
    <!-- 工作目录管理 -->
    <n-card title="📁 工作目录管理" size="small" style="background: #16213e">
      <template #header-extra>
        <n-space :size="8">
          <n-button size="small" type="primary" @click="showAddDir = true">
            ＋ 添加目录
          </n-button>
          <n-button size="small" @click="fetchWorkspaceDirs" :loading="loadingDirs">
            🔄
          </n-button>
        </n-space>
      </template>

      <!-- 添加目录弹窗 -->
      <n-modal v-model:show="showAddDir" preset="dialog" title="添加工作目录" positive-text="添加" negative-text="取消"
        @positive-click="handleAddDir" :loading="addingDir">
        <n-space vertical :size="12">
          <n-input v-model:value="newDirPath" placeholder="输入工作目录绝对路径, 如 D:\projects\myapp" clearable />
          <n-input v-model:value="newDirLabel" placeholder="标签 (可选, 如: UE5引擎)" clearable />
        </n-space>
      </n-modal>

      <!-- 目录列表 -->
      <n-spin :show="loadingDirs">
        <n-space vertical :size="8" v-if="workspaceDirs.length">
          <div
            v-for="dir in workspaceDirs"
            :key="dir.id"
            :style="{
              padding: '10px 14px',
              borderRadius: '6px',
              border: dir.is_active ? '1.5px solid #63e2b7' : '1px solid rgba(255,255,255,0.08)',
              background: dir.is_active ? 'rgba(99,226,183,0.06)' : 'rgba(255,255,255,0.02)',
              transition: 'all 0.2s',
            }"
          >
            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
              <!-- 活跃标记 -->
              <n-tag v-if="dir.is_active" type="success" size="small" :bordered="false" round>当前</n-tag>
              <!-- VCS 类型 -->
              <n-tag :type="dir.vcs_type === 'none' ? 'default' : 'info'" size="small" :bordered="false">
                {{ ({git: 'Git', svn: 'SVN', none: '—'} as Record<string, string>)[dir.vcs_type] || dir.vcs_type }}
              </n-tag>
              <!-- 标签 -->
              <n-text strong style="font-size: 13px">{{ dir.label || dir.path.split(/[\\/]/).pop() }}</n-text>
              <!-- 内置目录标记 -->
              <n-tag v-if="dir.is_builtin" type="info" size="small" :bordered="false">内置（ENV）</n-tag>
              <!-- 目录不存在警告 -->
              <n-tag v-if="!dir.exists" type="error" size="small">目录不存在</n-tag>
              <!-- 配置状态摘要（默认折叠也能看见） -->
              <template v-if="dir.vcs_type === 'git'">
                <n-tag size="small" type="info" :bordered="false">
                  {{ gitProviderLabel(dir.git_provider || 'github') }}
                </n-tag>
                <n-tag
                  size="small"
                  :type="((dir.git_provider || 'github') === 'gitlab' ? dir.gitlab_token_configured : dir.github_token_configured) ? 'success' : 'warning'"
                  :bordered="false"
                >
                  Token{{ ((dir.git_provider || 'github') === 'gitlab' ? dir.gitlab_token_configured : dir.github_token_configured) ? '已配' : '未配' }}
                </n-tag>
                <n-tag
                  size="small"
                  :type="((dir.git_provider || 'github') === 'gitlab' ? dir.gitlab_repo : dir.github_repo) ? 'success' : 'warning'"
                  :bordered="false"
                >
                  仓库{{ ((dir.git_provider || 'github') === 'gitlab' ? dir.gitlab_repo : dir.github_repo) ? '已绑' : '未绑' }}
                </n-tag>
              </template>
              <template v-else-if="dir.vcs_type === 'svn'">
                <n-tag size="small" :type="svnRepoReady(dir) ? 'success' : 'default'" :bordered="false">
                  SVN地址{{ svnRepoReady(dir) ? '已就绪' : '自动探测' }}
                </n-tag>
                <n-tag size="small" :type="dir.svn_username_configured ? 'success' : 'default'" :bordered="false">
                  用户{{ svnUserReady(dir) ? '已识别' : '可选' }}
                </n-tag>
              </template>

              <!-- 操作按钮 (右对齐) -->
              <div style="margin-left: auto; display: flex; gap: 4px; flex-shrink: 0">
                <n-button v-if="!dir.is_active" size="tiny" type="primary" ghost @click="handleActivate(dir)"
                  :loading="dir._switching">
                  切换
                </n-button>
                <n-popconfirm @positive-click="handleRemoveDir(dir)">
                  <template #trigger>
                    <n-button size="tiny" type="error" ghost :disabled="dir.is_builtin">
                      移除
                    </n-button>
                  </template>
                  确定移除工作目录「{{ dir.label || dir.path }}」？<br>（不会删除实际文件）
                </n-popconfirm>
              </div>
            </div>
            <!-- 路径 -->
            <n-text code depth="3" style="font-size: 11px; margin-top: 4px; display: block; word-break: break-all">
              {{ dir.path }}
            </n-text>

            <!-- 子项配置：默认折叠 -->
            <n-collapse
              v-if="dir.vcs_type === 'git' || dir.vcs_type === 'svn'"
              :default-expanded-names="[]"
              style="margin-top: 8px"
            >
              <n-collapse-item :title="dir.vcs_type === 'git' ? 'Git 平台配置' : 'SVN 配置'" :name="`cfg-${dir.id}`">
                <!-- Git 子页 -->
                <template v-if="dir.vcs_type === 'git'">
                  <n-space vertical :size="8">
                    <n-descriptions :column="1" bordered size="small" label-placement="left">
                      <n-descriptions-item label="平台">
                        <n-space align="center" :size="8">
                          <n-select
                            :value="dir.git_provider || 'github'"
                            :options="gitProviderOptions"
                            size="small"
                            class="git-provider-select"
                            style="width: 132px"
                            @update:value="(v) => handleSetGitProvider(dir, v)"
                          />
                          <n-text depth="3" style="font-size: 11px">
                            {{ (dir.git_provider || 'github') === 'gitlab' ? 'namespace/project + Token' : 'owner/repo + Token' }}
                          </n-text>
                        </n-space>
                      </n-descriptions-item>
                      <n-descriptions-item label="Token">
                        <n-tag :type="(dir.git_provider || 'github') === 'gitlab' ? (dir.gitlab_token_configured ? 'success' : 'warning') : (dir.github_token_configured ? 'success' : 'warning')" size="small">
                          {{ (dir.git_provider || 'github') === 'gitlab' ? (dir.gitlab_token_configured ? '已配置' : '未配置') : (dir.github_token_configured ? '已配置' : '未配置') }}
                        </n-tag>
                      </n-descriptions-item>
                      <n-descriptions-item label="仓库">
                        <n-tag :type="(dir.git_provider || 'github') === 'gitlab' ? (dir.gitlab_repo ? 'success' : 'warning') : (dir.github_repo ? 'success' : 'warning')" size="small">
                          {{ (dir.git_provider || 'github') === 'gitlab' ? (dir.gitlab_repo ? '已绑定' : '未绑定') : (dir.github_repo ? '已绑定' : '未绑定') }}
                        </n-tag>
                        <n-text v-if="(dir.git_provider || 'github') === 'gitlab' ? dir.gitlab_repo : dir.github_repo" code style="font-size: 12px; margin-left: 8px">
                          {{ (dir.git_provider || 'github') === 'gitlab' ? dir.gitlab_repo : dir.github_repo }}
                        </n-text>
                      </n-descriptions-item>
                      <n-descriptions-item label="GitLab 地址" v-if="(dir.git_provider || 'github') === 'gitlab'">
                        <n-text code style="font-size: 12px">{{ dir.gitlab_url || 'https://gitlab.com' }}</n-text>
                      </n-descriptions-item>
                      <n-descriptions-item label="连接状态" v-if="dir._validate_status">
                        <n-tag
                          :type="dir._validate_ok ? 'success' : 'warning'"
                          size="small"
                        >
                          {{ dir._validate_ok ? '已连接' : '未连接' }}
                        </n-tag>
                        <n-text v-if="dir._validate_message" depth="3" style="margin-left: 8px; font-size: 11px">
                          {{ dir._validate_message }}
                        </n-text>
                      </n-descriptions-item>
                    </n-descriptions>

                    <n-space :size="8" :wrap="true">
                      <n-button size="small" @click="handleValidateDir(dir)" :loading="dir._validating">🔄 校验连接</n-button>
                      <n-button size="small" type="primary" ghost @click="dir._showTokenInput = !dir._showTokenInput">
                        {{ ((dir.git_provider || 'github') === 'gitlab' ? dir.gitlab_token_configured : dir.github_token_configured) ? '🔑 更换 Token' : '🔑 设置 Token' }}
                      </n-button>
                      <n-button
                        v-if="(dir.git_provider || 'github') === 'gitlab' ? dir.gitlab_token_configured : dir.github_token_configured"
                        size="small"
                        type="error"
                        ghost
                        @click="handleClearTokenByProvider(dir)"
                      >
                        清除 Token
                      </n-button>
                      <n-button size="small" ghost @click="dir._showRepoInput = !dir._showRepoInput">
                        {{ ((dir.git_provider || 'github') === 'gitlab' ? dir.gitlab_repo : dir.github_repo) ? '📦 更换仓库' : '📦 绑定仓库' }}
                      </n-button>
                      <n-button
                        v-if="(dir.git_provider || 'github') === 'gitlab' ? dir.gitlab_repo : dir.github_repo"
                        size="small"
                        type="error"
                        ghost
                        @click="handleClearRepoByProvider(dir)"
                      >
                        清除仓库
                      </n-button>
                      <n-button
                        v-if="(dir.git_provider || 'github') === 'gitlab'"
                        size="small"
                        ghost
                        @click="dir._showGitlabUrlInput = !dir._showGitlabUrlInput"
                      >
                        🌐 设置 GitLab 地址
                      </n-button>
                    </n-space>

                    <div v-if="dir._showTokenInput" style="margin-top: 6px">
                        <n-input-group>
                          <n-input
                            v-model:value="dir._tokenInput"
                            type="password"
                            show-password-on="click"
                            :placeholder="(dir.git_provider || 'github') === 'gitlab' ? '输入 GitLab Token (PAT / Project Token)' : '输入 GitHub Token (ghp_... / github_pat_...)'"
                            clearable
                            style="flex: 1"
                          />
                          <n-button type="primary" :loading="savingToken" :disabled="!(dir._tokenInput || '').trim()" @click="handleSaveTokenByProvider(dir)">
                            保存
                          </n-button>
                        </n-input-group>
                    </div>

                    <div v-if="dir._showRepoInput" style="margin-top: 6px">
                        <n-input-group>
                          <n-input
                            v-model:value="dir._repoInput"
                            :placeholder="(dir.git_provider || 'github') === 'gitlab' ? 'namespace/project 格式, 如 mygroup/myproject' : 'owner/repo 格式, 如 myorg/myproject'"
                            clearable
                            style="flex: 1"
                          />
                          <n-button type="primary" :loading="savingRepo" :disabled="!(dir._repoInput || '').trim()" @click="handleSaveRepoByProvider(dir)">
                            保存
                          </n-button>
                        </n-input-group>
                    </div>

                    <div v-if="dir._showGitlabUrlInput && (dir.git_provider || 'github') === 'gitlab'" style="margin-top: 6px">
                        <n-input-group>
                          <n-input
                            v-model:value="dir._gitlabUrlInput"
                            placeholder="GitLab 地址, 如 https://gitlab.com 或 https://gitlab.company.com"
                            clearable
                            style="flex: 1"
                          />
                          <n-button type="primary" :loading="savingGitlabUrl" :disabled="!(dir._gitlabUrlInput || '').trim()" @click="handleSaveGitlabUrl(dir)">
                            保存
                          </n-button>
                        </n-input-group>
                    </div>
                  </n-space>
                </template>

                <!-- SVN 子页 -->
                <template v-else>
                  <n-space vertical :size="8">
                    <n-descriptions :column="1" bordered size="small" label-placement="left">
                      <n-descriptions-item label="SVN 仓库地址">
                        <n-tag :type="dir.svn_repo_configured ? 'info' : 'success'" size="small">
                          {{ dir.svn_repo_configured ? '已手动配置' : '自动探测（推荐）' }}
                        </n-tag>
                        <n-text v-if="dir.svn_repo_url" code style="margin-left: 8px; font-size: 12px">{{ dir.svn_repo_url }}</n-text>
                        <n-text
                          v-else-if="dir._validate_status?.repo_url"
                          code
                          style="margin-left: 8px; font-size: 12px"
                        >
                          {{ dir._validate_status.repo_url }}
                        </n-text>
                      </n-descriptions-item>
                      <n-descriptions-item label="SVN 用户名">
                        <n-tag :type="dir.svn_username_configured ? 'warning' : 'success'" size="small">
                          {{ dir.svn_username_configured ? '已手动配置' : '系统凭据（推荐）' }}
                        </n-tag>
                        <n-text
                          v-if="dir._validate_status?.username"
                          code
                          style="margin-left: 8px; font-size: 12px"
                        >
                          {{ dir._validate_status.username }}
                        </n-text>
                        <n-text
                          v-else
                          depth="3"
                          style="margin-left: 8px; font-size: 11px"
                        >
                          未返回登录用户名（正常，取决于 SVN 客户端与凭据缓存）
                        </n-text>
                        <n-text
                          v-if="dir._validate_status?.last_changed_author"
                          depth="3"
                          style="margin-left: 8px; font-size: 11px"
                        >
                          最近提交者: {{ dir._validate_status.last_changed_author }}
                        </n-text>
                      </n-descriptions-item>
                      <n-descriptions-item label="Trunk 路径">
                        <n-text code style="font-size: 12px">{{ dir.svn_trunk_path || 'trunk' }}</n-text>
                      </n-descriptions-item>
                    </n-descriptions>
                    <n-alert type="info" :bordered="false" style="background: rgba(32,128,240,.08)">
                      默认使用系统 SVN 环境和当前工作副本自动探测；仅在权限不足时再填写覆盖参数。
                    </n-alert>
                    <n-space :size="8" align="center">
                      <n-button size="small" @click="handleValidateDir(dir)" :loading="dir._validating">🔍 校验 SVN 可用性</n-button>
                      <n-tag v-if="dir._validate_status" :type="dir._validate_ok ? 'success' : 'warning'" size="small">
                        {{ dir._validate_ok ? '可用' : '不可用' }}
                      </n-tag>
                      <n-text v-if="dir._validate_message" depth="3" style="font-size: 11px">{{ dir._validate_message }}</n-text>
                    </n-space>

                    <n-collapse :default-expanded-names="[]">
                      <n-collapse-item :name="`svn-adv-${dir.id}`" title="高级覆盖参数（仅权限不足时使用）">
                        <n-grid :cols="2" :x-gap="8" :y-gap="8">
                          <n-gi>
                            <n-input v-model:value="dir._svnRepoUrlInput" placeholder="可选: SVN_REPO_URL 覆盖地址" clearable />
                          </n-gi>
                          <n-gi>
                            <n-input v-model:value="dir._svnTrunkPathInput" placeholder="可选: trunk 路径 (默认 trunk)" clearable />
                          </n-gi>
                          <n-gi>
                            <n-input
                              v-model:value="dir._svnUsernameInput"
                              placeholder="可选: SVN 用户名 (默认系统凭据)"
                              clearable
                              autocomplete="off"
                            />
                          </n-gi>
                          <n-gi>
                            <n-input
                              v-model:value="dir._svnPasswordInput"
                              type="password"
                              show-password-on="click"
                              placeholder="可选: SVN 密码"
                              clearable
                              autocomplete="new-password"
                            />
                          </n-gi>
                        </n-grid>
                        <n-space :size="8" style="margin-top: 8px">
                          <n-button size="small" type="primary" ghost @click="handleSaveSvnOverride(dir)">保存覆盖参数</n-button>
                          <n-button size="small" ghost @click="handleResetSvnOverride(dir)">恢复自动模式</n-button>
                        </n-space>
                      </n-collapse-item>
                    </n-collapse>
                  </n-space>
                </template>
              </n-collapse-item>
            </n-collapse>
          </div>
        </n-space>
        <n-empty v-else description="尚未配置工作目录，点击上方「添加目录」开始" />
      </n-spin>
    </n-card>

    <!-- 工作区概览 -->
    <n-card title="🔍 工作区概览" size="small" style="background: #16213e">
      <template #header-extra>
        <n-button size="small" @click="fetchWorkspaceOverview(true)" :loading="loadingWorkspace">
          🔄 刷新
        </n-button>
      </template>
      <n-spin :show="loadingWorkspace">
        <template v-if="workspaceOverview">
          <!-- 工作区路径 -->
          <n-alert type="info" :bordered="false" style="margin-bottom: 12px; background: rgba(32,128,240,.08)">
            <template #icon><span>📂</span></template>
            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
              <n-text depth="3" style="font-size: 12px; white-space: nowrap">当前路径:</n-text>
              <n-text code style="font-size: 12px; word-break: break-all">{{ workspaceOverview.workspace_path }}</n-text>
              <n-tag v-if="!workspaceOverview.workspace_exists" type="error" size="small">目录不存在</n-tag>
            </div>
          </n-alert>

          <!-- VCS + 基本信息 -->
          <n-descriptions :column="2" label-placement="left" bordered size="small" style="margin-bottom: 12px">
            <n-descriptions-item label="版本控制">
              <n-tag :type="workspaceOverview.vcs_type === 'none' ? 'default' : 'info'" size="small">
                {{ ({'git': 'Git', 'svn': 'SVN', 'none': '无'} as Record<string, string>)[workspaceOverview.vcs_type] || workspaceOverview.vcs_type }}
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="分支 / 路径" v-if="workspaceOverview.vcs?.branch">
              <n-text code>{{ workspaceOverview.vcs.branch }}</n-text>
            </n-descriptions-item>
            <n-descriptions-item label="最新提交" v-if="workspaceOverview.vcs?.last_commit_hash">
              <n-text code style="font-size: 12px">
                {{ workspaceOverview.vcs.last_commit_hash.slice(0, 8) }}
              </n-text>
              <n-text depth="3" style="margin-left: 6px; font-size: 12px">
                {{ workspaceOverview.vcs.last_commit_message }}
              </n-text>
            </n-descriptions-item>
            <n-descriptions-item label="未提交变更">
              <n-tag :type="workspaceOverview.uncommitted_count > 0 ? 'warning' : 'success'" size="small">
                {{ workspaceOverview.uncommitted_count }} 个文件
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="文件总数">
              {{ workspaceOverview.total_files }}
            </n-descriptions-item>
          </n-descriptions>

          <!-- 语言统计 -->
          <div v-if="workspaceOverview.language_stats?.length" style="margin-bottom: 12px">
            <n-text depth="3" style="font-size: 12px; margin-bottom: 6px; display: block">语言分布</n-text>
            <n-space :size="4" :wrap="true">
              <n-tag
                v-for="lang in workspaceOverview.language_stats"
                :key="lang.language"
                size="small"
                :bordered="false"
                :style="{ background: langColor(lang.language) + '25', color: langColor(lang.language) }"
              >
                {{ lang.language }}
                <template #avatar>
                  <span style="font-size: 10px; opacity: 0.7">{{ lang.percentage }}%</span>
                </template>
              </n-tag>
            </n-space>
          </div>

          <!-- 关键文件 -->
          <div v-if="workspaceOverview.key_files?.length" style="margin-bottom: 12px">
            <n-text depth="3" style="font-size: 12px; margin-bottom: 6px; display: block">关键文件</n-text>
            <n-space :size="4" :wrap="true">
              <n-tag v-for="f in workspaceOverview.key_files" :key="f" size="small" :bordered="false" type="success">
                {{ f }}
              </n-tag>
            </n-space>
          </div>

          <!-- 贡献者 -->
          <div v-if="workspaceOverview.contributors?.length" style="margin-bottom: 12px">
            <n-text depth="3" style="font-size: 12px; margin-bottom: 6px; display: block">贡献者 Top {{ workspaceOverview.contributors.length }}</n-text>
            <n-space :size="6" :wrap="true">
              <n-tag
                v-for="c in workspaceOverview.contributors"
                :key="c.name"
                size="small"
                round
              >
                {{ c.name }}
                <template #avatar>
                  <span style="font-size: 10px; opacity: 0.7">{{ c.commits }}</span>
                </template>
              </n-tag>
            </n-space>
          </div>

          <!-- 近期提交 -->
          <div v-if="workspaceOverview.recent_commits?.length">
            <n-text depth="3" style="font-size: 12px; margin-bottom: 6px; display: block">近期提交</n-text>
            <n-space vertical :size="2">
              <div v-for="(cm, idx) in workspaceOverview.recent_commits.slice(0, 8)" :key="idx" style="font-size: 12px; line-height: 1.6">
                <n-text code style="font-size: 11px; margin-right: 6px">{{ (cm.hash || '').slice(0, 7) }}</n-text>
                <n-text>{{ cm.message }}</n-text>
                <n-text depth="3" style="margin-left: 6px; font-size: 11px">{{ cm.author }} · {{ cm.time }}</n-text>
              </div>
            </n-space>
          </div>
        </template>
        <n-empty v-else description="加载中…" />
      </n-spin>
    </n-card>

    <!-- 系统状态 -->
    <n-card title="🖥️ 系统状态" size="small" style="background: #16213e">
      <n-spin :show="loadingStatus">
        <n-descriptions :column="1" label-placement="left" bordered v-if="systemStatus">
          <n-descriptions-item :label="vcsLabel + ' 分支'">
            {{ systemStatus.vcs?.branch || systemStatus.git?.branch || '—' }}
          </n-descriptions-item>
          <n-descriptions-item label="最近提交">
            <n-space vertical :size="2">
              <n-text v-for="(c, i) in recentCommitLines" :key="i" code style="font-size: 12px">
                {{ c }}
              </n-text>
              <n-text v-if="!recentCommitLines.length" depth="3">暂无提交记录</n-text>
            </n-space>
          </n-descriptions-item>
        </n-descriptions>
      </n-spin>
      <n-button style="margin-top: 8px" @click="fetchStatus" :loading="loadingStatus" size="small">
        🔄 刷新
      </n-button>
    </n-card>

    <!-- 容器状态 -->
    <n-card title="🐳 Docker 容器" size="small" style="background: #16213e" v-if="systemStatus?.containers">
      <n-table :bordered="false" size="small">
        <thead><tr><th>容器名</th><th>状态</th><th>端口</th></tr></thead>
        <tbody>
          <tr v-for="c in systemStatus.containers" :key="c.name">
            <td>{{ c.name }}</td>
            <td><n-tag :type="c.status?.includes('Up') ? 'success' : 'error'" size="small">{{ c.status }}</n-tag></td>
            <td style="font-size: 12px">{{ c.ports || '-' }}</td>
          </tr>
        </tbody>
      </n-table>
    </n-card>

    <!-- 外部 API 端点检测 -->
    <n-card title="🔌 外部 API 端点检测" size="small" style="background: #16213e">
      <template #header-extra>
        <n-space :size="8">
          <n-text v-if="probeResult?.context" depth="3" style="font-size: 11px; max-width: 560px">
            作用域: {{ probeResult.context.source === 'workspace' ? '当前工作目录' : '运行时' }}
            · {{ probeResult.context.vcs_type ? probeResult.context.vcs_type.toUpperCase() : 'NONE' }}
            · {{ probeResult.context.github_repo || '未绑定 GitHub 仓库' }}
          </n-text>
          <n-text v-if="probeResult" depth="3" style="font-size: 11px">
            {{ probeResult.ok }}✅ {{ probeResult.warning }}⚠️ {{ probeResult.error }}❌ {{ probeResult.skipped }}⏭
            · {{ probeResult.total_ms }}ms
          </n-text>
          <n-button type="primary" size="small" @click="probeAll" :loading="probingAll">
            🚀 一键全测
          </n-button>
        </n-space>
      </template>

      <n-table :bordered="false" size="small" style="margin-top: 4px">
        <thead>
          <tr>
            <th class="sys-col-group">分组</th>
            <th>端点</th>
            <th class="sys-col-auth">认证</th>
            <th class="sys-col-status">状态</th>
            <th class="sys-col-latency">延迟</th>
            <th class="sys-col-action">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="ep in probeEndpoints" :key="ep.id">
            <td class="sys-col-group" style="font-size: 12px; color: #aaa">{{ ep.group }}</td>
            <td>
              <div>
                <n-text style="font-size: 12px; font-family: monospace">{{ ep.name }}</n-text>
              </div>
              <n-text depth="3" style="font-size: 11px">{{ ep.description }}</n-text>
              <div style="margin-top: 2px">
                <n-text depth="3" style="font-size: 11px; font-family: monospace">
                  {{ ep._result?.resolved_url || ep.resolved_url || ep.url }}
                </n-text>
              </div>
              <!-- 测试后显示消息 -->
              <div v-if="ep._result && ep._result.status !== 'ok'" style="margin-top: 2px; display: flex; flex-direction: column; gap: 2px">
                <n-text :type="ep._result.status === 'error' ? 'error' : 'warning'" style="font-size: 11px; white-space: pre-wrap">
                  {{ ep._result.message }}
                </n-text>
                <n-text depth="3" style="font-size: 11px">
                  HTTP: {{ ep._result.http_status || '—' }} · 耗时: {{ ep._result.latency_ms || '—' }}ms
                </n-text>
                <n-text v-if="ep._result.context?.workspace_path" depth="3" style="font-size: 11px">
                  上下文: {{ ep._result.context.source === 'workspace' ? '当前工作目录' : '运行时' }}
                  / {{ ep._result.context.vcs_type }} / {{ ep._result.context.workspace_path }}
                </n-text>
                <n-text
                  v-for="(tip, tipIdx) in (ep._result.troubleshooting || [])"
                  :key="`${ep.id}-tip-${tipIdx}`"
                  depth="3"
                  style="font-size: 11px"
                >
                  💡 {{ tip }}
                </n-text>
              </div>
            </td>
            <td>
              <n-tag size="small" :type="ep.auth_type === 'none' ? 'default' : 'info'" :bordered="false" style="font-size: 10px">
                {{ { none: '无', github_pat: 'PAT', copilot_oauth: 'OAuth', copilot_session: 'Session' }[ep.auth_type] || ep.auth_type }}
              </n-tag>
            </td>
            <td>
              <n-tag v-if="ep._result" size="small" :bordered="false" :type="probeStatusType(ep._result.status)">
                {{ probeStatusLabel(ep._result.status) }}
              </n-tag>
              <n-spin v-else-if="ep._loading" :size="14" />
              <n-text v-else depth="3" style="font-size: 11px">—</n-text>
            </td>
            <td>
              <n-text v-if="ep._result" style="font-size: 12px; font-variant-numeric: tabular-nums">
                {{ ep._result.latency_ms ? ep._result.latency_ms + 'ms' : '—' }}
              </n-text>
            </td>
            <td>
              <n-button size="tiny" quaternary @click="probeOne(ep)" :loading="ep._loading">
                ▶
              </n-button>
            </td>
          </tr>
        </tbody>
      </n-table>
    </n-card>
  </n-space>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useMessage } from 'naive-ui'
import { systemApi, endpointProbeApi, workspaceDirApi } from '@/api'

const message = useMessage()

const githubStatus = ref<any>({})
const gitlabStatus = ref<any>({})
const systemStatus = ref<any>(null)
const loadingStatus = ref(false)

// GitHub Token / Repo 管理
const savingToken = ref(false)
const savingRepo = ref(false)
const savingGitlabUrl = ref(false)

// 工作目录管理
const workspaceDirs = ref<any[]>([])
const loadingDirs = ref(false)
const showAddDir = ref(false)
const addingDir = ref(false)
const newDirPath = ref('')
const newDirLabel = ref('')

// 工作区概览
const workspaceOverview = ref<any>(null)
const loadingWorkspace = ref(false)

// 端点探测
const probeEndpoints = ref<any[]>([])
const probingAll = ref(false)
const probeResult = ref<any>(null)

// SVN 校验缓存：避免每次刷新都打 svn 命令
const SVN_VALIDATE_TTL_MS = 2 * 60 * 1000
const svnValidateCache = new Map<number, { ts: number; ok: boolean; status: any }>()

// ── VCS 兼容 ──────────────────────────────
const vcsLabel = computed(() => {
  const t = systemStatus.value?.vcs?.type || workspaceOverview.value?.vcs_type
  if (t === 'svn') return 'SVN'
  if (t === 'git') return 'Git'
  return 'VCS'
})

const recentCommitLines = computed(() => {
  // 优先使用新格式（向后兼容旧 git 字段）
  return systemStatus.value?.git?.recent_commits || []
})
const gitProviderOptions = [
  { label: 'GitHub', value: 'github' },
  { label: 'GitLab', value: 'gitlab' },
]

function gitProviderLabel(provider: string) {
  return (provider || 'github').toLowerCase() === 'gitlab' ? 'GitLab' : 'GitHub'
}

function svnRepoReady(dir: any): boolean {
  return !!(dir?.svn_repo_configured || dir?._validate_status?.repo_url)
}

function svnUserReady(dir: any): boolean {
  return !!(dir?.svn_username_configured || dir?._validate_status?.username)
}

// ── 语言颜色 ──────────────────────────────
const LANG_COLORS: Record<string, string> = {
  Python: '#3572A5', TypeScript: '#3178C6', JavaScript: '#F7DF1E', Vue: '#42b883',
  Java: '#B07219', Go: '#00ADD8', Rust: '#DEA584', 'C++': '#F34B7D', 'C#': '#178600',
  Ruby: '#CC342D', PHP: '#4F5D95', Swift: '#F05138', Kotlin: '#A97BFF', Dart: '#00B4AB',
  HTML: '#E34F26', CSS: '#1572B6', SCSS: '#C6538C', Shell: '#89E051', SQL: '#E38C00',
  Markdown: '#083FA1', YAML: '#CB171E', JSON: '#A0A0A0', XML: '#0060AC', Docker: '#2496ED',
}
function langColor(lang: string): string {
  return LANG_COLORS[lang] || '#63e2b7'
}

function probeStatusType(status: string) {
  return { ok: 'success', warning: 'warning', error: 'error', skipped: 'default' }[status] || 'default'
}
function probeStatusLabel(status: string) {
  return { ok: '正常', warning: '警告', error: '异常', skipped: '跳过' }[status] || status
}

async function fetchProbeEndpoints() {
  try {
    const { data } = await endpointProbeApi.listEndpoints()
    probeEndpoints.value = data.map((ep: any) => ({ ...ep, _result: null, _loading: false }))
  } catch {}
}

async function probeAll() {
  probingAll.value = true
  probeEndpoints.value.forEach((ep: any) => { ep._loading = true; ep._result = null })
  try {
    const { data } = await endpointProbeApi.testAll()
    probeResult.value = data
    for (const r of data.results) {
      const ep = probeEndpoints.value.find((e: any) => e.id === r.id)
      if (ep) { ep._result = r; ep._loading = false }
    }
  } catch (e: any) {
    message.error('探测失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    probingAll.value = false
    probeEndpoints.value.forEach((ep: any) => { ep._loading = false })
  }
}

async function probeOne(ep: any) {
  ep._loading = true
  ep._result = null
  try {
    const { data } = await endpointProbeApi.testOne(ep.id)
    ep._result = data
  } catch (e: any) {
    ep._result = { status: 'error', message: e.response?.data?.detail || e.message, latency_ms: 0 }
  } finally {
    ep._loading = false
  }
}

async function handleSaveTokenByProvider(dir: any) {
  savingToken.value = true
  try {
    const p = (dir.git_provider || 'github').toLowerCase()
    const token = (dir._tokenInput || '').trim()
    if (!token) return
    if (p === 'gitlab') {
      await workspaceDirApi.update(dir.id, { gitlab_token: token })
      message.success('GitLab Token 已保存')
    } else {
      await workspaceDirApi.update(dir.id, { github_token: token })
      message.success('GitHub Token 已保存')
    }
    dir._tokenInput = ''
    dir._showTokenInput = false
    await fetchWorkspaceDirs()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '保存失败')
  } finally {
    savingToken.value = false
  }
}

async function handleClearTokenByProvider(dir: any) {
  try {
    const p = (dir.git_provider || 'github').toLowerCase()
    if (p === 'gitlab') {
      await workspaceDirApi.update(dir.id, { gitlab_token: '' })
      message.success('GitLab Token 已清除')
    } else {
      await workspaceDirApi.update(dir.id, { github_token: '' })
      message.success('GitHub Token 已清除')
    }
    await fetchWorkspaceDirs()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '清除失败')
  }
}

async function handleClearRepoByProvider(dir: any) {
  try {
    const p = (dir.git_provider || 'github').toLowerCase()
    if (p === 'gitlab') {
      await workspaceDirApi.update(dir.id, { gitlab_repo: '' })
      message.success('GitLab 仓库已清除')
    } else {
      await workspaceDirApi.update(dir.id, { github_repo: '' })
      message.success('GitHub 仓库已清除')
    }
    await fetchWorkspaceDirs()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '清除失败')
  }
}

async function handleSaveRepoByProvider(dir: any) {
  savingRepo.value = true
  try {
    const p = (dir.git_provider || 'github').toLowerCase()
    const repo = (dir._repoInput || '').trim()
    if (!repo) return
    if (p === 'gitlab') {
      await workspaceDirApi.update(dir.id, { gitlab_repo: repo })
      message.success('GitLab 仓库已绑定')
    } else {
      await workspaceDirApi.update(dir.id, { github_repo: repo })
      message.success('GitHub 仓库已绑定')
    }
    dir._repoInput = ''
    dir._showRepoInput = false
    await fetchWorkspaceDirs()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '保存失败')
  } finally {
    savingRepo.value = false
  }
}

async function handleSetGitProvider(dir: any, provider: 'github' | 'gitlab') {
  try {
    await workspaceDirApi.update(dir.id, { git_provider: provider })
    message.success(`已切换 Git 平台: ${provider.toUpperCase()}`)
    await fetchWorkspaceDirs()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '切换平台失败')
  }
}

async function handleSaveGitlabUrl(dir: any) {
  savingGitlabUrl.value = true
  try {
    const url = (dir._gitlabUrlInput || '').trim()
    if (!url) return
    await workspaceDirApi.update(dir.id, { gitlab_url: url })
    dir._showGitlabUrlInput = false
    dir._gitlabUrlInput = ''
    message.success('GitLab 地址已保存')
    await fetchWorkspaceDirs()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '保存失败')
  } finally {
    savingGitlabUrl.value = false
  }
}

async function handleValidateDir(dir: any) {
  // 优先使用新鲜缓存
  const cached = svnValidateCache.get(dir.id)
  if (cached && (Date.now() - cached.ts) < SVN_VALIDATE_TTL_MS) {
    dir._validate_status = cached.status || null
    dir._validate_ok = !!cached.ok
    dir._validate_message = cached.status?.message || cached.status?.hint || ''
    return
  }

  dir._validating = true
  try {
    const { data } = await workspaceDirApi.validate(dir.id)
    dir._validate_status = data?.status || null
    dir._validate_ok = !!data?.ok
    dir._validate_message = data?.status?.message || data?.status?.hint || ''
    svnValidateCache.set(dir.id, {
      ts: Date.now(),
      ok: !!data?.ok,
      status: data?.status || null,
    })
    if (data?.ok) message.success('配置校验通过')
    else message.warning(dir._validate_message || '配置不可用')
  } catch (e: any) {
    dir._validate_ok = false
    dir._validate_message = e.response?.data?.detail || e.message || '校验失败'
    message.error(dir._validate_message)
  } finally {
    dir._validating = false
  }
}

async function autoRefreshSvnValidation(dirs: any[]) {
  // 仅对 SVN 目录做静默刷新：先用缓存，缓存过期则后台校验并更新 UI
  for (const dir of dirs) {
    if (dir.vcs_type !== 'svn') continue

    const cached = svnValidateCache.get(dir.id)
    if (cached && (Date.now() - cached.ts) < SVN_VALIDATE_TTL_MS) {
      dir._validate_status = cached.status || null
      dir._validate_ok = !!cached.ok
      dir._validate_message = cached.status?.message || cached.status?.hint || ''
      continue
    }

    try {
      dir._validating = true
      const { data } = await workspaceDirApi.validate(dir.id)
      dir._validate_status = data?.status || null
      dir._validate_ok = !!data?.ok
      dir._validate_message = data?.status?.message || data?.status?.hint || ''
      svnValidateCache.set(dir.id, {
        ts: Date.now(),
        ok: !!data?.ok,
        status: data?.status || null,
      })
    } catch {
      // 静默自动刷新，不弹消息
    } finally {
      dir._validating = false
    }
  }
}

async function handleSaveSvnOverride(dir: any) {
  try {
    await workspaceDirApi.update(dir.id, {
      svn_repo_url: (dir._svnRepoUrlInput || '').trim(),
      svn_username: (dir._svnUsernameInput || '').trim(),
      svn_password: (dir._svnPasswordInput || '').trim(),
      svn_trunk_path: (dir._svnTrunkPathInput || '').trim() || 'trunk',
    })
    svnValidateCache.delete(dir.id)
    message.success('SVN 覆盖参数已保存')
    await fetchWorkspaceDirs()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '保存失败')
  }
}

async function handleResetSvnOverride(dir: any) {
  try {
    await workspaceDirApi.update(dir.id, {
      svn_repo_url: '',
      svn_username: '',
      svn_password: '',
    })
    svnValidateCache.delete(dir.id)
    message.success('已恢复为系统自动模式')
    await fetchWorkspaceDirs()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '重置失败')
  }
}

async function fetchStatus() {
  loadingStatus.value = true
  try {
    const { data } = await systemApi.status()
    systemStatus.value = data
    githubStatus.value = data.github || {}
    gitlabStatus.value = data.gitlab || {}
  } catch {}
  finally { loadingStatus.value = false }
}

async function fetchWorkspaceOverview(forceRefresh = false) {
  loadingWorkspace.value = true
  try {
    const { data } = await systemApi.workspaceOverview(forceRefresh)
    workspaceOverview.value = data
  } catch (e: any) {
    console.warn('工作区概览加载失败', e)
  } finally {
    loadingWorkspace.value = false
  }
}

// ── 工作目录管理 ─────────────────────────
async function fetchWorkspaceDirs() {
  loadingDirs.value = true
  try {
    const { data } = await workspaceDirApi.list()
    workspaceDirs.value = data.map((d: any) => {
      const cached = svnValidateCache.get(d.id)
      const cacheValid = !!cached && (Date.now() - cached.ts) < SVN_VALIDATE_TTL_MS
      return ({
      ...d,
      _switching: false,
      _validating: false,
      _validate_ok: cacheValid ? !!cached?.ok : null,
      _validate_message: cacheValid ? (cached?.status?.message || cached?.status?.hint || '') : '',
      _validate_status: cacheValid ? (cached?.status || null) : null,
      _showTokenInput: false,
      _showRepoInput: false,
      _showGitlabUrlInput: false,
      _tokenInput: '',
      _repoInput: '',
      _gitlabUrlInput: d.gitlab_url || 'https://gitlab.com',
      _svnRepoUrlInput: d.svn_repo_url || '',
      _svnUsernameInput: d.svn_username || '',
      _svnPasswordInput: '',
      _svnTrunkPathInput: d.svn_trunk_path || 'trunk',
    })
    })
    // 默认静默刷新 SVN 详细信息（带 TTL 缓存）
    autoRefreshSvnValidation(workspaceDirs.value)
  } catch (e: any) {
    console.warn('加载工作目录列表失败', e)
  } finally {
    loadingDirs.value = false
  }
}

async function handleAddDir() {
  if (!newDirPath.value.trim()) {
    message.warning('请输入工作目录路径')
    return false
  }
  addingDir.value = true
  try {
    await workspaceDirApi.add({ path: newDirPath.value.trim(), label: newDirLabel.value.trim() })
    message.success('工作目录已添加')
    newDirPath.value = ''
    newDirLabel.value = ''
    showAddDir.value = false
    await fetchWorkspaceDirs()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '添加失败')
    return false
  } finally {
    addingDir.value = false
  }
}

async function handleActivate(dir: any) {
  dir._switching = true
  try {
    await workspaceDirApi.activate(dir.id)
    message.success(`已切换到: ${dir.label || dir.path}`)
    await fetchWorkspaceDirs()
    // 切换后重新加载工作区概览
    fetchWorkspaceOverview(true)
    // 重新加载系统状态
    fetchStatus()
    // 通知其他组件（头部、Dashboard）同步更新
    window.dispatchEvent(new CustomEvent('workspace-switched', { detail: { id: dir.id, path: dir.path } }))
  } catch (e: any) {
    message.error(e.response?.data?.detail || '切换失败')
  } finally {
    dir._switching = false
  }
}

async function handleRemoveDir(dir: any) {
  try {
    await workspaceDirApi.remove(dir.id)
    message.success('工作目录已移除')
    await fetchWorkspaceDirs()
    fetchWorkspaceOverview(true)
  } catch (e: any) {
    message.error(e.response?.data?.detail || '移除失败')
  }
}

onMounted(() => {
  fetchStatus()
  fetchProbeEndpoints()
  fetchWorkspaceDirs()
  // 工作区概览独立加载，不阻塞页面
  fetchWorkspaceOverview()
})
</script>

<style scoped>
.sys-col-group { width: 160px; }
.sys-col-auth { width: 70px; }
.sys-col-status { width: 90px; }
.sys-col-latency { width: 70px; }
.sys-col-action { width: 56px; }

.git-provider-select :deep(.n-base-selection) {
  background: rgba(255, 255, 255, 0.04);
}

@media (max-width: 768px) {
  .sys-col-group { display: none; }
  .sys-col-latency { width: 50px; }
  .sys-col-auth { width: 50px; }
  .sys-col-status { width: 60px; }
  .sys-col-action { width: 40px; }
}
</style>
