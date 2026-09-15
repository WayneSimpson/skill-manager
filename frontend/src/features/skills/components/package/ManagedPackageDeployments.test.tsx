import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PackageDeploymentsResponse } from "../../api/package-types";
import { usePackageDeploymentMutation, usePackageDeploymentsQuery } from "../../api/queries";
import { ManagedPackageDeployments } from "./ManagedPackageDeployments";

vi.mock("../../api/queries", () => ({
  usePackageDeploymentMutation: vi.fn(),
  usePackageDeploymentsQuery: vi.fn(),
}));

const usePackageDeploymentsQueryMock = vi.mocked(usePackageDeploymentsQuery);
const usePackageDeploymentMutationMock = vi.mocked(usePackageDeploymentMutation);

const packageId = "a".repeat(64);
const replacementPackageId = "b".repeat(64);
const deployments: PackageDeploymentsResponse = {
  packageId,
  harnesses: [
    {
      harness: "claude",
      state: "enabled",
      support: "supported",
      ownership: "managed",
      strategy: "native-local",
      selectedPackageId: packageId,
      deploymentId: "deployment-claude",
      enabled: true,
      blockers: [],
      actions: ["disable", "remove"],
      preflight: ["Fresh native inventory and deployment checks passed."],
    },
    {
      harness: "opencode",
      state: "external-existing",
      support: "manual",
      ownership: "external-existing",
      strategy: "manual/unsupported",
      selectedPackageId: null,
      deploymentId: null,
      enabled: null,
      blockers: ["external-existing-no-takeover"],
      actions: [],
      preflight: ["Manual inspection is required."],
    },
  ],
};

describe("ManagedPackageDeployments", () => {
  beforeEach(() => {
    const refetch = vi.fn().mockResolvedValue({ data: deployments, error: null });
    usePackageDeploymentsQueryMock.mockReturnValue({
      data: deployments,
      error: null,
      isPending: false,
      isFetching: false,
      refetch,
    } as unknown as ReturnType<typeof usePackageDeploymentsQuery>);
    usePackageDeploymentMutationMock.mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue(deployments),
      isPending: false,
      error: null,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof usePackageDeploymentMutation>);
  });

  it("shows backend deployment state and does not offer takeover for an external install", () => {
    render(<ManagedPackageDeployments packageId={packageId} />);

    expect(screen.getByText("Claude")).toBeInTheDocument();
    expect(screen.getByText("Enabled")).toBeInTheDocument();
    expect(screen.getByText("Already installed externally")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Deploy package" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Update deployment" })).not.toBeInTheDocument();
  });

  it("confirms removal and sends only the backend-owned package identifiers", async () => {
    const mutateAsync = vi.fn().mockResolvedValue(deployments);
    usePackageDeploymentMutationMock.mockReturnValue({
      mutateAsync,
      isPending: false,
      error: null,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof usePackageDeploymentMutation>);

    render(<ManagedPackageDeployments packageId={packageId} />);

    fireEvent.click(screen.getByRole("button", { name: "Remove deployment" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({
      packageId,
      harness: "claude",
      action: "remove",
      replacementPackageId: undefined,
    }));
  });

  it("uses the managed package source in confirmation when the deployment row has no source coordinate", () => {
    render(
      <ManagedPackageDeployments
        packageId={packageId}
        packageSource={{
          kind: "github",
          locator: "mode-io/trace-lens",
          version: "1.2.0",
          ref: "main",
          revision: "source-revision",
          package_path: null,
          skill_path: "skills/trace-lens",
          integrity: null,
          artifact_url: null,
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Disable package" }));

    expect(screen.getByText("github · mode-io/trace-lens · v1.2.0 · ref main · source-revision")).toBeInTheDocument();
  });

  it("disables deployment controls while facts are refetching or unavailable", () => {
    const cases = [
      { isFetching: true, error: null },
      { isFetching: false, error: new Error("facts unavailable") },
    ];

    for (const state of cases) {
      usePackageDeploymentsQueryMock.mockReturnValue({
        data: deployments,
        error: state.error,
        isPending: false,
        isFetching: state.isFetching,
        refetch: vi.fn(),
      } as unknown as ReturnType<typeof usePackageDeploymentsQuery>);

      const { unmount } = render(<ManagedPackageDeployments packageId={packageId} />);
      expect(screen.getByRole("button", { name: "Disable package" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Remove deployment" })).toBeDisabled();
      unmount();
    }
  });

  it("requires an explicitly selected backend-proven replacement snapshot", async () => {
    const replacementSource = {
      kind: "github" as const,
      locator: "mode-io/trace-lens",
      version: "1.3.0",
      ref: "main",
      revision: "replacement-revision",
      package_path: null,
      skill_path: "skills/trace-lens",
      integrity: null,
      artifact_url: null,
    };
    const deploymentsWithReplacement = {
      ...deployments,
      harnesses: deployments.harnesses.map((deployment) => deployment.harness === "claude"
        ? {
            ...deployment,
            actions: [...deployment.actions, "update" as const],
            replacementOptions: [{ packageId: replacementPackageId, label: "Trace Lens 1.3.0", source: replacementSource }],
          }
        : deployment),
    };
    usePackageDeploymentsQueryMock.mockReturnValue({
      data: deploymentsWithReplacement,
      error: null,
      isPending: false,
      isFetching: false,
      refetch: vi.fn().mockResolvedValue({ data: deploymentsWithReplacement, error: null }),
    } as unknown as ReturnType<typeof usePackageDeploymentsQuery>);
    const mutateAsync = vi.fn().mockResolvedValue(deploymentsWithReplacement);
    usePackageDeploymentMutationMock.mockReturnValue({
      mutateAsync,
      isPending: false,
      error: null,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof usePackageDeploymentMutation>);

    render(<ManagedPackageDeployments packageId={packageId} />);
    fireEvent.click(screen.getByRole("button", { name: "Update deployment" }));
    expect(screen.getByRole("combobox")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Confirm" })).toBeDisabled();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: replacementPackageId } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({
      packageId,
      harness: "claude",
      action: "update",
      replacementPackageId,
    }));
  });

  it("explains that disable keeps central package management intact", () => {
    render(<ManagedPackageDeployments packageId={packageId} />);

    fireEvent.click(screen.getByRole("button", { name: "Disable package" }));

    expect(screen.getByText("Removing or disabling this harness deployment keeps the central package managed.")).toBeInTheDocument();
  });

  it('returns keyboard focus to the action after closing confirmation', async () => {
    render(<ManagedPackageDeployments packageId={packageId} />);
    const trigger = screen.getByRole('button', {name: 'Disable package'});
    fireEvent.click(trigger);
    fireEvent.keyDown(screen.getByRole('dialog'), {key: 'Escape'});
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it("shows changed-preflight errors inside the confirmation without posting a mutation", async () => {
    const blocked = { ...deployments, harnesses: deployments.harnesses.map((row) => ({...row, actions: []})) };
    usePackageDeploymentsQueryMock.mockReturnValue({
      data: deployments, error: null, isPending: false, isFetching: false,
      refetch: vi.fn().mockResolvedValue({ data: blocked, error: null }),
    } as unknown as ReturnType<typeof usePackageDeploymentsQuery>);
    const mutateAsync = vi.fn();
    usePackageDeploymentMutationMock.mockReturnValue({
      mutateAsync, isPending: false, error: null, reset: vi.fn(),
    } as unknown as ReturnType<typeof usePackageDeploymentMutation>);
    render(<ManagedPackageDeployments packageId={packageId} />);
    fireEvent.click(screen.getByRole('button', {name: 'Disable package'}));
    fireEvent.click(screen.getByRole('button', {name: 'Confirm'}));
    await waitFor(() => expect(within(screen.getByRole('dialog')).getByText(
      'This action is no longer allowed by the latest deployment facts.',
    )).toBeVisible());
    expect(mutateAsync).not.toHaveBeenCalled();
  });
});
