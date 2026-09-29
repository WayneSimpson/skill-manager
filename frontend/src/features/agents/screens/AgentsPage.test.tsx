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
  };
});

import { useOpenCodeAgentsQuery } from "../api/queries";

const fixture: OpenCodeAgentsDto = {
  agents: [
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
      readOnly: true,
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
      readOnly: true,
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

describe("AgentsPage", () => {
  it("lists config-declared agents as read-only with their details", async () => {
    vi.mocked(useOpenCodeAgentsQuery).mockReturnValue({
      data: fixture,
      isPending: false,
      error: null,
    } as unknown as ReturnType<typeof useOpenCodeAgentsQuery>);

    renderPage();

    expect((await screen.findAllByText("reviewer")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Reviews code").length).toBeGreaterThan(0);
    expect(screen.getByText("broken")).toBeInTheDocument();
    // Every agent is presented read-only, with its schema generation shown.
    expect(screen.getAllByText("Read-only").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("V1 syntax").length).toBeGreaterThan(0);

    // Canonical Instructions and first-class Reasoning/Variant are visible.
    expect(screen.getByText("You review code.")).toBeInTheDocument();
    expect(screen.getByText("Reasoning / Variant")).toBeInTheDocument();
    expect(screen.getAllByText("high").length).toBeGreaterThan(0);
    expect(screen.getAllByText("anthropic/claude-sonnet-4").length).toBeGreaterThan(0);
    expect(screen.getByText("subagent")).toBeInTheDocument();
    expect(screen.getByText('"high"')).toBeInTheDocument();
    expect(
      screen.getByText(/Config-defined \(editing arrives in a later update\)/),
    ).toBeInTheDocument();

    // Selecting the unsupported definition shows its read-only reason truthfully.
    fireEvent.click(screen.getByText("broken"));
    expect(await screen.findByText("Definition is not a JSON object")).toBeInTheDocument();
    expect(screen.getByText("Agent definition is not an object")).toBeInTheDocument();

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
});
