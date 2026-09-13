import { fetchJson, postJson, putJson } from "../../../api/http";
import type {
  OpenCodeRuntimeSkillsRefreshRequest,
  OpenCodeRuntimeSkillsStatus,
  SetHarnessSupportRequest,
  SettingsData,
} from "./types";

export async function fetchSettings(): Promise<SettingsData> {
  return fetchJson<SettingsData>("/settings");
}

export async function updateHarnessSupport(harness: string, enabled: boolean): Promise<{ ok: boolean; enabled: boolean }> {
  const body: SetHarnessSupportRequest = { enabled };
  return putJson<{ ok: boolean; enabled: boolean }>(
    `/settings/harnesses/${encodeURIComponent(harness)}/support`,
    body,
  );
}

export async function fetchOpenCodeRuntimeSkillsStatus(): Promise<OpenCodeRuntimeSkillsStatus> {
  return fetchJson<OpenCodeRuntimeSkillsStatus>("/opencode/runtime-skills/status");
}

export async function refreshOpenCodeRuntimeSkills(
  body: OpenCodeRuntimeSkillsRefreshRequest,
): Promise<OpenCodeRuntimeSkillsStatus> {
  return postJson<OpenCodeRuntimeSkillsStatus>("/opencode/runtime-skills/refresh", body);
}

export async function disconnectOpenCodeRuntimeSkills(): Promise<OpenCodeRuntimeSkillsStatus> {
  return postJson<OpenCodeRuntimeSkillsStatus>("/opencode/runtime-skills/disconnect");
}
