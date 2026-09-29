import type { OpenCodeAgentsDto } from "../../features/agents/api/types";

export function openCodeAgentsPayload({
  count = 0,
}: { count?: number } = {}): OpenCodeAgentsDto {
  return {
    agents: Array.from({ length: count }, (_, index) => ({
      name: `agent-${index + 1}`,
      schemaGeneration: "v2" as const,
      description: null,
      instructions: null,
      prompt: null,
      model: null,
      variant: null,
      modelRaw: null,
      mode: null,
      temperature: null,
      topP: null,
      steps: null,
      disabled: null,
      hidden: null,
      color: null,
      permissions: null,
      permission: null,
      tools: null,
      additionalOptions: {},
      source: null,
      editability: "read-only" as const,
      readOnlyReasons: [],
      readOnly: true,
      valid: true,
      diagnostic: null,
    })),
    writeTarget: "/tmp/opencode/opencode.jsonc",
    sources: [],
    diagnostics: [],
    limitation: "Static compatibility only.",
  };
}
