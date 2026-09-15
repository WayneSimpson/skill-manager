import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SkillSourcePackage } from "../../model/types";
import { SkillDetailSourcePackage } from "./SkillDetailSourcePackage";

const packageBase: NonNullable<SkillSourcePackage["package"]> = {
  id: "pkg-trace-lens",
  root: "/workspace/trace-lens",
  name: "trace-lens",
  version: "1.2.0",
  evidence: "declared_standard",
  manifests: [
    { path: "plugin.json", format: "json", evidence: "declared_standard" },
  ],
  components: [],
  diagnostics: [],
  revision: "capability-revision",
};

function renderSourcePackage(sourcePackage: SkillSourcePackage) {
  return render(<SkillDetailSourcePackage sourcePackage={sourcePackage} />);
}

function resolvedPackage(overrides: Partial<NonNullable<SkillSourcePackage["package"]>> = {}): SkillSourcePackage {
  return {
    status: "resolved",
    sourceKind: "local",
    sourcePath: "/workspace/trace-lens/skills/trace-lens",
    sourceRevision: "source-revision",
    reason: null,
    package: { ...packageBase, ...overrides },
  };
}

describe("SkillDetailSourcePackage", () => {
  it("shows the resolved package root separately from the original skill source and explains package limits", () => {
    renderSourcePackage(resolvedPackage({
      components: [
        {
          kind: "skills",
          harness: null,
          path: "skills/trace-lens",
          evidence: "declared_standard",
          manifest: "plugin.json",
          supported: true,
        },
        {
          kind: "mcp",
          harness: null,
          path: "mcp.json",
          evidence: "declared_standard",
          manifest: "mcp.json",
          supported: false,
          entries: ["demo-server"],
        },
      ],
    }));

    expect(screen.getByRole("heading", { level: 3, name: "Source package" })).toBeInTheDocument();
    expect(screen.getByText("Source root")).toBeInTheDocument();
    expect(screen.getByText("/workspace/trace-lens", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("Original skill source")).toBeInTheDocument();
    expect(screen.getByText("/workspace/trace-lens/skills/trace-lens", { exact: true })).toBeInTheDocument();
    expect(screen.getAllByText("Portable/shared")).toHaveLength(2);
    expect(screen.queryByText("Individual skill copying is supported")).not.toBeInTheDocument();
    expect(screen.queryByText("Package deployment is not supported")).not.toBeInTheDocument();
    expect(screen.getByText("demo-server")).toBeInTheDocument();
    expect(screen.getByText(/Package hooks, MCP configuration, and plugin code are not installed by skill toggles/i)).toBeInTheDocument();
  });

  it("distinguishes portable components from harness-specific components in a mixed package", () => {
    renderSourcePackage(resolvedPackage({
      components: [
        {
          kind: "skills",
          harness: null,
          path: "skills/trace-lens",
          evidence: "declared_standard",
          manifest: "plugin.json",
          supported: true,
        },
        {
          kind: "hooks",
          harness: "claude",
          path: "hooks/hooks.json",
          evidence: "verified_convention",
          manifest: ".claude-plugin/plugin.json",
          supported: false,
          entries: ["SessionStart", "<script>not-executed</script>"],
        },
      ],
    }));

    expect(screen.getByText("Portable/shared")).toBeInTheDocument();
    expect(screen.getByText("Harness-specific · Claude")).toBeInTheDocument();
    expect(screen.getByText("SessionStart")).toBeInTheDocument();
    expect(screen.getByText("<script>not-executed</script>")).toBeInTheDocument();
  });

  it("does not call an unknown harness-null extension portable", () => {
    renderSourcePackage(resolvedPackage({
      components: [
        {
          kind: "extensions",
          harness: null,
          path: "plugin.json#extensions.custom",
          evidence: "unresolved",
          manifest: "plugin.json",
          supported: false,
        },
      ],
    }));

    expect(screen.getByText("Unknown extension · not portable")).toBeInTheDocument();
    expect(screen.queryByText("Portable/shared")).not.toBeInTheDocument();
    expect(screen.queryByText("Package deployment is not supported")).not.toBeInTheDocument();
  });

  it("explains an unresolved package without suggesting the skill itself cannot be used", () => {
    renderSourcePackage({
      status: "unresolved",
      sourceKind: "github",
      sourcePath: "/workspace/trace-lens/skills/trace-lens",
      sourceRevision: "source-revision",
      reason: "Repository-relative skill provenance has no retained local source checkout.",
      package: null,
    });

    expect(screen.getByText("Source package could not be resolved")).toBeInTheDocument();
    expect(screen.getByText("Repository-relative skill provenance has no retained local source checkout.")).toBeInTheDocument();
    expect(screen.getByText(/The skill itself can still be used normally/i)).toBeInTheDocument();
    expect(screen.getByText("/workspace/trace-lens/skills/trace-lens", { exact: true })).toBeInTheDocument();
    expect(screen.queryByText("Source root")).not.toBeInTheDocument();
  });
});
