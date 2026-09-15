import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  ManagedPackageResponse,
  SkillPackageContextResponse,
} from "../../api/package-types";
import { PackageAwareSkillPanel } from "./PackageAwareSkillPanel";

const observedPackage = {
  id: "local-package-identity",
  root: "/workspace/trace-lens",
  name: "trace-lens",
  version: "1.2.0",
  evidence: "declared_standard",
  manifests: [
    { path: "plugin.json", format: "json", evidence: "declared_standard" },
  ],
  components: [
    {
      kind: "skills",
      harness: null,
      path: "skills/trace-lens",
      evidence: "declared_standard",
      manifest: "plugin.json",
      supported: true,
      entries: [],
    },
  ],
  diagnostics: [],
  revision: "capability-revision",
} satisfies NonNullable<NonNullable<SkillPackageContextResponse["observation"]>["package"]>;

const source = {
  kind: "github",
  locator: "mode-io/trace-lens",
  version: "1.2.0",
  ref: "main",
  revision: "source-revision",
  package_path: "",
  skill_path: "skills/trace-lens",
  artifact_url: null,
  integrity: null,
} satisfies ManagedPackageResponse["source"];

const managedPackage = {
  id: "managed-package-identity",
  ownership: "skill-manager",
  source,
  fingerprint: "package-fingerprint",
  capabilities: {
    name: "trace-lens",
    version: "1.2.0",
    evidence: "declared_standard",
    manifests: observedPackage.manifests,
    components: [...observedPackage.components, {
      kind: 'hooks', harness: 'claude', path: 'hooks/hooks.json',
      evidence: 'declared_harness_manifest', manifest: '.claude-plugin/plugin.json',
      supported: false, entries: ['SessionStart'],
    }],
    diagnostics: [],
    revision: "capability-revision",
  },
  evidence: [],
  distributions: [{
    source,
    relationship: "explicit native distribution",
    evidence: "native manifest",
    harness: "claude",
  }],
  limitations: ["Native deployment is not exposed without backend support facts."],
  resolutionStatus: "resolved",
  upstreamState: "current",
  candidateSource: null,
  reason: null,
  artifactRoot: "/workspace/.skill-manager/packages/trace-lens",
  artifactState: "current",
  state: "current",
  skillRefs: ["unmanaged:trace-lens"],
  observations: [],
} satisfies ManagedPackageResponse;

const observedContext: SkillPackageContextResponse = {
  packageBacked: true,
  observation: {
    status: "resolved",
    sourceKind: "local",
    sourcePath: "/workspace/trace-lens/skills/trace-lens",
    sourceRevision: "source-revision",
    reason: null,
    package: observedPackage,
  },
  resolution: null,
  managedPackage: null,
};

describe("PackageAwareSkillPanel", () => {
  it("routes an observed but unresolved package to explicit source review", () => {
    const onManagePackage = vi.fn();
    const onResolvePackage = vi.fn();

    render(
      <PackageAwareSkillPanel
        context={observedContext}
        pendingStructuralAction={null}
        onManagePackage={onManagePackage}
        onResolvePackage={onResolvePackage}
      />,
    );

    expect(screen.getAllByText("Source is observed locally; upstream resolution starts when the whole package is managed.")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Resolve source" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Manage package" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Resolve source" }));
    expect(onResolvePackage).toHaveBeenCalledOnce();
    expect(onManagePackage).not.toHaveBeenCalled();
  });

  it("allows package management from an authoritative source without a local package manifest", () => {
    const context: SkillPackageContextResponse = {
      packageBacked: true,
      observation: {
        status: "unresolved",
        sourceKind: "native",
        sourcePath: "/workspace/trace-lens/skills/trace-lens",
        sourceRevision: "observed-revision",
        reason: "The local manifest is unavailable.",
        package: null,
      },
      resolution: {
        status: "resolved",
        source,
        reason: null,
        limitations: [],
        evidence: [],
      },
      managedPackage: null,
    };
    const onManagePackage = vi.fn();
    const onResolvePackage = vi.fn();

    render(
      <PackageAwareSkillPanel
        context={context}
        pendingStructuralAction={null}
        onManagePackage={onManagePackage}
        onResolvePackage={onResolvePackage}
      />,
    );

    expect(screen.getByText("An authoritative package source is resolved; the whole package is ready for central management.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Manage package" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Resolve source" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Manage package" }));
    expect(onManagePackage).toHaveBeenCalledOnce();
  });

  it("keeps unresolved managed packages blocked from management while allowing source review", () => {
    const context: SkillPackageContextResponse = {
      ...observedContext,
      resolution: {
        status: "unavailable",
        source: null,
        reason: "The source is unavailable offline.",
        limitations: [],
        evidence: [],
      },
      managedPackage: { ...managedPackage, resolutionStatus: "unavailable", state: "unavailable" },
    };

    render(
      <PackageAwareSkillPanel
        context={context}
        pendingStructuralAction={null}
        onManagePackage={vi.fn()}
        onResolvePackage={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: "Manage package" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resolve source" })).toBeEnabled();
    expect(screen.queryByText("An authoritative package source is resolved; the whole package is ready for central management.")).not.toBeInTheDocument();
  });

  it("disables the package action while either structural package action is pending", () => {
    const unresolved = render(
      <PackageAwareSkillPanel
        context={observedContext}
        pendingStructuralAction="resolve"
        onManagePackage={vi.fn()}
        onResolvePackage={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /Resolving source/ })).toBeDisabled();
    unresolved.unmount();

    render(
      <PackageAwareSkillPanel
        context={{ ...observedContext, resolution: { status: "resolved", source, reason: null, limitations: [], evidence: [] } }}
        pendingStructuralAction="manage"
        onManagePackage={vi.fn()}
        onResolvePackage={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /Managing package/ })).toBeDisabled();
  });

  it("shows the centrally managed package and its retained limitations without native controls", () => {
    const context: SkillPackageContextResponse = {
      ...observedContext,
      resolution: {
        status: "resolved",
        source,
        reason: null,
        limitations: managedPackage.limitations,
        evidence: [],
      },
      managedPackage,
    };

    render(
      <PackageAwareSkillPanel
        context={context}
        pendingStructuralAction={null}
        onManagePackage={vi.fn()}
        onResolvePackage={vi.fn()}
      />,
    );

    expect(screen.getByText("Skill Manager owns this whole package; individual package components are not separately deployed.")).toBeInTheDocument();
    expect(screen.getAllByText("Native deployment is not exposed without backend support facts.")).toHaveLength(1);
    expect(screen.getByText("package-fingerprint")).toBeInTheDocument();
    expect(screen.getByText("explicit native distribution")).toBeInTheDocument();
    expect(screen.getByText('hooks · claude')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Manage package" })).not.toBeInTheDocument();
    expect(screen.getAllByText("trace-lens")).toHaveLength(2);
  });
});
