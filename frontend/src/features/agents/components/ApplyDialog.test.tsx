import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LocaleProvider } from "../../../i18n";
import { ApplyDialog } from "./ApplyDialog";
import type { AgentApplyCapabilityDto } from "../api/types";

vi.mock("../api/queries", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/queries")>();
  const idle = () => ({ mutateAsync: vi.fn(), isPending: false });
  return {
    ...actual,
    useApplyAgentConfigMutation: vi.fn(idle),
    useAcknowledgeManualApplyMutation: vi.fn(idle),
  };
});

import {
  useAcknowledgeManualApplyMutation,
  useApplyAgentConfigMutation,
} from "../api/queries";

function capability(
  mechanism: AgentApplyCapabilityDto["mechanism"],
  canExecute = mechanism === "reload" || mechanism === "restart-managed",
): AgentApplyCapabilityDto {
  return {
    mechanism,
    reloadAvailable: mechanism === "reload",
    managedRuntime: mechanism === "restart-managed",
    canExecute,
    detail: "probe detail",
    confirmRequired: true,
  };
}

function renderDialog(cap: AgentApplyCapabilityDto | undefined) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <LocaleProvider>
        <ApplyDialog open capability={cap} onOpenChange={() => undefined} />
      </LocaleProvider>
    </QueryClientProvider>,
  );
}

describe("ApplyDialog", () => {
  it("reload mechanism shows a clear warning and a confirming apply action", () => {
    const apply = { mutateAsync: vi.fn().mockResolvedValue({}), isPending: false };
    vi.mocked(useApplyAgentConfigMutation).mockReturnValue(apply as never);

    renderDialog(capability("reload"));

    expect(screen.getByText(/Confirm to reload now/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Apply now/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Apply now/ }));
    expect(apply.mutateAsync).toHaveBeenCalledWith({ confirm: true });
  });

  it("managed restart shows the stronger interruption warning", () => {
    const apply = { mutateAsync: vi.fn().mockResolvedValue({}), isPending: false };
    vi.mocked(useApplyAgentConfigMutation).mockReturnValue(apply as never);

    renderDialog(capability("restart-managed"));

    expect(screen.getByText(/sessions on the managed runtime will be interrupted/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Apply now/ })).toBeInTheDocument();
  });

  it("manual restart explains the situation without a fake executable action", () => {
    const apply = { mutateAsync: vi.fn(), isPending: false };
    const acknowledge = { mutateAsync: vi.fn().mockResolvedValue({}), isPending: false };
    vi.mocked(useApplyAgentConfigMutation).mockReturnValue(apply as never);
    vi.mocked(useAcknowledgeManualApplyMutation).mockReturnValue(acknowledge as never);

    renderDialog(capability("restart-manual"));

    expect(screen.getByText(/Restart OpenCode yourself/)).toBeInTheDocument();
    // No executable apply button is presented for a manual mechanism.
    expect(screen.queryByRole("button", { name: /Apply now/ })).not.toBeInTheDocument();

    // Acknowledgement requires the explicit checkbox first.
    const acknowledgeButton = screen.getByRole("button", { name: /I have restarted OpenCode/ });
    expect(acknowledgeButton).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(acknowledgeButton).toBeEnabled();
    fireEvent.click(acknowledgeButton);
    expect(acknowledge.mutateAsync).toHaveBeenCalledWith({ confirm: true });
    expect(apply.mutateAsync).not.toHaveBeenCalled();
  });

  it("surfaces apply failures without closing", async () => {
    vi.mocked(useApplyAgentConfigMutation).mockReturnValue({
      mutateAsync: vi.fn().mockRejectedValue(new Error("boom")),
      isPending: false,
    } as never);

    renderDialog(capability("reload"));
    fireEvent.click(screen.getByRole("button", { name: /Apply now/ }));

    expect(await screen.findByText("boom")).toBeInTheDocument();
  });

  it("hides the executable apply action when capability says canExecute=false for reload", () => {
    renderDialog(capability("reload", false));

    expect(
      screen.getByText(/not configured to execute it safely/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Apply now/ })).not.toBeInTheDocument();
  });
});
