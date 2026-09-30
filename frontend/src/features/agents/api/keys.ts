export const agentKeys = {
  all: ["agents"] as const,
  opencode: () => ["agents", "opencode"] as const,
  editorContext: () => ["agents", "opencode", "editor-context"] as const,
  applyCapability: () => ["agents", "opencode", "apply-capability"] as const,
  applyStatus: () => ["agents", "opencode", "apply-status"] as const,
  modelCatalogue: () => ["agents", "opencode", "model-catalogue"] as const,
  permissionActions: () => ["agents", "opencode", "permission-actions"] as const,
};

export const AGENTS_STALE_TIME_MS = 10_000;
export const AGENTS_GC_TIME_MS = 5 * 60_000;
