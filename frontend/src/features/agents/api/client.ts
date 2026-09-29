import { fetchJson } from "../../../api/http";
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
