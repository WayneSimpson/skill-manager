import { useLocalizedCopy, type CopyShape, type LocalizedCopy } from "../../i18n";

const englishSettingsCopy = {
  title: "Settings",
  subtitle: "Local paths and per-harness discovery.",
  loading: "Loading settings",
  storage: {
    heading: "Local storage",
    storeTitle: "Skill Manager store",
    storeSubtitle: "Canonical copies of skills in use live here.",
    cacheTitle: "Marketplace cache",
    cacheSubtitle: "Downloaded previews and install bundles.",
  },
  harnesses: {
    heading: "Harness roots",
    detected: "Detected on this machine",
    notDetected: "Not detected on this machine",
    enableSupport: (label: string) => `Enable ${label} support`,
    saving: "Saving...",
  },
  runtimeSkills: {
    heading: "OpenCode runtime skills",
    subtitle: "Connect to a local OpenCode server to load its current skill snapshot.",
    serverUrlLabel: "Local OpenCode server URL",
    serverUrlPlaceholder: "http://127.0.0.1:4096",
    directoryLabel: "Absolute OpenCode directory",
    directoryPlaceholder: "/absolute/path/to/opencode",
    usernameLabel: "Basic username (optional)",
    passwordLabel: "Basic password (optional)",
    passwordHint: "Used only for this request and cleared after submit.",
    consentLabel: "I explicitly allow Skill Manager to contact this local server.",
    consentWarning:
      "This request may initialize OpenCode, execute plugins, install dependencies, and write local state.",
    refresh: "Refresh runtime skills",
    refreshAria: "Refresh OpenCode snapshot",
    refreshing: "Refreshing...",
    clearSnapshot: "Clear snapshot",
    clearing: "Clearing...",
    statusLabel: "Connection status",
    snapshotLabel: "Runtime snapshot",
    statuses: {
      disconnected: "Disconnected",
      ready: "Ready",
      error: "Error",
    },
    checking: "Checking the current snapshot...",
    disconnectedBody: "No runtime skill snapshot is connected.",
    snapshotCount: (count: number) => `${count} runtime skill${count === 1 ? "" : "s"} in the current snapshot.`,
    unableToLoad: "Unable to load runtime skill status.",
    incompleteCredentials: "Enter both a username and password, or leave both blank.",
    capabilityLimitation: (reason: string) => `Cannot manage this runtime skill automatically: ${reason}`,
  },
  errors: {
    unableToLoad: "Unable to load settings.",
    unableToUpdateHarnessSupport: "Unable to update harness support.",
  },
} as const;

export type SettingsCopy = CopyShape<typeof englishSettingsCopy>;

export const settingsCopy = {
  en: englishSettingsCopy,
  "zh-CN": {
    title: "设置",
    subtitle: "本地路径和每个 harness 的发现设置。",
    loading: "正在加载设置",
    storage: {
      heading: "本地存储",
      storeTitle: "Skill Manager 存储",
      storeSubtitle: "使用中的 Skill 会以规范副本保存在这里。",
      cacheTitle: "商城缓存",
      cacheSubtitle: "已下载的预览和安装包。",
    },
    harnesses: {
      heading: "Harness 根目录",
      detected: "已在这台机器上检测到",
      notDetected: "未在这台机器上检测到",
      enableSupport: (label: string) => `启用 ${label} 支持`,
      saving: "保存中...",
    },
    runtimeSkills: {
      heading: "OpenCode 运行时 Skill",
      subtitle: "连接本地 OpenCode 服务器，以加载当前 Skill 快照。",
      serverUrlLabel: "本地 OpenCode 服务器 URL",
      serverUrlPlaceholder: "http://127.0.0.1:4096",
      directoryLabel: "绝对 OpenCode 目录",
      directoryPlaceholder: "/absolute/path/to/opencode",
      usernameLabel: "Basic 用户名（可选）",
      passwordLabel: "Basic 密码（可选）",
      passwordHint: "仅用于本次请求，提交后会清除。",
      consentLabel: "我明确允许 Skill Manager 联系此本地服务器。",
      consentWarning: "此请求可能初始化 OpenCode、执行插件、安装依赖并写入本地状态。",
      refresh: "刷新运行时 Skill",
      refreshAria: "刷新 OpenCode 快照",
      refreshing: "刷新中...",
      clearSnapshot: "清除快照",
      clearing: "清除中...",
      statusLabel: "连接状态",
      snapshotLabel: "运行时快照",
      statuses: {
        disconnected: "未连接",
        ready: "就绪",
        error: "错误",
      },
      checking: "正在检查当前快照...",
      disconnectedBody: "当前没有已连接的运行时 Skill 快照。",
      snapshotCount: (count: number) => `当前快照中有 ${count} 个运行时 Skill。`,
      unableToLoad: "无法加载运行时 Skill 状态。",
      incompleteCredentials: "请输入用户名和密码，或同时留空。",
      capabilityLimitation: (reason: string) => `无法自动管理此运行时 Skill：${reason}`,
    },
    errors: {
      unableToLoad: "无法加载设置。",
      unableToUpdateHarnessSupport: "无法更新 harness 支持状态。",
    },
  },
} satisfies LocalizedCopy<SettingsCopy>;

export function useSettingsCopy(): SettingsCopy {
  return useLocalizedCopy(settingsCopy);
}
