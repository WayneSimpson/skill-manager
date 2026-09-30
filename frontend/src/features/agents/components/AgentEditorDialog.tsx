import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useMemo, useState } from "react";

import { LoadingSpinner } from "../../../components/LoadingSpinner";
import { useAgentsCopy } from "../i18n";
import type { OpenCodeAgentDto } from "../api/types";
import {
  useAgentEditorContextQuery,
  useCreateAgentMutation,
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
  const isCreate = mode.kind === "create";

  const [form, setForm] = useState<AgentEditorState>(emptyForm());
  const [step, setStep] = useState<"fields" | "preview">("fields");
  const [error, setError] = useState("");
  const [savedNote, setSavedNote] = useState(false);
  const [variantOptions, setVariantOptions] = useState<string[]>([]);

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
      setForm(
        mode.kind === "create" ? emptyForm() : formFromAgent(mode.agent),
      );
      setStep("fields");
      setError("");
      setSavedNote(false);
      setVariantOptions([]);
      return;
    }
    setSavedNote(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, targetKey]);

  useEffect(() => {
    let cancelled = false;
    if (!form.model.trim()) {
      setVariantOptions([]);
      return;
    }
    void (async () => {
      const { fetchAgentVariantOptions } = await import("../api/client");
      const options = await fetchAgentVariantOptions(form.model.trim());
      if (!cancelled) {
        setVariantOptions(options);
      }
    })().catch(() => setVariantOptions([]));
    return () => {
      cancelled = true;
    };
  }, [form.model]);

  const dirty = useMemo(
    () => isCreate
      ? Boolean(form.name.trim())
      : mode.kind === "edit" && formDiffers(form, mode.agent),
    [form, isCreate, mode],
  );

  const sourceHash = contextQuery.data?.sourceHash ?? "";

  function update(patch: Partial<AgentEditorState>) {
    setForm((current) => ({ ...current, ...patch }));
    setSavedNote(false);
  }

  async function buildPreview() {
    setError("");
    try {
      if (isCreate) {
        await previewCreate.mutateAsync({
          generation: mode.generation,
          fields: fieldsPayload(form, true),
        });
      } else if (mode.kind === "edit") {
        await previewUpdate.mutateAsync({
          name: mode.agent.name,
          generation: mode.generation,
          fields: fieldsPayload(form, false, mode.agent.name),
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
            fields: fieldsPayload(form, true),
            expectedSourceHash: sourceHash,
          })
        : await updateMutation.mutateAsync({
            name: (mode as { agent: OpenCodeAgentDto }).agent.name,
            generation: (mode as { generation: "v1" | "v2" | null }).generation,
            fields: fieldsPayload(form, false, (mode as { agent: OpenCodeAgentDto }).agent.name),
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
                  disabled={!isCreate && form.name === "" /* rename handled below */}
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
              <label>
                <span>{copy.editor.fields.model}</span>
                <input
                  value={form.model}
                  list="agent-variant-model-options"
                  onChange={(event) => update({ model: event.target.value })}
                />
              </label>
              <label>
                <span>{copy.editor.fields.variant}</span>
                <input
                  value={form.variant}
                  list="agent-variant-options"
                  placeholder={copy.editor.fields.variantFree}
                  onChange={(event) => update({ variant: event.target.value })}
                />
                <datalist id="agent-variant-options">
                  {variantOptions.map((option) => (
                    <option key={option} value={option} />
                  ))}
                </datalist>
              </label>
              <label>
                <span>{copy.editor.fields.mode}</span>
                <select
                  value={form.mode}
                  disabled={isCreate}
                  onChange={(event) => update({ mode: event.target.value })}
                >
                  {(isCreate ? ["subagent"] : ["subagent", "primary", "all"]).map((value) => (
                    <option key={value} value={value}>
                      {copy.editor.modes[value as "subagent"]}
                    </option>
                  ))}
                </select>
                {isCreate ? (
                  <em className="agent-editor__hint">{copy.editor.subagentLocked}</em>
                ) : null}
              </label>
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
  return { name: "", description: "", instructions: "", model: "", variant: "", mode: "subagent" };
}

function formFromAgent(agent: OpenCodeAgentDto): AgentEditorState {
  return {
    name: agent.name,
    description: agent.description ?? "",
    instructions: agent.instructions ?? "",
    model: agent.model ?? "",
    variant: agent.variant ?? "",
    mode: agent.mode ?? "subagent",
  };
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

function fieldsPayload(form: AgentEditorState, isCreate: boolean, originalName?: string) {
  const payload: Record<string, string | null> = {
    description: form.description || null,
    instructions: form.instructions || null,
    model: form.model || null,
    variant: form.variant || null,
  };
  if (isCreate) {
    payload.name = form.name.trim();
  } else if (originalName && form.name.trim() !== originalName) {
    payload.renameTo = form.name.trim();
  }
  if (!isCreate) {
    payload.mode = form.mode;
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
