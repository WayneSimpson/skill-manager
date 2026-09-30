import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useMemo, useState } from "react";

import { LoadingSpinner } from "../../../components/LoadingSpinner";
import { useAgentsCopy } from "../i18n";
import type { OpenCodeAgentDto, PermissionRuleDto, ModelCatalogueDto } from "../api/types";
import { ModelPicker } from "./ModelPicker";
import { PermissionEditor } from "./PermissionEditor";
import {
  useAgentEditorContextQuery,
  useCreateAgentMutation,
  useMcpServersQuery,
  useModelCatalogueQuery,
  usePreviewAgentCreateMutation,
  usePreviewAgentUpdateMutation,
  useUpdateAgentMutation,
} from "../api/queries";

export interface AgentEditorState {
  name: string;
  description: string;
  instructions: string;
  model: string;
  variant: string;
  mode: string;
  permissionRules: PermissionRuleDto[];
}

type EditorMode = { kind: "create"; generation: "v1" | "v2" } | {
  kind: "edit"; agent: OpenCodeAgentDto; generation: "v1" | "v2" | null;
};

interface AgentEditorDialogProps {
  open: boolean;
  mode: EditorMode;
  onOpenChange: (open: boolean) => void;
  onSaved: (result: { changed: boolean }) => void;
}

export function AgentEditorDialog({ open, mode, onOpenChange, onSaved }: AgentEditorDialogProps) {
  const copy = useAgentsCopy();
  const contextQuery = useAgentEditorContextQuery();
  const catalogueQuery = useModelCatalogueQuery();
  const mcpQuery = useMcpServersQuery();
  const isCreate = mode.kind === "create";
  const generation = isCreate ? mode.generation : mode.agent.schemaGeneration;

  const [form, setForm] = useState<AgentEditorState>(emptyForm());
  const [permissionsDirty, setPermissionsDirty] = useState(false);
  const [touchedFields, setTouchedFields] = useState<Set<string>>(new Set());
  const [step, setStep] = useState<"fields" | "preview">("fields");
  const [error, setError] = useState("");
  const [savedNote, setSavedNote] = useState(false);

  const previewCreate = usePreviewAgentCreateMutation();
  const previewUpdate = usePreviewAgentUpdateMutation();
  const createMutation = useCreateAgentMutation();
  const updateMutation = useUpdateAgentMutation();
  const savePending = createMutation.isPending || updateMutation.isPending;

  const targetKey = mode.kind === "create"
    ? `create:${mode.generation}`
    : `edit:${mode.agent.name}:${mode.agent.schemaGeneration}`;

  useEffect(() => {
    if (open) {
      setForm(mode.kind === "create" ? emptyForm() : formFromAgent(mode.agent));
      setPermissionsDirty(false);
      setTouchedFields(new Set());
      setStep("fields");
      setError("");
      setSavedNote(false);
      return;
    }
    setSavedNote(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, targetKey]);

  // Derive per-model variant options from the catalogue (not the old cache-only API).
  const variantOptions = useMemo(() => {
    const model = form.model.trim();
    if (!model || !catalogueQuery.data) return [];
    for (const provider of catalogueQuery.data.providers) {
      for (const entry of provider.models) {
        if (entry.id === model) return entry.variants;
      }
    }
    return []; // Model not in catalogue — no known variants; custom is preserved.
  }, [form.model, catalogueQuery.data]);

  // Track whether the variant is a custom value not in the catalogue's list.
  const isCustomVariant = useMemo(
    () => Boolean(form.variant.trim()) && !variantOptions.includes(form.variant.trim()),
    [form.variant, variantOptions],
  );

  const dirty = useMemo(
    () => isCreate
      ? Boolean(form.name.trim()) || permissionsDirty
      : mode.kind === "edit" && (formDiffers(form, mode.agent) || permissionsDirty),
    [form, isCreate, mode, permissionsDirty],
  );

  const sourceHash = contextQuery.data?.sourceHash ?? "";

  function update(patch: Partial<AgentEditorState>) {
    setForm((current) => ({ ...current, ...patch }));
    setTouchedFields((current) => {
      const next = new Set(current);
      for (const key of Object.keys(patch)) {
        next.add(key);
      }
      return next;
    });
    setSavedNote(false);
  }

  function handleModelChange(model: string) {
    setTouchedFields((current) => new Set(current).add("model"));
    setForm((current) => {
      const previousModel = current.model;
      const next = { ...current, model };
      // Only reset variant when the model actually changes and the variant
      // was chosen for the previous model. Custom/unknown variants on the
      // initial load are preserved until the user acts.
      if (model !== previousModel && current.variant.trim()) {
        // The new model's variants will be recomputed by useMemo; we cannot
        // check them here synchronously. Clear the variant if the model is
        // changing — the user will see the new options and re-pick.
        next.variant = "";
      }
      return next;
    });
    setSavedNote(false);
  }

  function handlePermissionsChange(rules: PermissionRuleDto[]) {
    setForm((current) => ({ ...current, permissionRules: rules }));
    setPermissionsDirty(true);
    setSavedNote(false);
  }

  async function buildPreview() {
    setError("");
    try {
      if (isCreate) {
        await previewCreate.mutateAsync({
          generation: mode.generation,
          fields: fieldsPayload(form, true, undefined, permissionsDirty, touchedFields),
        });
      } else if (mode.kind === "edit") {
        await previewUpdate.mutateAsync({
          name: mode.agent.name,
          generation: mode.generation,
          fields: fieldsPayload(form, false, mode.agent.name, permissionsDirty, touchedFields),
        });
      }
      setStep("preview");
    } catch (previewError) {
      setError(errorMessage(previewError, copy.errors.preview));
    }
  }

  async function save() {
    setError("");
    try {
      const result = isCreate
        ? await createMutation.mutateAsync({
            generation: (mode as { generation: "v1" | "v2" }).generation,
            fields: fieldsPayload(form, true, undefined, permissionsDirty, touchedFields),
            expectedSourceHash: sourceHash,
          })
        : await updateMutation.mutateAsync({
            name: (mode as { agent: OpenCodeAgentDto }).agent.name,
            generation: (mode as { generation: "v1" | "v2" | null }).generation,
            fields: fieldsPayload(form, false, (mode as { agent: OpenCodeAgentDto }).agent.name, permissionsDirty, touchedFields),
            expectedSourceHash: sourceHash,
          });
      setSavedNote(true);
      onSaved({ changed: result.changed });
      onOpenChange(false);
    } catch (saveError) {
      setError(errorMessage(saveError, copy.errors.save));
    }
  }

  const preview = previewCreate.data ?? previewUpdate.data ?? null;

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !savePending && onOpenChange(next)}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content agent-editor" aria-label={editorTitle(copy, mode)}>
          <Dialog.Description className="agent-editor__description">
            {copy.editor.pendingApplyNote}
          </Dialog.Description>
          <div className="dialog-header">
            <Dialog.Title className="dialog-title">{editorTitle(copy, mode)}</Dialog.Title>
            <span className={`agent-editor__state${dirty ? " is-unsaved" : ""}`}>
              {dirty ? copy.editor.unsaved : copy.editor.saved}
            </span>
            <Dialog.Close className="agent-editor__close" aria-label={copy.editor.close}>
              ×
            </Dialog.Close>
          </div>

          {step === "fields" ? (
            <div className="agent-editor__form">
              <label>
                <span>{copy.editor.fields.name}</span>
                <input
                  value={form.name}
                  onChange={(event) => update({ name: event.target.value })}
                />
              </label>
              <label>
                <span>{copy.editor.fields.description}</span>
                <input
                  value={form.description}
                  onChange={(event) => update({ description: event.target.value })}
                />
              </label>
              <label>
                <span>{copy.editor.fields.instructions}</span>
                <textarea
                  rows={5}
                  value={form.instructions}
                  onChange={(event) => update({ instructions: event.target.value })}
                />
              </label>
              <div>
                <span>{copy.editor.fields.model}</span>
                <ModelPicker
                  value={form.model}
                  onChange={handleModelChange}
                  catalogue={catalogueQuery.data}
                />
              </div>
              <label>
                <span>{copy.editor.fields.variant}</span>
                {variantOptions.length > 0 ? (
                  <select
                    value={form.variant}
                    onChange={(event) => update({ variant: event.target.value })}
                  >
                    <option value="">{copy.editor.variantStates.none}</option>
                    {variantOptions.map((option) => (
                      <option key={option} value={option}>{option}</option>
                    ))}
                    {form.variant.trim() && isCustomVariant ? (
                      <option value={form.variant}>{form.variant}</option>
                    ) : null}
                  </select>
                ) : (
                  <input
                    value={form.variant}
                    placeholder={form.model.trim() ? copy.editor.variantStates.none : copy.editor.fields.variantFree}
                    disabled={Boolean(form.model.trim()) && variantOptions.length === 0 && !isCustomVariant}
                    onChange={(event) => update({ variant: event.target.value })}
                  />
                )}
                {isCustomVariant ? (
                  <em className="agent-editor__hint">
                    {copy.editor.variantStates.custom(form.variant)}
                  </em>
                ) : null}
              </label>
              <label>
                <span>{copy.editor.fields.mode}</span>
                <select
                  value={form.mode || (isCreate ? "subagent" : "")}
                  disabled={isCreate}
                  onChange={(event) => update({ mode: event.target.value })}
                >
                  {!isCreate && !form.mode ? (
                    <option value="">{copy.editor.variantStates.none}</option>
                  ) : null}
                  {(isCreate ? ["subagent"] : ["subagent", "primary", "all"]).map((value) => (
                    <option key={value} value={value}>
                      {copy.editor.modeLabels[value as "subagent"]}
                    </option>
                  ))}
                </select>
                <em className="agent-editor__mode-hint">
                  {isCreate ? copy.editor.subagentLocked : copy.editor.modeHint}
                </em>
              </label>
              <PermissionEditor
                generation={generation}
                rules={form.permissionRules}
                onChange={handlePermissionsChange}
                mcpServers={mcpQuery.data?.servers}
                mcpSource={mcpQuery.data?.source}
              />
            </div>
          ) : (
            <div className="agent-editor__preview">
              <p className="agent-editor__preview-note">{copy.editor.previewNote}</p>
              <pre className="agent-editor__diff" aria-label={copy.editor.diffLabel}>
                {(preview?.textDiff ?? []).join("\n")}
              </pre>
              <p className="agent-editor__pending">{copy.editor.pendingApplyNote}</p>
            </div>
          )}

          {error ? <p className="agent-editor__error" role="alert">{error}</p> : null}
          {savedNote ? <p className="agent-editor__saved">{copy.editor.savedToast}</p> : null}

          <div className="dialog-actions">
            {step === "preview" ? (
              <button type="button" className="btn" onClick={() => setStep("fields")}>
                {copy.editor.backToEdit}
              </button>
            ) : null}
            <button type="button" className="btn" onClick={() => onOpenChange(false)}>
              {copy.editor.close}
            </button>
            {step === "fields" ? (
              <button
                type="button"
                className="btn btn--primary"
                disabled={!dirty || previewCreate.isPending || previewUpdate.isPending}
                onClick={() => void buildPreview()}
              >
                {previewCreate.isPending || previewUpdate.isPending ? (
                  <LoadingSpinner size="sm" label={copy.editor.previewPending} />
                ) : null}
                {copy.editor.reviewChanges}
              </button>
            ) : (
              <button
                type="button"
                className="btn btn--primary"
                disabled={savePending}
                onClick={() => void save()}
              >
                {savePending ? <LoadingSpinner size="sm" label={copy.editor.saving} /> : null}
                {copy.editor.save}
              </button>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function emptyForm(): AgentEditorState {
  return {
    name: "", description: "", instructions: "", model: "", variant: "",
    mode: "subagent", permissionRules: [],
  };
}

function formFromAgent(agent: OpenCodeAgentDto): AgentEditorState {
  return {
    name: agent.name,
    description: agent.description ?? "",
    instructions: agent.instructions ?? "",
    model: agent.model ?? "",
    variant: agent.variant ?? "",
    // Preserve omitted mode as empty string; fieldsPayload only sends when touched.
    mode: agent.mode ?? "",
    permissionRules: parseAgentPermissions(agent),
  };
}

/** Parse the agent DTO's raw permissions into neutral rules for the editor. */
function parseAgentPermissions(agent: OpenCodeAgentDto): PermissionRuleDto[] {
  const raw = agent.permissions;
  if (raw == null) return [];
  if (Array.isArray(raw)) {
    // V2 ordered rules: carry order and rawValue for opaque fidelity.
    return raw
      .filter((entry): entry is Record<string, unknown> =>
        typeof entry === "object" && entry !== null &&
        "action" in entry && typeof (entry as { action: unknown }).action === "string")
      .map((entry, order) => {
        const hasExtra = Object.keys(entry).some(
          (k) => !["action", "effect", "resource"].includes(k),
        );
        return {
          action: entry.action as string,
          effect: ((entry.effect as string) ?? "allow") as PermissionRuleDto["effect"],
          resource: typeof entry.resource === "string" ? entry.resource : null,
          order,
          // Preserve extra V2 fields for lossless round-trip.
          ...(hasExtra ? { rawValue: entry } : {}),
        };
      });
  }
  if (typeof raw === "object") {
    // V1 permission dict: carry rawValue for unknown/nested shapes.
    const rules: PermissionRuleDto[] = [];
    let order = 0;
    for (const [action, value] of Object.entries(raw)) {
      if (typeof value === "string" && ["allow", "ask", "deny"].includes(value)) {
        rules.push({
          action, effect: value as PermissionRuleDto["effect"], resource: null, order: order++,
        });
      } else if (typeof value === "object" && value !== null) {
        for (const [pattern, effect] of Object.entries(value)) {
          if (typeof effect === "string" && ["allow", "ask", "deny"].includes(effect)) {
            rules.push({
              action, effect: effect as PermissionRuleDto["effect"], resource: pattern,
              order: order++,
            });
          } else {
            // Unknown nested value: preserve rawValue for round-trip.
            rules.push({
              action, effect: "allow", resource: pattern, order: order++, rawValue: effect,
            });
          }
        }
      } else {
        // Unknown scalar: preserve rawValue.
        rules.push({
          action, effect: "allow", resource: null, order: order++, rawValue: value,
        });
      }
    }
    return rules;
  }
  return [];
}

function formDiffers(form: AgentEditorState, agent: OpenCodeAgentDto): boolean {
  const original = formFromAgent(agent);
  return (
    form.description !== original.description ||
    form.instructions !== original.instructions ||
    form.model !== original.model ||
    (form.variant || "") !== (original.variant || "") ||
    form.mode !== original.mode ||
    form.name !== original.name
  );
}

function fieldsPayload(
  form: AgentEditorState,
  isCreate: boolean,
  originalName: string | undefined,
  includePermissions: boolean,
  touched: Set<string> = new Set(),
): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    description: form.description || null,
    instructions: form.instructions || null,
  };
  if (isCreate) {
    payload.name = form.name.trim();
    payload.model = form.model || null;
    payload.variant = form.variant || null;
    payload.permissionRules = form.permissionRules; // Create sends all rules.
    return payload;
  }
  if (originalName && form.name.trim() !== originalName) {
    payload.renameTo = form.name.trim();
  }
  // Only send mode/model/variant when the user actually changed them; omitted
  // values must not be materialised by an unrelated edit.
  if (touched.has("mode")) {
    payload.mode = form.mode;
  }
  if (touched.has("model")) {
    payload.model = form.model || null;
  }
  if (touched.has("variant")) {
    payload.variant = form.variant || null;
  }
  // Only include permissionRules when the user actually changed them, so
  // unrelated edits do not rewrite the permission block.
  if (includePermissions) {
    payload.permissionRules = form.permissionRules;
  }
  return payload;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function editorTitle(
  copy: ReturnType<typeof useAgentsCopy>,
  mode: EditorMode,
): string {
  return mode.kind === "create"
    ? copy.editor.createTitle
    : copy.editor.editTitle(mode.agent.name);
}
