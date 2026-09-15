import type {
  BulkManageResult,
  DisableSkillRequest,
  EnableSkillRequest,
  OkResponse,
  SetSkillHarnessesRequest,
  SetSkillHarnessesResultDto,
  SkillDetailDto,
  SkillsPageDto,
  SkillSourceStatusDto,
} from "./types";
import type {
  ManagedPackageResponse,
  ManagedPackagesResponse,
  PackageDeploymentActionRequest,
  PackageDeploymentHarnessResponse,
  PackageDeploymentsResponse,
  SourcePackagesResponse,
  SkillPackageContextResponse,
} from "./package-types";
import { fetchJson, postJson } from "../../../api/http";

export async function fetchSkillsPage(): Promise<SkillsPageDto> {
  return fetchJson<SkillsPageDto>("/skills");
}

export async function fetchSkillDetail(skillRef: string): Promise<SkillDetailDto> {
  return fetchJson<SkillDetailDto>(`/skills/${encodeURIComponent(skillRef)}`);
}

export async function fetchSkillSourceStatus(skillRef: string): Promise<SkillSourceStatusDto> {
  return fetchJson<SkillSourceStatusDto>(`/skills/${encodeURIComponent(skillRef)}/source-status`);
}

export async function enableSkill(skillRef: string, harness: string): Promise<OkResponse> {
  const body: EnableSkillRequest = { harness };
  return postJson<OkResponse>(`/skills/${encodeURIComponent(skillRef)}/enable`, body);
}

export async function disableSkill(skillRef: string, harness: string): Promise<OkResponse> {
  const body: DisableSkillRequest = { harness };
  return postJson<OkResponse>(`/skills/${encodeURIComponent(skillRef)}/disable`, body);
}

export async function setSkillHarnesses(
  skillRef: string,
  target: "enabled" | "disabled",
): Promise<SetSkillHarnessesResultDto> {
  const body: SetSkillHarnessesRequest = { target };
  return postJson<SetSkillHarnessesResultDto>(
    `/skills/${encodeURIComponent(skillRef)}/set-harnesses`,
    body,
  );
}

export async function manageSkill(skillRef: string): Promise<OkResponse> {
  return postJson<OkResponse>(`/skills/${encodeURIComponent(skillRef)}/manage`);
}

export async function manageSourcePackage(skillRef: string): Promise<ManagedPackageResponse> {
  return postJson<ManagedPackageResponse>(`/skills/${encodeURIComponent(skillRef)}/manage-package`);
}

export async function fetchManagedPackages(): Promise<ManagedPackagesResponse> {
  return fetchJson<ManagedPackagesResponse>("/skills/managed-packages");
}

export async function fetchSourcePackages(): Promise<SourcePackagesResponse> {
  return fetchJson<SourcePackagesResponse>("/skills/source-packages");
}

export async function fetchSkillPackageContext(skillRef: string): Promise<SkillPackageContextResponse> {
  return fetchJson<SkillPackageContextResponse>(
    `/skills/${encodeURIComponent(skillRef)}/package-context`,
  );
}

export async function resolveSkillPackage(skillRef: string): Promise<SkillPackageContextResponse> {
  return postJson<SkillPackageContextResponse>(
    `/skills/${encodeURIComponent(skillRef)}/resolve-package`,
  );
}

export async function refreshManagedPackage(packageId: string): Promise<ManagedPackageResponse> {
  return postJson<ManagedPackageResponse>(
    `/skills/managed-packages/${encodeURIComponent(packageId)}/refresh`,
  );
}

export async function fetchPackageDeployments(packageId: string): Promise<PackageDeploymentsResponse> {
  return fetchJson<PackageDeploymentsResponse>(
    `/skills/managed-packages/${encodeURIComponent(packageId)}/deployments`,
  );
}

export async function mutatePackageDeployment(
  packageId: string,
  harness: PackageDeploymentHarnessResponse["harness"],
  action: PackageDeploymentActionRequest["action"],
  replacementPackageId?: string,
): Promise<PackageDeploymentsResponse> {
  const body: PackageDeploymentActionRequest = {
    action,
    ...(replacementPackageId ? { replacementPackageId } : {}),
  };
  return postJson<PackageDeploymentsResponse>(
    `/skills/managed-packages/${encodeURIComponent(packageId)}/deployments/${encodeURIComponent(harness)}`,
    body,
  );
}

export async function updateSkill(skillRef: string): Promise<OkResponse> {
  return postJson<OkResponse>(`/skills/${encodeURIComponent(skillRef)}/update`);
}

export async function unmanageSkill(skillRef: string): Promise<OkResponse> {
  return postJson<OkResponse>(`/skills/${encodeURIComponent(skillRef)}/unmanage`);
}

export async function deleteSkill(skillRef: string): Promise<OkResponse> {
  return postJson<OkResponse>(`/skills/${encodeURIComponent(skillRef)}/delete`);
}

export async function manageAllSkills(): Promise<BulkManageResult> {
  const result = await postJson<BulkManageResult>("/skills/manage-all");
  if (!result.ok) {
    const firstFailure = result.failures[0];
    throw new Error(firstFailure?.error ?? "Unable to manage all eligible skills.");
  }
  return result;
}
