export type AgentEditability = "config" | "read-only";
export type AgentSchemaGeneration = "v1" | "v2";

export interface OpenCodeAgentSourceDto {
  file: string;
  path: string;
  format: "json" | "jsonc";
  isWriteTarget: boolean;
}

export interface OpenCodeAgentDto {
  name: string;
  schemaGeneration: AgentSchemaGeneration;
  description: string | null;
  /** Canonical instructions (V1 prompt / V2 system); `prompt` is a legacy alias. */
  instructions: string | null;
  prompt: string | null;
  model: string | null;
  /** First-class model variant / reasoning setting when one is declared. */
  variant: string | null;
  /** Original model selection form, preserved verbatim for future safe round-trips. */
  modelRaw: unknown;
  mode: string | null;
  /** Canonical permissions (V1 object / V2 rule list), raw fidelity. */
  permissions: Record<string, unknown> | unknown[] | null;
  permission: Record<string, unknown> | unknown[] | null;
  disabled: boolean | null;
  hidden: boolean | null;
  color: string | null;
  temperature: number | null;
  topP: number | null;
  steps: number | null;
  tools: Record<string, unknown> | null;
  additionalOptions: Record<string, unknown>;
  source: OpenCodeAgentSourceDto | null;
  editability: AgentEditability;
  readOnlyReasons: string[];
  readOnly: boolean;
  valid: boolean;
  diagnostic: string | null;
}

export interface OpenCodeConfigSourceDto {
  name: string;
  path: string;
  format: "json" | "jsonc";
  precedence: number;
  status: "loaded" | "missing" | "invalid" | "unreadable";
  diagnostic: string | null;
}

export interface OpenCodeAgentsDto {
  agents: OpenCodeAgentDto[];
  writeTarget: string;
  sources: OpenCodeConfigSourceDto[];
  diagnostics: string[];
  limitation: string;
}
