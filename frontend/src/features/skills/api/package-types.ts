import type { components } from "../../../api/generated";

export type PackageSourceResponse = components["schemas"]["PackageSource"];
export type SourcePackageResponse = components["schemas"]["SourcePackageResponse"];
export type SkillSourcePackageResponse = components["schemas"]["SkillSourcePackageResponse"];
export type SourceEvidence = components["schemas"]["SourceEvidence"];
export type ManagedPackageResponse = components["schemas"]["ManagedPackageResponse"];
export type ManagedPackageSkillLink = components["schemas"]["ManagedPackageSkillLink"];
export type ManagedPackagesResponse = components["schemas"]["ManagedPackagesResponse"];
export type SourcePackagesResponse = components["schemas"]["SourcePackagesResponse"];
export type PackageDeploymentActionRequest = components["schemas"]["PackageDeploymentActionRequest"];

export type PackageReplacementOption = components["schemas"]["PackageReplacementOption"];
export type PackageDeploymentHarnessResponse = components["schemas"]["PackageDeploymentHarnessResponse"];
export type PackageDeploymentsResponse = components["schemas"]["PackageDeploymentsResponse"];

export type PackageResolutionStatus = "resolved" | "unresolved" | "ambiguous" | "unavailable";

export type PackageResolutionResponse = components["schemas"]["PackageResolutionResponse"];
export type SkillPackageContextResponse = components["schemas"]["SkillPackageContextResponse"];
