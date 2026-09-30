import { fetchJson, postJson, putJson } from "../../../api/http";
import type { OpenCodeAgentDto, OpenCodeAgentsDto } from "./types";

export async function fetchOpenCodeAgents(): Promise<OpenCodeAgentsDto> {
  return fetchJson<OpenCodeAgentsDto>("/agents/opencode");
}

export async function fetchOpenCodeAgent(
  name: string,
  schemaGeneration?: "v1" | "v2",
): Promise<OpenCodeAgentDto> {
  const suffix = schemaGeneration ? `?schemaGeneration=${schemaGeneration}` : "";
  return fetchJson<OpenCodeAgentDto>(`/agents/opencode/${encodeURIComponent(name)}${suffix}`);
}
import type {
  AgentEditorContextDto,
  AgentFieldsInput,
  AgentPreviewDto,
  AgentSaveResultDto,
} from "./types";

export async function fetchAgentEditorContext(): Promise<AgentEditorContextDto> {
  return fetchJson<AgentEditorContextDto>("/agents/opencode/editor-context");
}

export async function fetchAgentVariantOptions(model: string): Promise<string[]> {
  const payload = await fetchJson<{ options: string[] }>(
    `/agents/opencode/variant-options?model=${encodeURIComponent(model)}`,
  );
  return payload.options;
}

export async function previewAgentCreate(
  schemaGeneration: "v1" | "v2",
  fields: AgentFieldsInput,
): Promise<AgentPreviewDto> {
  return postJson<AgentPreviewDto>("/agents/opencode/preview-create", {
    schemaGeneration,
    fields,
  });
}

export async function previewAgentUpdate(
  name: string,
  schemaGeneration: "v1" | "v2" | null,
  fields: AgentFieldsInput,
): Promise<AgentPreviewDto> {
  const suffix = schemaGeneration ? `?schemaGeneration=${schemaGeneration}` : "";
  return postJson<AgentPreviewDto>(
    `/agents/opencode/preview-update/${encodeURIComponent(name)}${suffix}`,
    { fields },
  );
}

export async function createAgent(
  schemaGeneration: "v1" | "v2",
  fields: AgentFieldsInput,
  expectedSourceHash: string,
): Promise<AgentSaveResultDto> {
  return postJson<AgentSaveResultDto>("/agents/opencode", {
    schemaGeneration,
    fields,
    expectedSourceHash,
  });
}

export async function updateAgent(
  name: string,
  schemaGeneration: "v1" | "v2" | null,
  fields: AgentFieldsInput,
  expectedSourceHash: string,
): Promise<AgentSaveResultDto> {
  const suffix = schemaGeneration ? `?schemaGeneration=${schemaGeneration}` : "";
  return putJson<AgentSaveResultDto>(
    `/agents/opencode/${encodeURIComponent(name)}${suffix}`,
    { fields, expectedSourceHash },
  );
}
import type {
  AgentAcknowledgeResultDto,
  AgentApplyCapabilityDto,
  AgentApplyResultDto,
  AgentApplyStatusDto,
} from "./types";

export async function fetchAgentApplyCapability(): Promise<AgentApplyCapabilityDto> {
  return fetchJson<AgentApplyCapabilityDto>("/agents/opencode/apply-capability");
}

export async function fetchAgentApplyStatus(): Promise<AgentApplyStatusDto> {
  return fetchJson<AgentApplyStatusDto>("/agents/opencode/apply-status");
}

export async function applyAgentConfig(confirm: boolean): Promise<AgentApplyResultDto> {
  return postJson<AgentApplyResultDto>("/agents/opencode/apply", { confirm });
}

export async function acknowledgeManualApply(confirm: boolean): Promise<AgentAcknowledgeResultDto> {
  return postJson<AgentAcknowledgeResultDto>("/agents/opencode/apply/acknowledge-manual", {
    confirm,
  });
}
import type { ModelCatalogueDto, PermissionActionsDto } from "./types";

export async function fetchModelCatalogue(): Promise<ModelCatalogueDto> {
  return fetchJson<ModelCatalogueDto>("/agents/opencode/model-catalogue");
}

export async function fetchPermissionActions(): Promise<PermissionActionsDto> {
  return fetchJson<PermissionActionsDto>("/agents/opencode/permission-actions");
}
