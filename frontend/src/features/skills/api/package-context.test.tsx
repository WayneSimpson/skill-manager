import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { fetchSkillPackageContext, resolveSkillPackage } from "./client";
import type { SkillPackageContextResponse } from "./package-types";
import { useResolveSkillPackageMutation, useSkillPackageContextQuery } from "./queries";

vi.mock("./client", () => ({
  fetchSkillPackageContext: vi.fn(),
  resolveSkillPackage: vi.fn(),
}));

const fetchSkillPackageContextMock = vi.mocked(fetchSkillPackageContext);
const resolveSkillPackageMock = vi.mocked(resolveSkillPackage);

const standaloneContext: SkillPackageContextResponse = {
  packageBacked: false,
  observation: {
    status: "unresolved",
    sourceKind: "local",
    sourcePath: "/workspace/standalone/SKILL.md",
    sourceRevision: "standalone-revision",
    reason: "No package manifest was found.",
    package: null,
  },
  resolution: null,
  managedPackage: null,
};

const packageContext: SkillPackageContextResponse = {
  packageBacked: true,
  observation: {
    status: "resolved",
    sourceKind: "local",
    sourcePath: "/workspace/trace-lens/skills/trace-lens",
    sourceRevision: "observed-revision",
    reason: null,
    package: {
      id: "observed-package",
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
  resolution: {
    status: "resolved",
    source: {
      kind: "github",
      locator: "mode-io/trace-lens",
      version: null,
      ref: "main",
      revision: "source-revision",
      package_path: null,
      skill_path: "skills/trace-lens",
      artifact_url: null,
      integrity: null,
    },
    reason: null,
    limitations: [],
    evidence: [],
  },
  managedPackage: null,
};

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      {children}
    </QueryClientProvider>
  );
}

describe("useSkillPackageContextQuery", () => {
  it("uses the server package-context response without treating a standalone skill as package-backed", async () => {
    fetchSkillPackageContextMock.mockResolvedValue(standaloneContext);

    const { result } = renderHook(() => useSkillPackageContextQuery("unmanaged:standalone"), { wrapper });

    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.packageBacked).toBe(false);
    expect(result.current.data?.observation?.reason).toBe("No package manifest was found.");
    expect(fetchSkillPackageContextMock).toHaveBeenCalledWith("unmanaged:standalone");
  });

  it("keeps observed package and authoritative source facts distinct", async () => {
    fetchSkillPackageContextMock.mockResolvedValue(packageContext);

    const { result } = renderHook(() => useSkillPackageContextQuery("unmanaged:trace-lens"), { wrapper });

    await waitFor(() => expect(result.current.data?.packageBacked).toBe(true));
    expect(result.current.data?.observation?.package?.id).toBe("observed-package");
    expect(result.current.data?.resolution?.source?.locator).toBe("mode-io/trace-lens");
    expect(result.current.data?.resolution?.source?.revision).toBe("source-revision");
  });
});

describe("useResolveSkillPackageMutation", () => {
  it("stores explicit resolution review data in the skill context cache", async () => {
    resolveSkillPackageMock.mockResolvedValue(packageContext);
    const queryClient = new QueryClient();
    const testWrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useResolveSkillPackageMutation(), { wrapper: testWrapper });

    await result.current.mutateAsync({ skillRef: "unmanaged:trace-lens" });

    expect(queryClient.getQueryData(["skills", "package-context", "unmanaged:trace-lens"])).toEqual(packageContext);
  });
});
