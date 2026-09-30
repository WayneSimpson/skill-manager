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

export interface AgentEditorContextDto {
  writeTarget: string;
  create: {
    targetGeneration: AgentSchemaGeneration | null;
    requiresGenerationChoice: boolean;
    reason?: string;
  };
  sourceHash: string;
  readOnly: boolean;
}

export interface AgentFieldsInput {
  name?: string | null;
  description?: string | null;
  instructions?: string | null;
  model?: string | null;
  variant?: string | null;
  mode?: string | null;
  renameTo?: string | null;
}

export interface AgentPreviewDto {
  mode: "create" | "update";
  generation: AgentSchemaGeneration;
  targetFile: string;
  sourceHash: string;
  old: Record<string, unknown> | null;
  new: Record<string, unknown>;
  renameTo: string | null;
  textDiff: string[];
}

export interface AgentBackupDto {
  file: string;
  path: string;
}

export interface AgentSaveResultDto {
  agent: OpenCodeAgentDto;
  changed: boolean;
  pendingApply: boolean;
  backup: AgentBackupDto | null;
}

export type ApplyMechanismDto = "reload" | "restart-managed" | "restart-manual" | "unavailable";

export interface AgentApplyCapabilityDto {
  mechanism: ApplyMechanismDto;
  reloadAvailable: boolean;
  managedRuntime: boolean;
  canExecute: boolean;
  detail: string;
  confirmRequired: boolean;
}

export interface AgentApplyTargetStatusDto {
  target: string;
  pending: boolean;
  savedHash: string | null;
  appliedHash: string | null;
}

export interface AgentApplyStatusDto {
  target: string;
  pending: boolean;
  savedHash: string | null;
  appliedHash: string | null;
  targets: AgentApplyTargetStatusDto[];
  pendingTargets: string[];
}

export interface AgentApplyResultDto {
  applied: boolean;
  mechanism: "reload" | "restart-managed";
  pending: boolean;
  target: string;
}

export interface AgentAcknowledgeResultDto {
  acknowledged: boolean;
  pending: boolean;
  target: string;
}
