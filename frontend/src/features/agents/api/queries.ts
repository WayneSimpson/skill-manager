import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { queryPolicy } from "../../../lib/query";
import {
  acknowledgeManualApply,
  applyAgentConfig,
  createAgent,
  fetchAgentApplyCapability,
  fetchAgentApplyStatus,
  fetchAgentEditorContext,
  fetchOpenCodeAgents,
  previewAgentCreate,
  previewAgentUpdate,
  updateAgent,
} from "./client";
import { AGENTS_GC_TIME_MS, AGENTS_STALE_TIME_MS, agentKeys } from "./keys";
import type { AgentFieldsInput } from "./types";

export { agentKeys } from "./keys";

export function useOpenCodeAgentsQuery() {
  return useQuery({
    queryKey: agentKeys.opencode(),
    queryFn: fetchOpenCodeAgents,
    ...queryPolicy(AGENTS_STALE_TIME_MS, AGENTS_GC_TIME_MS),
  });
}

export async function invalidateAgentQueries(queryClient: QueryClient): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: agentKeys.all });
}

export function useAgentEditorContextQuery() {
  return useQuery({
    queryKey: agentKeys.editorContext(),
    queryFn: fetchAgentEditorContext,
    ...queryPolicy(AGENTS_STALE_TIME_MS, AGENTS_GC_TIME_MS),
  });
}

export function usePreviewAgentCreateMutation() {
  return useMutation({ mutationFn: ({ generation, fields }: {
    generation: "v1" | "v2"; fields: AgentFieldsInput;
  }) => previewAgentCreate(generation, fields) });
}

export function usePreviewAgentUpdateMutation() {
  return useMutation({ mutationFn: ({ name, generation, fields }: {
    name: string; generation: "v1" | "v2" | null; fields: AgentFieldsInput;
  }) => previewAgentUpdate(name, generation, fields) });
}

export function useCreateAgentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ generation, fields, expectedSourceHash }: {
      generation: "v1" | "v2"; fields: AgentFieldsInput; expectedSourceHash: string;
    }) => createAgent(generation, fields, expectedSourceHash),
    onSuccess: () => void invalidateAgentQueries(queryClient),
  });
}

export function useUpdateAgentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, generation, fields, expectedSourceHash }: {
      name: string; generation: "v1" | "v2" | null;
      fields: AgentFieldsInput; expectedSourceHash: string;
    }) => updateAgent(name, generation, fields, expectedSourceHash),
    onSuccess: () => void invalidateAgentQueries(queryClient),
  });
}

export function useAgentApplyCapabilityQuery() {
  return useQuery({
    queryKey: agentKeys.applyCapability(),
    queryFn: fetchAgentApplyCapability,
    ...queryPolicy(AGENTS_STALE_TIME_MS, AGENTS_GC_TIME_MS),
  });
}

export function useAgentApplyStatusQuery() {
  return useQuery({
    queryKey: agentKeys.applyStatus(),
    queryFn: fetchAgentApplyStatus,
    ...queryPolicy(AGENTS_STALE_TIME_MS, AGENTS_GC_TIME_MS),
  });
}

export function useApplyAgentConfigMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ confirm }: { confirm: boolean }) => applyAgentConfig(confirm),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: agentKeys.applyStatus() });
    },
  });
}

export function useAcknowledgeManualApplyMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ confirm }: { confirm: boolean }) => acknowledgeManualApply(confirm),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: agentKeys.applyStatus() });
    },
  });
}
