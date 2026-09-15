import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithAppProviders } from "../../../../test/render";
import type { SkillPackageContextResponse } from "../../api/package-types";
import type { SkillListRow } from "../../model/types";
import { SkillNeedsReviewCard } from "./SkillNeedsReviewCard";

const packageContextMock = vi.hoisted(() => ({
  data: null as SkillPackageContextResponse | null,
  isPending: false,
  isError: false,
  error: null as Error | null,
}));

vi.mock("../../api/queries", () => ({
  useSkillPackageContextQuery: () => packageContextMock,
}));

const row: SkillListRow = {
  skillRef: "unmanaged:trace-lens",
  name: "Trace Lens",
  description: "Package-backed skill",
  displayStatus: "Unmanaged",
  actions: { canManage: true, canStopManaging: false, canDelete: false },
  cells: [{ harness: "claude", label: "Claude", state: "found", interactive: false }],
};

const observedPackageContext: SkillPackageContextResponse = {
  packageBacked: true,
  observation: {
    status: "resolved",
    sourceKind: "local",
    sourcePath: "/workspace/trace-lens/skills/trace-lens",
    sourceRevision: "source-revision",
    reason: null,
    package: {
      id: "local-package-identity",
      root: "/workspace/trace-lens",
      name: "trace-lens",
      version: "1.2.0",
      evidence: "declared_standard",
      manifests: [{ path: "plugin.json", format: "json", evidence: "declared_standard" }],
      components: [],
      diagnostics: [],
      revision: "capability-revision",
    },
  },
  resolution: null,
  managedPackage: null,
};

describe("SkillNeedsReviewCard package actions", () => {
  it("routes an observed but unresolved package to package source review", () => {
    packageContextMock.data = observedPackageContext;
    const onManageSkill = vi.fn(async () => undefined);
    const onManagePackage = vi.fn(async () => undefined);
    const onOpenSkill = vi.fn();

    renderWithAppProviders(
      <SkillNeedsReviewCard
        row={row}
        pendingStructuralAction={null}
        bulkActionPending={false}
        selected={false}
        onOpenSkill={onOpenSkill}
        onManageSkill={onManageSkill}
        onManagePackage={onManagePackage}
      />,
    );

    const button = screen.getByRole("button", { name: "Review package source" });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(onOpenSkill).toHaveBeenCalledWith(row.skillRef);
    expect(onManagePackage).not.toHaveBeenCalled();
    expect(onManageSkill).not.toHaveBeenCalled();
  });

  it("offers whole-package management after authoritative resolution without a local manifest", () => {
    packageContextMock.data = {
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
        source: {
          kind: "github",
          locator: "mode-io/trace-lens",
          version: "1.2.0",
          ref: null,
          revision: "source-revision",
          package_path: null,
        },
        reason: null,
        limitations: [],
        evidence: [],
      },
      managedPackage: null,
    };
    const onManageSkill = vi.fn(async () => undefined);
    const onManagePackage = vi.fn(async () => undefined);

    renderWithAppProviders(
      <SkillNeedsReviewCard
        row={row}
        pendingStructuralAction={null}
        bulkActionPending={false}
        selected={false}
        onOpenSkill={vi.fn()}
        onManageSkill={onManageSkill}
        onManagePackage={onManagePackage}
      />,
    );

    const button = screen.getByRole("button", { name: "Manage package" });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(onManagePackage).toHaveBeenCalledWith(row.skillRef);
    expect(onManageSkill).not.toHaveBeenCalled();
  });

  it("opens package details for a package relationship without a complete local package", () => {
    packageContextMock.data = {
      packageBacked: true,
      observation: null,
      resolution: {
        status: "unavailable",
        source: null,
        reason: "The complete package source is unavailable.",
        limitations: [],
        evidence: [],
      },
      managedPackage: null,
    };
    const onOpenSkill = vi.fn();
    const onManageSkill = vi.fn(async () => undefined);

    render(
      <SkillNeedsReviewCard
        row={row}
        pendingStructuralAction={null}
        bulkActionPending={false}
        selected={false}
        onOpenSkill={onOpenSkill}
        onManageSkill={onManageSkill}
      />,
    );

    const button = screen.getByRole("button", { name: "Review package source" });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(onOpenSkill).toHaveBeenCalledWith(row.skillRef);
    expect(onManageSkill).not.toHaveBeenCalled();
  });
});
