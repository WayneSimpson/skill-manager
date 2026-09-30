import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LocaleProvider } from "../../../i18n";
import { AgentEditorDialog } from "./AgentEditorDialog";
import type { ModelCatalogueDto, OpenCodeAgentDto } from "../api/types";

vi.mock("../api/queries", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/queries")>();
  const idle = () => ({ mutateAsync: vi.fn(), isPending: false, data: undefined });
  return {
    ...actual,
    useAgentEditorContextQuery: vi.fn(() => ({
      data: {
        writeTarget: "/tmp/config/opencode/opencode.jsonc",
        create: { targetGeneration: "v1", requiresGenerationChoice: false },
        sourceHash: "hash-1",
        readOnly: false,
      },
      isPending: false,
    })),
    useModelCatalogueQuery: vi.fn(),
    usePreviewAgentCreateMutation: vi.fn(idle),
    usePreviewAgentUpdateMutation: vi.fn(idle),
    useCreateAgentMutation: vi.fn(idle),
    useUpdateAgentMutation: vi.fn(idle),
  };
});

import {
  useModelCatalogueQuery,
  usePreviewAgentCreateMutation,
  usePreviewAgentUpdateMutation,
  useUpdateAgentMutation,
} from "../api/queries";

const catalogue: ModelCatalogueDto = {
  source: "runtime",
  totalModels: 3,
  providers: [
    {
      id: "anthropic", name: "Anthropic", source: "env", connected: true,
      models: [
        {
          id: "anthropic/claude-sonnet-4-5", providerId: "anthropic",
          providerName: "Anthropic", name: "Claude Sonnet 4.5",
          status: "active", reasoning: true, variants: ["low", "high"],
        },
        {
          id: "anthropic/claude-haiku-4-5", providerId: "anthropic",
          providerName: "Anthropic", name: "Claude Haiku 4.5",
          status: "active", reasoning: false, variants: [],
        },
      ],
    },
  ],
};

function makeAgent(overrides: Partial<OpenCodeAgentDto> = {}): OpenCodeAgentDto {
  return {
    name: "reviewer", schemaGeneration: "v1", description: "Reviews",
    instructions: "Review code.", prompt: "Review code.",
    model: "anthropic/claude-sonnet-4-5", variant: "high",
    modelRaw: "anthropic/claude-sonnet-4-5", mode: "subagent",
    temperature: null, topP: null, steps: null, disabled: null, hidden: null,
    color: null, permissions: null, permission: null, tools: null,
    additionalOptions: {}, source: null, editability: "config",
    readOnlyReasons: [], readOnly: false, valid: true, diagnostic: null,
    ...overrides,
  };
}

function renderDialog(props: Parameters<typeof AgentEditorDialog>[0]) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <LocaleProvider>
        <AgentEditorDialog {...props} />
      </LocaleProvider>
    </QueryClientProvider>,
  );
}

function stubCatalogue(cat: ModelCatalogueDto = catalogue) {
  vi.mocked(useModelCatalogueQuery).mockReturnValue({
    data: cat, isPending: false, error: null,
  } as unknown as ReturnType<typeof useModelCatalogueQuery>);
}

describe("AgentEditorDialog — Task 14 integration", () => {
  it("uses ModelPicker, not a free-text model input", async () => {
    stubCatalogue();
    renderDialog({
      open: true,
      mode: { kind: "edit", agent: makeAgent(), generation: null },
      onOpenChange: () => undefined,
      onSaved: () => undefined,
    });

    // The old free-text input had list="agent-variant-model-options".
    expect(screen.queryByLabelText(/list=/)).not.toBeInTheDocument();

    // ModelPicker is present with the current model value.
    const modelInput = screen.getByRole("textbox", { name: /Model/i });
    expect((modelInput as HTMLInputElement).value).toBe("anthropic/claude-sonnet-4-5");
  });

  it("selecting a model updates variant options from the catalogue", async () => {
    stubCatalogue();
    renderDialog({
      open: true,
      mode: { kind: "edit", agent: makeAgent({ model: "anthropic/claude-sonnet-4-5", variant: "" }), generation: null },
      onOpenChange: () => undefined,
      onSaved: () => undefined,
    });

    // Focus model picker and switch to the haiku model (no variants).
    const modelInput = screen.getByRole("textbox", { name: /Model/i });
    fireEvent.focus(modelInput);
    fireEvent.click(screen.getByRole("option", { name: /Claude Haiku/ }));

    // Haiku has no variants → variant input shows the no-variants placeholder.
    await waitFor(() => {
      expect(
        screen.getByPlaceholderText(/No variants available/),
      ).toBeInTheDocument();
    });
  });

  it("shows variant dropdown options from the catalogue for a model with variants", async () => {
    stubCatalogue();
    renderDialog({
      open: true,
      mode: { kind: "edit", agent: makeAgent({ model: "anthropic/claude-sonnet-4-5", variant: "" }), generation: null },
      onOpenChange: () => undefined,
      onSaved: () => undefined,
    });

    const variantSelect = screen.getByRole("combobox", { name: /Reasoning \/ Variant/i });
    const options = Array.from((variantSelect as HTMLSelectElement).options).map(
      (o) => o.value,
    );
    expect(options).toContain("low");
    expect(options).toContain("high");
  });

  it("preserves existing custom variant with a warning on initial load", () => {
    stubCatalogue();
    renderDialog({
      open: true,
      mode: { kind: "edit", agent: makeAgent({ variant: "ultra-custom" }), generation: null },
      onOpenChange: () => undefined,
      onSaved: () => undefined,
    });

    expect(screen.getByText(/Custom variant: ultra-custom/)).toBeInTheDocument();
    const variantSelect = screen.getByRole("combobox", { name: /Reasoning \/ Variant/i });
    expect((variantSelect as HTMLSelectElement).value).toBe("ultra-custom");
  });

  it("includes PermissionEditor in the form", () => {
    stubCatalogue();
    renderDialog({
      open: true,
      mode: { kind: "edit", agent: makeAgent(), generation: null },
      onOpenChange: () => undefined,
      onSaved: () => undefined,
    });

    expect(screen.getByLabelText(/Permissions/)).toBeInTheDocument();
    expect(screen.getByText(/Edit \/ write files/)).toBeInTheDocument();
    expect(screen.getByText(/Shell \/ bash/)).toBeInTheDocument();
  });

  it("initialises V1 permission rules from the agent DTO", () => {
    stubCatalogue();
    const agent = makeAgent({
      permissions: { edit: "deny", bash: "ask" } as unknown as Record<string, unknown>,
    });
    renderDialog({
      open: true,
      mode: { kind: "edit", agent, generation: null },
      onOpenChange: () => undefined,
      onSaved: () => undefined,
    });

    const editSelect = screen.getByLabelText(/Edit \/ write files/) as HTMLSelectElement;
    expect(editSelect.value).toBe("deny");
    const bashSelect = screen.getByLabelText(/Shell \/ bash/) as HTMLSelectElement;
    expect(bashSelect.value).toBe("ask");
  });

  it("initialises V2 ordered permission rules from the agent DTO", () => {
    stubCatalogue();
    const agent = makeAgent({
      schemaGeneration: "v2",
      permissions: [
        { action: "edit", resource: "*", effect: "deny" },
        { action: "shell", resource: "git status", effect: "allow" },
      ] as unknown as Array<Record<string, unknown>>,
    });
    renderDialog({
      open: true,
      mode: { kind: "edit", agent, generation: null },
      onOpenChange: () => undefined,
      onSaved: () => undefined,
    });

    // V2 ordered rules with resource: "*" appear in the advanced section.
    fireEvent.click(screen.getByText(/Advanced permission rules/));
    const inputs = screen.getAllByLabelText(/Action\/tool/);
    expect((inputs[0] as HTMLInputElement).value).toBe("edit");
    expect((inputs[1] as HTMLInputElement).value).toBe("shell");
  });

  it("emits permissionRules in the preview payload when permissions change", async () => {
    stubCatalogue();
    const preview = { mutateAsync: vi.fn().mockResolvedValue({ textDiff: [] }) };
    vi.mocked(usePreviewAgentUpdateMutation).mockReturnValue(preview as never);

    renderDialog({
      open: true,
      mode: { kind: "edit", agent: makeAgent(), generation: null },
      onOpenChange: () => undefined,
      onSaved: () => undefined,
    });

    fireEvent.change(screen.getByLabelText(/Shell \/ bash/), { target: { value: "deny" } });
    fireEvent.click(screen.getByText(/Review changes/));

    await waitFor(() => {
      expect(preview.mutateAsync).toHaveBeenCalled();
    });
    const call = preview.mutateAsync.mock.calls[0][0];
    expect(call.fields.permissionRules).toBeDefined();
    expect(call.fields.permissionRules).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ action: "bash", effect: "deny" }),
      ]),
    );
  });

  it("does NOT emit permissionRules for unrelated edits", async () => {
    stubCatalogue();
    const preview = { mutateAsync: vi.fn().mockResolvedValue({ textDiff: [] }) };
    vi.mocked(usePreviewAgentUpdateMutation).mockReturnValue(preview as never);

    renderDialog({
      open: true,
      mode: { kind: "edit", agent: makeAgent(), generation: null },
      onOpenChange: () => undefined,
      onSaved: () => undefined,
    });

    // Only change description — permissions untouched.
    fireEvent.change(screen.getByDisplayValue("Reviews"), { target: { value: "New desc" } });
    fireEvent.click(screen.getByText(/Review changes/));

    await waitFor(() => {
      expect(preview.mutateAsync).toHaveBeenCalled();
    });
    const call = preview.mutateAsync.mock.calls[0][0];
    expect(call.fields.permissionRules).toBeUndefined();
  });

  it("create mode remains locked to sub-agent with human-readable labels", () => {
    stubCatalogue();
    renderDialog({
      open: true,
      mode: { kind: "create", generation: "v1" },
      onOpenChange: () => undefined,
      onSaved: () => undefined,
    });

    const modeSelect = screen.getByRole("combobox", { name: /Agent role/i }) as HTMLSelectElement;
    expect(modeSelect.disabled).toBe(true);
    expect(modeSelect.value).toBe("subagent");
    expect(screen.getByText(/always created as sub-agents/)).toBeInTheDocument();

    // Human-readable label shown.
    expect(screen.getByText(/Sub-agent only/)).toBeInTheDocument();
  });

  it("edit mode shows human-readable labels", () => {
    stubCatalogue();
    renderDialog({
      open: true,
      mode: { kind: "edit", agent: makeAgent({ mode: "all" }), generation: null },
      onOpenChange: () => undefined,
      onSaved: () => undefined,
    });

    const modeSelect = screen.getByRole("combobox", { name: /Agent role/i }) as HTMLSelectElement;
    expect(modeSelect.value).toBe("all");
    const options = Array.from(modeSelect.options).map((o) => o.text);
    expect(options).toContain("Sub-agent only");
    expect(options).toContain("Primary / main-session agent");
    expect(options).toContain("Both primary and sub-agent");
  });

  it("custom/unavailable model is preserved in the picker on initial load", () => {
    stubCatalogue();
    renderDialog({
      open: true,
      mode: { kind: "edit", agent: makeAgent({ model: "custom/legacy-model" }), generation: null },
      onOpenChange: () => undefined,
      onSaved: () => undefined,
    });

    const modelInput = screen.getByRole("textbox", { name: /Model/i }) as HTMLInputElement;
    expect(modelInput.value).toBe("custom/legacy-model");
    expect(screen.getByText(/Custom\/unavailable model/)).toBeInTheDocument();
  });
});

describe("AgentEditorDialog — touched/dirty field semantics", () => {
  it("unrelated edit does NOT send mode when the agent originally omitted it", async () => {
    stubCatalogue();
    const preview = { mutateAsync: vi.fn().mockResolvedValue({ textDiff: [] }) };
    vi.mocked(usePreviewAgentUpdateMutation).mockReturnValue(preview as never);
    const agent = makeAgent({ mode: null });

    renderDialog({
      open: true, mode: { kind: "edit", agent, generation: null },
      onOpenChange: () => undefined, onSaved: () => undefined,
    });

    fireEvent.change(screen.getByDisplayValue("Reviews"), { target: { value: "New" } });
    fireEvent.click(screen.getByText(/Review changes/));

    await waitFor(() => expect(preview.mutateAsync).toHaveBeenCalled());
    const call = preview.mutateAsync.mock.calls[0][0];
    expect(call.fields.mode).toBeUndefined();
    expect(call.fields.model).toBeUndefined();
    expect(call.fields.variant).toBeUndefined();
  });

  it("unrelated edit does NOT send model/variant when they were unchanged", async () => {
    stubCatalogue();
    const preview = { mutateAsync: vi.fn().mockResolvedValue({ textDiff: [] }) };
    vi.mocked(usePreviewAgentUpdateMutation).mockReturnValue(preview as never);
    const agent = makeAgent({ model: "anthropic/claude-sonnet-4-5", variant: "high" });

    renderDialog({
      open: true, mode: { kind: "edit", agent, generation: null },
      onOpenChange: () => undefined, onSaved: () => undefined,
    });

    fireEvent.change(screen.getByDisplayValue("Reviews"), { target: { value: "New" } });
    fireEvent.click(screen.getByText(/Review changes/));

    await waitFor(() => expect(preview.mutateAsync).toHaveBeenCalled());
    const call = preview.mutateAsync.mock.calls[0][0];
    expect(call.fields.model).toBeUndefined();
    expect(call.fields.variant).toBeUndefined();
  });

  it("sends mode when the user explicitly changes it", async () => {
    stubCatalogue();
    const preview = { mutateAsync: vi.fn().mockResolvedValue({ textDiff: [] }) };
    vi.mocked(usePreviewAgentUpdateMutation).mockReturnValue(preview as never);
    const agent = makeAgent({ mode: "subagent" });

    renderDialog({
      open: true, mode: { kind: "edit", agent, generation: null },
      onOpenChange: () => undefined, onSaved: () => undefined,
    });

    fireEvent.change(screen.getByRole("combobox", { name: /Agent role/i }), {
      target: { value: "all" },
    });
    fireEvent.click(screen.getByText(/Review changes/));

    await waitFor(() => expect(preview.mutateAsync).toHaveBeenCalled());
    const call = preview.mutateAsync.mock.calls[0][0];
    expect(call.fields.mode).toBe("all");
  });
});

describe("AgentEditorDialog — preview/save payload consistency", () => {
  it("save payload exactly matches preview payload for touched model/variant/mode", async () => {
    stubCatalogue();
    const preview = { mutateAsync: vi.fn().mockResolvedValue({ textDiff: [] }) };
    const save = { mutateAsync: vi.fn().mockResolvedValue({ changed: true }) };
    vi.mocked(usePreviewAgentUpdateMutation).mockReturnValue(preview as never);
    vi.mocked(useUpdateAgentMutation).mockReturnValue(save as never);
    const agent = makeAgent({ mode: "subagent", model: "anthropic/claude-sonnet-4-5", variant: "high" });

    renderDialog({
      open: true, mode: { kind: "edit", agent, generation: null },
      onOpenChange: () => undefined, onSaved: () => undefined,
    });

    // Change mode, variant, and description.
    fireEvent.change(screen.getByRole("combobox", { name: /Agent role/i }), {
      target: { value: "all" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: /Reasoning \/ Variant/i }), {
      target: { value: "low" },
    });
    fireEvent.change(screen.getByDisplayValue("Reviews"), { target: { value: "New" } });

    // Preview.
    fireEvent.click(screen.getByText(/Review changes/));
    await waitFor(() => expect(preview.mutateAsync).toHaveBeenCalled());
    const previewFields = preview.mutateAsync.mock.calls[0][0].fields;

    // Save (go back and save to exercise the save path).
    fireEvent.click(screen.getByText(/Back to editing/));
    fireEvent.click(screen.getByText(/Review changes/));
    await waitFor(() => expect(preview.mutateAsync).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByText(/^Save$/));
    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalled());
    const saveFields = save.mutateAsync.mock.calls[0][0].fields;

    // Both payloads must be identical.
    expect(saveFields).toEqual(previewFields);
    expect(saveFields.mode).toBe("all");
    expect(saveFields.variant).toBe("low");
    expect(saveFields.description).toBe("New");
    // Untouched model not sent.
    expect(saveFields.model).toBeUndefined();
  });

  it("unrelated edit omits untouched mode/model/variant in BOTH preview and save", async () => {
    stubCatalogue();
    const preview = { mutateAsync: vi.fn().mockResolvedValue({ textDiff: [] }) };
    const save = { mutateAsync: vi.fn().mockResolvedValue({ changed: true }) };
    vi.mocked(usePreviewAgentUpdateMutation).mockReturnValue(preview as never);
    vi.mocked(useUpdateAgentMutation).mockReturnValue(save as never);
    const agent = makeAgent({ mode: null });

    renderDialog({
      open: true, mode: { kind: "edit", agent, generation: null },
      onOpenChange: () => undefined, onSaved: () => undefined,
    });

    fireEvent.change(screen.getByDisplayValue("Reviews"), { target: { value: "New" } });
    fireEvent.click(screen.getByText(/Review changes/));
    await waitFor(() => expect(preview.mutateAsync).toHaveBeenCalled());
    const previewFields = preview.mutateAsync.mock.calls[0][0].fields;

    // Stay on preview and save directly.
    const saveButton = await screen.findByText(/^Save$/);
    fireEvent.click(saveButton);
    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalled());
    const saveFields = save.mutateAsync.mock.calls[0][0].fields;

    expect(saveFields).toEqual(previewFields);
    expect(saveFields.mode).toBeUndefined();
    expect(saveFields.model).toBeUndefined();
    expect(saveFields.variant).toBeUndefined();
  });
});
