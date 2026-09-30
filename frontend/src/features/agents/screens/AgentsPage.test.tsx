import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { LocaleProvider } from "../../../i18n";
import AgentsPage from "./AgentsPage";
import type { OpenCodeAgentsDto } from "../api/types";

vi.mock("../api/queries", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/queries")>();
  return {
    ...actual,
    useOpenCodeAgentsQuery: vi.fn(),
    useAgentEditorContextQuery: vi.fn(),
    useAgentApplyCapabilityQuery: vi.fn(),
    useAgentApplyStatusQuery: vi.fn(),
  };
});

import {
  useAgentApplyCapabilityQuery,
  useAgentApplyStatusQuery,
  useAgentEditorContextQuery,
  useOpenCodeAgentsQuery,
} from "../api/queries";

const fixture: OpenCodeAgentsDto = {
  agents: [
    {
      name: "legacy-agent",
      schemaGeneration: "v1",
      description: "Declared in the legacy config file",
      instructions: null,
      prompt: null,
      model: null,
      variant: null,
      modelRaw: null,
      mode: "all",
      temperature: null,
      topP: null,
      steps: null,
      disabled: null,
      hidden: null,
      color: null,
      permissions: null,
      permission: null,
      tools: null,
      additionalOptions: {},
      source: {
        file: "legacy",
        path: "/tmp/home/.opencode/opencode.jsonc",
        format: "jsonc",
        isWriteTarget: false,
      },
      editability: "config",
      readOnlyReasons: [],
      readOnly: false,
      valid: true,
      diagnostic: null,
    },
    {
      name: "reviewer",
      schemaGeneration: "v1",
      description: "Reviews code",
      instructions: "You review code.",
      prompt: "You review code.",
      model: "anthropic/claude-sonnet-4",
      variant: "high",
      modelRaw: "anthropic/claude-sonnet-4",
      mode: "subagent",
      temperature: 0.2,
      topP: null,
      steps: 8,
      disabled: null,
      hidden: false,
      color: null,
      permissions: { edit: "deny" },
      permission: { edit: "deny" },
      tools: null,
      additionalOptions: { variant: "high" },
      source: {
        file: "xdg-jsonc",
        path: "/tmp/home/.config/opencode/opencode.jsonc",
        format: "jsonc",
        isWriteTarget: true,
      },
      editability: "config",
      readOnlyReasons: [],
      readOnly: false,
      valid: true,
      diagnostic: null,
    },
    {
      name: "v2-writer",
      schemaGeneration: "v2",
      description: "Writes documentation",
      instructions: "Write docs.",
      prompt: "Write docs.",
      model: "anthropic/claude-sonnet-4-5",
      variant: "high",
      modelRaw: "anthropic/claude-sonnet-4-5#high",
      mode: "subagent",
      temperature: null,
      topP: null,
      steps: null,
      disabled: true,
      hidden: null,
      color: null,
      permissions: [{ action: "edit", resource: "*", effect: "deny" }],
      permission: [{ action: "edit", resource: "*", effect: "deny" }],
      tools: null,
      additionalOptions: {},
      source: {
        file: "xdg-jsonc",
        path: "/tmp/home/.config/opencode/opencode.jsonc",
        format: "jsonc",
        isWriteTarget: true,
      },
      editability: "config",
      readOnlyReasons: [],
      readOnly: false,
      valid: true,
      diagnostic: null,
    },
    {
      name: "broken",
      schemaGeneration: "v1",
      description: null,
      instructions: null,
      prompt: null,
      model: null,
      variant: null,
      modelRaw: null,
      mode: null,
      temperature: null,
      topP: null,
      steps: null,
      disabled: null,
      hidden: null,
      color: null,
      permissions: null,
      permission: null,
      tools: null,
      additionalOptions: {},
      source: null,
      editability: "read-only",
      readOnlyReasons: ["definition-not-an-object"],
      readOnly: true,
      valid: false,
      diagnostic: "Agent definition is not an object",
    },
  ],
  writeTarget: "/tmp/home/.config/opencode/opencode.jsonc",
  sources: [
    {
      name: "xdg-jsonc",
      path: "/tmp/home/.config/opencode/opencode.jsonc",
      format: "jsonc",
      precedence: 2,
      status: "loaded",
      diagnostic: null,
    },
  ],
  diagnostics: [],
  limitation: "Static compatibility only.",
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <LocaleProvider>
        <MemoryRouter>
          <AgentsPage />
        </MemoryRouter>
      </LocaleProvider>
    </QueryClientProvider>,
  );
}

function stubApplyState(pending = false) {
  vi.mocked(useAgentApplyStatusQuery).mockReturnValue({
    data: {
      target: "/tmp/config/opencode/opencode.jsonc",
      pending,
      savedHash: pending ? "hash-saved" : null,
      appliedHash: pending ? "hash-applied" : null,
    },
    isPending: false,
    error: null,
  } as unknown as ReturnType<typeof useAgentApplyStatusQuery>);
  vi.mocked(useAgentApplyCapabilityQuery).mockReturnValue({
    data: {
      mechanism: "restart-manual",
      reloadAvailable: false,
      managedRuntime: false,
      detail: "no reload command exists",
      confirmRequired: true,
    },
    isPending: false,
    error: null,
  } as unknown as ReturnType<typeof useAgentApplyCapabilityQuery>);
}

function stubDeterministicContext(generation: "v1" | "v2" | null = "v1") {
  vi.mocked(useAgentEditorContextQuery).mockReturnValue({
    data: {
      writeTarget: "/tmp/config/opencode/opencode.jsonc",
      create: {
        targetGeneration: generation,
        requiresGenerationChoice: generation === null,
      },
      sourceHash: "hash-1",
      readOnly: false,
    },
    isPending: false,
    error: null,
  } as unknown as ReturnType<typeof useAgentEditorContextQuery>);
}

describe("AgentsPage", () => {
  it("lists config-declared agents as read-only with their details", async () => {
    stubApplyState();
    stubDeterministicContext();
    vi.mocked(useOpenCodeAgentsQuery).mockReturnValue({
      data: fixture,
      isPending: false,
      error: null,
    } as unknown as ReturnType<typeof useOpenCodeAgentsQuery>);

    renderPage();

    expect((await screen.findAllByText("reviewer")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Reviews code").length).toBeGreaterThan(0);
    expect(screen.getByText("broken")).toBeInTheDocument();
    // Editable agents show no read-only badge; only the invalid definition does.
    expect(screen.getAllByText("Read-only").length).toBe(1);
    expect(screen.getAllByText("V1 syntax").length).toBeGreaterThan(0);

    // A supported legacy/non-write-target definition is editable, not read-only.
    fireEvent.click(screen.getAllByText("legacy-agent")[0]);
    expect((await screen.findAllByText("Declared in the legacy config file")).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /^Edit$/ })).toBeInTheDocument();
    expect(screen.getAllByText(/editable in its declaring file/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByText("reviewer")[0]);
    await screen.findByText("You review code.");

    // Canonical Instructions and first-class Reasoning/Variant are visible
    // for the selected reviewer agent.
    fireEvent.click(screen.getAllByText("reviewer")[0]);
    expect(await screen.findByText("You review code.")).toBeInTheDocument();
    expect(screen.getByText("Reasoning / Variant")).toBeInTheDocument();
    expect(screen.getAllByText("high").length).toBeGreaterThan(0);
    expect(screen.getAllByText("anthropic/claude-sonnet-4").length).toBeGreaterThan(0);
    expect(screen.getByText("subagent")).toBeInTheDocument();
    expect(screen.getByText('"high"')).toBeInTheDocument();
    expect(
      screen.getByText(/Config-defined · editable in its declaring file/),
    ).toBeInTheDocument();
    // Editable config-defined agents expose the Edit control.
    expect(screen.getByRole("button", { name: /Edit/ })).toBeInTheDocument();

    // Selecting the unsupported definition shows its read-only reason truthfully.
    fireEvent.click(screen.getByText("broken"));
    expect(await screen.findByText("Definition is not a JSON object")).toBeInTheDocument();
    expect(screen.getByText("Agent definition is not an object")).toBeInTheDocument();
    // The unsupported definition keeps its read-only badge and no Edit control.
    expect(screen.queryByRole("button", { name: /^Edit$/ })).not.toBeInTheDocument();

    // A V2 agent shows parsed base model + variant and generation badge.
    fireEvent.click(screen.getByText("v2-writer"));
    expect((await screen.findAllByText("V2 syntax")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("anthropic/claude-sonnet-4-5").length).toBeGreaterThan(0);
  });

  it("shows an empty state when no agents are configured", () => {
    vi.mocked(useOpenCodeAgentsQuery).mockReturnValue({
      data: { ...fixture, agents: [] },
      isPending: false,
      error: null,
    } as unknown as ReturnType<typeof useOpenCodeAgentsQuery>);

    renderPage();

    expect(screen.getByText("No configured agents")).toBeInTheDocument();
  });

  it("surfaces load failures", async () => {
    vi.mocked(useOpenCodeAgentsQuery).mockReturnValue({
      data: undefined,
      isPending: false,
      error: new Error("boom"),
    } as unknown as ReturnType<typeof useOpenCodeAgentsQuery>);

    renderPage();

    await waitFor(() => expect(screen.getByText("boom")).toBeInTheDocument());
  });

  it("opens the editor directly when the create generation is deterministic", async () => {
    stubApplyState();
    stubDeterministicContext("v1");
    vi.mocked(useOpenCodeAgentsQuery).mockReturnValue({
      data: fixture,
      isPending: false,
      error: null,
    } as unknown as ReturnType<typeof useOpenCodeAgentsQuery>);

    renderPage();

    fireEvent.click(await screen.findByText("New sub-agent"));
    expect(await screen.findByText("New OpenCode sub-agent")).toBeInTheDocument();
    expect(screen.queryByText("Choose agent syntax")).not.toBeInTheDocument();
  });

  it("requires an explicit syntax choice when both generations exist", async () => {
    stubApplyState();
    stubDeterministicContext(null);
    vi.mocked(useOpenCodeAgentsQuery).mockReturnValue({
      data: { ...fixture, agents: [] },
      isPending: false,
      error: null,
    } as unknown as ReturnType<typeof useOpenCodeAgentsQuery>);

    renderPage();

    fireEvent.click(await screen.findByText("New sub-agent"));
    expect(await screen.findByText("Choose agent syntax")).toBeInTheDocument();
    // No editor until a generation is explicitly selected.
    expect(screen.queryByText("New OpenCode sub-agent")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("V2 syntax (agents / system / permissions)"));
    expect(await screen.findByText("New OpenCode sub-agent")).toBeInTheDocument();
    expect(screen.queryByText("Choose agent syntax")).not.toBeInTheDocument();
  });

  it("supports creating on a fresh config with no agent section after an explicit choice", async () => {
    stubApplyState();
    stubDeterministicContext(null);
    vi.mocked(useOpenCodeAgentsQuery).mockReturnValue({
      data: { ...fixture, agents: [] },
      isPending: false,
      error: null,
    } as unknown as ReturnType<typeof useOpenCodeAgentsQuery>);

    renderPage();

    fireEvent.click(await screen.findByText("New sub-agent"));
    fireEvent.click(await screen.findByText("V1 syntax (agent / prompt / permission)"));
    expect(await screen.findByText("New OpenCode sub-agent")).toBeInTheDocument();
    // Cancellation returns to the list without an editor.
    fireEvent.click(screen.getByText("Close"));
    await waitFor(() =>
      expect(screen.queryByText("New OpenCode sub-agent")).not.toBeInTheDocument(),
    );
  });

  it("shows the pending-apply badge and apply action from durable server state", async () => {
    stubApplyState(true);
    stubDeterministicContext("v1");
    vi.mocked(useOpenCodeAgentsQuery).mockReturnValue({
      data: fixture,
      isPending: false,
      error: null,
    } as unknown as ReturnType<typeof useOpenCodeAgentsQuery>);

    renderPage();

    expect(await screen.findByText("Apply changes…")).toBeInTheDocument();
    expect(await screen.findByText("Saved — pending apply in OpenCode")).toBeInTheDocument();
  });
});
