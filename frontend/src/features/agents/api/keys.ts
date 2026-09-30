export const agentKeys = {
  all: ["agents"] as const,
  opencode: () => ["agents", "opencode"] as const,
  editorContext: () => ["agents", "opencode", "editor-context"] as const,
};

export const AGENTS_STALE_TIME_MS = 10_000;
export const AGENTS_GC_TIME_MS = 5 * 60_000;
