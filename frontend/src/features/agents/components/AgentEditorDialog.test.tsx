import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LocaleProvider } from "../../../i18n";
import { AgentEditorDialog } from "../components/AgentEditorDialog";
import type { OpenCodeAgentDto } from "../api/types";

import {
  useAgentEditorContextQuery,
  useCreateAgentMutation,
  useModelCatalogueQuery,
  usePreviewAgentCreateMutation,
  usePreviewAgentUpdateMutation,
  useUpdateAgentMutation,
} from "../api/queries";

vi.mock("../api/queries", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/queries")>();
  const idle = () => ({ mutateAsync: vi.fn(), isPending: false, data: undefined });
  return {
    ...actual,
    useAgentEditorContextQuery: vi.fn(() => ({ data: undefined, isPending: false })),
    useModelCatalogueQuery: vi.fn(() => ({
      data: { source: "unavailable", providers: [], totalModels: 0 },
      isPending: false,
      error: null,
    })),
    usePreviewAgentCreateMutation: vi.fn(idle),
    usePreviewAgentUpdateMutation: vi.fn(idle),
    useCreateAgentMutation: vi.fn(idle),
    useUpdateAgentMutation: vi.fn(idle),
  };
});


function renderDialog(mode: Parameters<typeof AgentEditorDialog>[0]["mode"]) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <LocaleProvider>
        <AgentEditorDialog
          open
          mode={mode}
          onOpenChange={() => undefined}
          onSaved={() => undefined}
        />
      </LocaleProvider>
    </QueryClientProvider>,
  );
}

const v1Agent: OpenCodeAgentDto = {
  name: "reviewer",
  schemaGeneration: "v1",
  description: "Reviews code",
  instructions: "Old prompt.",
  prompt: "Old prompt.",
  model: "anthropic/claude-sonnet-4",
  variant: "high",
  modelRaw: "anthropic/claude-sonnet-4",
  mode: "subagent",
  temperature: null,
  topP: null,
  steps: null,
  disabled: null,
  hidden: false,
  color: null,
  permissions: null,
  permission: null,
  tools: null,
  additionalOptions: { variant: "high" },
  source: {
    file: "xdg-jsonc",
    path: "/tmp/config/opencode/opencode.jsonc",
    format: "jsonc",
    isWriteTarget: true,
  },
  editability: "config",
  readOnlyReasons: [],
  readOnly: true,
  valid: true,
  diagnostic: null,
};

describe("AgentEditorDialog", () => {
  it("creates sub-agents with a locked subagent mode and explicit unsaved state", async () => {
    vi.mocked(useAgentEditorContextQuery).mockReturnValue({
      data: {
        writeTarget: "/tmp/config/opencode/opencode.jsonc",
        create: { targetGeneration: "v1", requiresGenerationChoice: false },
        sourceHash: "hash-1",
        readOnly: false,
      },
      isPending: false,
    } as ReturnType<typeof useAgentEditorContextQuery>);
    const create = { mutateAsync: vi.fn(), isPending: false, data: undefined };
    vi.mocked(usePreviewAgentCreateMutation).mockReturnValue(create as never);
    vi.mocked(useCreateAgentMutation).mockReturnValue({
      ...create,
      mutateAsync: vi.fn().mockResolvedValue({
        agent: v1Agent, changed: true, pendingApply: true, backup: null,
      }),
    } as never);

    renderDialog({ kind: "create", generation: "v1" });

    expect(await screen.findByText("New OpenCode sub-agent")).toBeInTheDocument();
    // Nothing entered yet: the draft is clean.
    expect(screen.getByText("Saved")).toBeInTheDocument();

    // Mode is locked to subagent for creation (labeled "Agent role").
    const modeSelect = screen.getByRole("combobox", { name: /Agent role/i }) as HTMLSelectElement;
    expect(modeSelect.disabled).toBe(true);
    expect(modeSelect.value).toBe("subagent");
    expect(screen.getByText("New agents are always created as sub-agents.")).toBeInTheDocument();

    // Typing a name marks the draft unsaved.
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: "summarizer" } });
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
  });

  it("shows the preview diff before save and reports pending apply", async () => {
    vi.mocked(useAgentEditorContextQuery).mockReturnValue({
      data: {
        writeTarget: "/tmp/config/opencode/opencode.jsonc",
        create: { targetGeneration: "v1", requiresGenerationChoice: false },
        sourceHash: "hash-1",
        readOnly: false,
      },
      isPending: false,
    } as ReturnType<typeof useAgentEditorContextQuery>);
    const preview = {
      mutateAsync: vi.fn().mockResolvedValue({
        mode: "update",
        generation: "v1",
        targetFile: "/tmp/config/opencode/opencode.jsonc",
        sourceHash: "hash-1",
        old: { description: "Reviews code" },
        new: { description: "Reviews carefully" },
        renameTo: null,
        textDiff: ["-  \"description\": \"Reviews code\",",
                   "+  \"description\": \"Reviews carefully\","],
      }),
      data: {
        mode: "update", generation: "v1", targetFile: "/tmp/config/opencode/opencode.jsonc",
        sourceHash: "hash-1", old: { description: "Reviews code" },
        new: { description: "Reviews carefully" }, renameTo: null,
        textDiff: [
          '-  "description": "Reviews code",',
          '+  "description": "Reviews carefully",',
        ],
      },
      isPending: false,
    };
    vi.mocked(usePreviewAgentUpdateMutation).mockReturnValue(preview as never);
    const save = {
      mutateAsync: vi.fn().mockResolvedValue({
        agent: v1Agent, changed: true, pendingApply: true, backup: null,
      }),
    };
    vi.mocked(useUpdateAgentMutation).mockReturnValue({ ...save, isPending: false } as never);

    renderDialog({ kind: "edit", agent: v1Agent, generation: null });

    const description = await screen.findByDisplayValue("Reviews code");
    fireEvent.change(description, { target: { value: "Reviews carefully" } });
    fireEvent.click(screen.getByText("Review changes"));

    const diff = await screen.findByLabelText("Proposed configuration diff");
    expect(diff.textContent).toContain('"description": "Reviews carefully"');
    expect(
      screen.getAllByText(/Saving writes configuration only/).length,
    ).toBeGreaterThan(0);

    await waitFor(() => {
      expect(preview.mutateAsync).toHaveBeenCalledWith({
        name: "reviewer",
        generation: null,
        fields: expect.objectContaining({ description: "Reviews carefully" }),
      });
    });

    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => {
      expect(save.mutateAsync).toHaveBeenCalledWith({
        name: "reviewer",
        generation: null,
        fields: expect.objectContaining({ description: "Reviews carefully" }),
        expectedSourceHash: "hash-1",
      });
    });
  });

  it("surfaces save failures without pretending the config was saved", async () => {
    vi.mocked(useAgentEditorContextQuery).mockReturnValue({
      data: {
        writeTarget: "/tmp/config/opencode/opencode.jsonc",
        create: { targetGeneration: "v1", requiresGenerationChoice: false },
        sourceHash: "hash-1",
        readOnly: false,
      },
      isPending: false,
    } as ReturnType<typeof useAgentEditorContextQuery>);
    vi.mocked(usePreviewAgentUpdateMutation).mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({
        mode: "update", generation: "v1", targetFile: "/t", sourceHash: "h",
        old: null, new: {}, renameTo: null, textDiff: [],
      }),
      isPending: false,
      data: undefined,
    } as never);
    vi.mocked(useUpdateAgentMutation).mockReturnValue({
      mutateAsync: vi.fn().mockRejectedValue(
        new Error("The configuration changed concurrently; re-open this agent and retry."),
      ),
    } as never);

    renderDialog({ kind: "edit", agent: v1Agent, generation: null });
    fireEvent.change(await screen.findByDisplayValue("Reviews code"), {
      target: { value: "Changed" },
    });
    fireEvent.click(screen.getByText("Review changes"));
    fireEvent.click(await screen.findByText("Save"));

    expect(
      await screen.findByText(/changed concurrently/i),
    ).toBeInTheDocument();
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
  });
});

describe("AgentEditorDialog — model picker drives variant options", () => {
  it("selecting a model from the picker populates the field and updates variants", async () => {
    vi.mocked(useModelCatalogueQuery).mockReturnValue({
      data: {
        source: "runtime" as const,
        totalModels: 1,
        providers: [
          {
            id: "anthropic", name: "Anthropic", source: "env", connected: true,
            models: [
              {
                id: "anthropic/claude-sonnet-4-5", providerId: "anthropic",
                providerName: "Anthropic", name: "Claude Sonnet 4.5",
                status: "active", reasoning: true, variants: ["low", "medium", "high"],
              },
            ],
          },
        ],
      },
      isPending: false,
      error: null,
    } as ReturnType<typeof useModelCatalogueQuery>);

    renderDialog({ kind: "create", generation: "v1" });

    // No variant select before a model is chosen (free-text input instead).
    expect(screen.queryByRole("combobox", { name: /Reasoning/ })).not.toBeInTheDocument();

    // Browser-realistic pointer selection through the picker.
    const input = screen.getByRole("textbox", { name: /Model/i });
    fireEvent.focus(input);
    const option = screen.getByRole("option", { name: /Claude Sonnet 4.5/ });
    fireEvent.mouseDown(option, { buttons: 1 });
    fireEvent.blur(input, { relatedTarget: option });
    fireEvent.click(option);

    // The variant selector now offers exactly the selected model's variants.
    const variantSelect = await screen.findByRole("combobox", { name: /Reasoning \/ Variant/ });
    const options = Array.from((variantSelect as HTMLSelectElement).options);
    expect(options.map((o) => o.value)).toEqual(["", "low", "medium", "high"]);

    // Choose a variant and confirm it sticks.
    fireEvent.change(variantSelect, { target: { value: "high" } });
    expect((variantSelect as HTMLSelectElement).value).toBe("high");
  });
});

describe("AgentEditorDialog — responsive layout structure (Task 14B)", () => {
  it("renders the wide dialog with an internal scroll body and reachable footer actions", () => {
    renderDialog({ kind: "create", generation: "v1" });

    // Radix portals the dialog into document.body.
    const dialog = document.body.querySelector(".dialog-content.agent-editor");
    expect(dialog).not.toBeNull();

    // Content scrolls internally inside a bounded body region...
    const body = dialog?.querySelector(".agent-editor__body");
    expect(body).not.toBeNull();

    // ...while the action footer stays outside the scroll region.
    const actions = dialog?.querySelector(".dialog-actions");
    expect(actions).not.toBeNull();
    expect(body?.contains(actions as Node)).toBe(false);

    // Two-column grid fields with wide spans present.
    const form = dialog?.querySelector(".agent-editor__form");
    expect(form?.className).not.toContain("agent-editor__form--single");
    expect(dialog?.querySelector(".agent-editor__field--wide")).not.toBeNull();
  });
});
