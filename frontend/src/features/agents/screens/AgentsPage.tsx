import * as Dialog from "@radix-ui/react-dialog";
import { useMemo, useState } from "react";

import { ErrorBanner } from "../../../components/ErrorBanner";
import { LoadingSpinner } from "../../../components/LoadingSpinner";
import { PageHeader } from "../../../components/PageHeader";
import { AgentEditorDialog } from "../components/AgentEditorDialog";
import { useAgentsCopy } from "../i18n";
import {
  useOpenCodeAgentsQuery,
  useAgentEditorContextQuery,
  useAgentApplyCapabilityQuery,
  useAgentApplyStatusQuery,
} from "../api/queries";
import { ApplyDialog } from "../components/ApplyDialog";
import type { OpenCodeAgentDto } from "../api/types";

type EditorTarget =
  | { kind: "create"; generation: "v1" | "v2" }
  | { kind: "edit"; agent: OpenCodeAgentDto; generation: "v1" | "v2" | null };

type CreateChoice =
  | { kind: "direct"; generation: "v1" | "v2" }
  | { kind: "choose" };

export default function AgentsPage() {
  const query = useOpenCodeAgentsQuery();
  const contextQuery = useAgentEditorContextQuery();
  const applyCapabilityQuery = useAgentApplyCapabilityQuery();
  const applyStatusQuery = useAgentApplyStatusQuery();
  const copy = useAgentsCopy();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorTarget | null>(null);
  const [createChoice, setCreateChoice] = useState<CreateChoice | null>(null);
  const [applyOpen, setApplyOpen] = useState(false);
  const pendingApply = applyStatusQuery.data?.pending ?? false;

  const agents = query.data?.agents ?? [];
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) {
      return agents;
    }
    return agents.filter((agent) =>
      [agent.name, agent.description ?? "", agent.model ?? ""]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  }, [agents, search]);

  const selectedAgent =
    agents.find(
      (agent) => selected !== null && `${agent.name}:${agent.schemaGeneration}` === selected,
    ) ?? filtered[0] ?? null;

  return (
    <>
      <div className="page-chrome">
        <PageHeader
          title={copy.title}
          subtitle={copy.subtitle}
          actions={
            <div className="page-header__actions agents-header-actions">
            <button
              type="button"
              className="action-pill action-pill--md action-pill--accent"
              disabled={contextQuery.isPending}
              onClick={() => {
                const create = contextQuery.data?.create;
                if (create?.targetGeneration) {
                  // Deterministic from the effective config: no choice needed.
                  setEditor({ kind: "create", generation: create.targetGeneration });
                } else {
                  // Truly ambiguous or fresh config: explicit syntax choice.
                  setCreateChoice({ kind: "choose" });
                }
              }}
            >
              {copy.newSubagent}
            </button>
            {pendingApply ? (
              <button
                type="button"
                className="action-pill action-pill--md"
                onClick={() => setApplyOpen(true)}
              >
                {copy.applyAction}
              </button>
            ) : null}
            </div>
          }
        />
        {agents.length > 0 ? (
          <label className="agents-search">
            <span className="agents-search__label">{copy.searchLabel}</span>
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={copy.searchPlaceholder}
              className="agents-search__input"
            />
          </label>
        ) : null}
      </div>

      {query.error ? (
        <ErrorBanner message={query.error instanceof Error ? query.error.message : copy.unableToLoad} />
      ) : null}

      {query.isPending ? (
        <div className="panel-state">
          <LoadingSpinner label={copy.loading} />
        </div>
      ) : agents.length === 0 ? (
        <div className="panel-state agents-empty">
          <h3>{copy.emptyTitle}</h3>
          <p>{copy.emptyBody}</p>
        </div>
      ) : (
        <div className="agents-layout">
          <ul className="agents-list" aria-label={copy.listAria}>
            {filtered.map((agent) => (
              <li key={`${agent.name}:${agent.schemaGeneration}`}>
                <button
                  type="button"
                  className={`agents-list__item${
                    selectedAgent && `${selectedAgent.name}:${selectedAgent.schemaGeneration}` === `${agent.name}:${agent.schemaGeneration}`
                      ? " is-selected"
                      : ""
                  }`}
                  onClick={() => setSelected(`${agent.name}:${agent.schemaGeneration}`)}
                  aria-pressed={
                    selectedAgent !== null &&
                    `${selectedAgent.name}:${selectedAgent.schemaGeneration}` ===
                      `${agent.name}:${agent.schemaGeneration}`
                  }
                >
                  <span className="agents-list__name">{agent.name}</span>
                  <span className="agents-list__meta">
                    {agent.valid
                      ? [agent.model, agent.variant].filter(Boolean).join(" · ") || agent.mode || ""
                      : copy.invalidBadge}
                  </span>
                  <span className="agents-list__badges">
                    <span className="agents-badge">
                      {agent.schemaGeneration === "v2" ? copy.generationV2 : copy.generationV1}
                    </span>
                    {agent.readOnly ? <span className="agents-badge">{copy.readOnlyBadge}</span> : null}
                    {!agent.valid ? <span className="agents-badge agents-badge--warn">{copy.invalidBadge}</span> : null}
                  </span>
                  {agent.description ? (
                    <span className="agents-list__description">{agent.description}</span>
                  ) : null}
                  <span className="agents-sr-only">{copy.select(agent.name)}</span>
                </button>
              </li>
            ))}
          </ul>
          {selectedAgent ? (
            <AgentDetail
              agent={selectedAgent}
              onClose={() => setSelected(null)}
              onEdit={(agent) => {
                const ambiguous = agent.readOnlyReasons.includes("defined-in-both-v1-and-v2-sections");
                setEditor({
                  kind: "edit",
                  agent,
                  generation: ambiguous ? agent.schemaGeneration : null,
                });
              }}
              savedPendingApply={pendingApply}
            />
          ) : null}
        </div>
      )}

      {applyOpen ? (
        <ApplyDialog
          open={applyOpen}
          capability={applyCapabilityQuery.data}
          onOpenChange={setApplyOpen}
        />
      ) : null}

      {createChoice ? (
        <GenerationChoiceDialog
          onChoose={(generation) => {
            setCreateChoice(null);
            setEditor({ kind: "create", generation });
          }}
          onClose={() => setCreateChoice(null)}
        />
      ) : null}

      {editor ? (
        <AgentEditorDialog
          open={editor !== null}
          mode={editor}
          onOpenChange={(open) => {
            if (!open) {
              setEditor(null);
            }
          }}
          onSaved={() => {
            void applyStatusQuery.refetch();
          }}
        />
      ) : null}

      {query.data ? (
        <p className="agents-limitation">
          {copy.limitationNote}
          <br />
          <span className="agents-sr-only">{copy.writeTarget(query.data.writeTarget)}</span>
        </p>
      ) : null}
    </>
  );
}

function AgentDetail({
  agent,
  onClose,
  onEdit,
  savedPendingApply,
}: {
  agent: OpenCodeAgentDto;
  onClose: () => void;
  onEdit: (agent: OpenCodeAgentDto) => void;
  savedPendingApply: boolean;
}) {
  const copy = useAgentsCopy();
  const additionalEntries = Object.entries(agent.additionalOptions ?? {});
  const editable = agent.valid && !agent.readOnly;

  return (
    <aside className="agents-detail ui-scrollbar" aria-label={copy.detailTitle(agent.name)}>
      <header className="agents-detail__header">
        <div>
          <h3 className="agents-detail__name">{agent.name}</h3>
          <span className="agents-badge">
            {agent.schemaGeneration === "v2" ? copy.generationV2 : copy.generationV1}
          </span>
          {agent.readOnly ? <span className="agents-badge">{copy.readOnlyBadge}</span> : null}
          {!agent.valid ? (
            <span className="agents-badge agents-badge--warn">{copy.invalidBadge}</span>
          ) : null}
        </div>
        <div className="agents-detail__header-actions">
          {editable ? (
            <button
              type="button"
              className="action-pill action-pill--sm"
              onClick={() => onEdit(agent)}
            >
              {copy.edit}
            </button>
          ) : null}
          <button type="button" className="agents-detail__close" onClick={onClose}>
            {copy.closeDetail}
          </button>
        </div>
      </header>

      {savedPendingApply ? (
        <p className="agents-detail__pending" role="status">{copy.savedPendingApply}</p>
      ) : null}

      {agent.diagnostic ? <p className="agents-detail__diagnostic">{agent.diagnostic}</p> : null}

      <dl className="agents-detail__fields">
        <Field label={copy.fields.description} value={agent.description} />
        <Field label={copy.fields.model} value={agent.model} />
        <Field label={copy.fields.variant} value={agent.variant} />
        <Field label={copy.fields.mode} value={agent.mode} />
        <Field label={copy.fields.temperature} value={formatNumber(agent.temperature)} />
        <Field label={copy.fields.topP} value={formatNumber(agent.topP)} />
        <Field label={copy.fields.steps} value={agent.steps === null ? null : String(agent.steps)} />
        <Field label={copy.fields.disabled} value={formatBoolean(agent.disabled, copy)} />
        <Field label={copy.fields.hidden} value={formatBoolean(agent.hidden, copy)} />
        <Field label={copy.fields.color} value={agent.color} />
        {agent.instructions ? (
          <div className="agents-detail__prompt">
            <dt>{copy.fields.instructions}</dt>
            <dd>
              <pre>{agent.instructions}</pre>
            </dd>
          </div>
        ) : null}
        {agent.permissions ? (
          <div className="agents-detail__prompt">
            <dt>{copy.fields.permission}</dt>
            <dd>
              <pre>{JSON.stringify(agent.permissions, null, 2)}</pre>
            </dd>
          </div>
        ) : null}
        {agent.tools ? (
          <div className="agents-detail__prompt">
            <dt>{copy.fields.tools}</dt>
            <dd>
              <pre>{JSON.stringify(agent.tools, null, 2)}</pre>
            </dd>
          </div>
        ) : null}
        {additionalEntries.length > 0 ? (
          <div className="agents-detail__prompt">
            <dt>{copy.fields.additionalOptions}</dt>
            <dd>
              <dl className="agents-detail__additional">
                {additionalEntries.map(([key, value]) => (
                  <div key={key}>
                    <dt>{key}</dt>
                    <dd>
                      <pre>{JSON.stringify(value, null, 2)}</pre>
                    </dd>
                  </div>
                ))}
              </dl>
            </dd>
          </div>
        ) : null}
      </dl>

      <section className="agents-detail__source">
        <h4>{copy.source.title}</h4>
        {agent.source ? (
          <dl>
            <Field label={copy.source.file} value={agent.source.path} mono />
            <Field label={copy.source.format} value={agent.source.format} />
            <Field
              label={copy.source.writeTarget}
              value={agent.source.isWriteTarget ? "✓" : "—"}
            />
          </dl>
        ) : null}
        <p>
          {copy.source.editability.label}:{" "}
          <strong>{copy.source.editability[agent.editability]}</strong>
        </p>
        {agent.readOnlyReasons.map((reason) => (
          <p key={reason} className="agents-detail__reason">
            {reason === "declared-outside-selected-config-source"
              ? copy.source.reasonOutsideWriteTarget
              : reason === "definition-not-an-object"
                ? copy.source.reasonNotObject
                : reason === "defined-in-both-v1-and-v2-sections"
                  ? copy.source.reasonBothGenerations
                  : reason}
          </p>
        ))}
      </section>
    </aside>
  );
}

function Field({ label, value, mono }: { label: string; value: string | null; mono?: boolean }) {
  const copy = useAgentsCopy();
  return (
    <div className={mono ? "agents-detail__field agents-detail__field--mono" : "agents-detail__field"}>
      <dt>{label}</dt>
      <dd>{value ?? copy.fields.notSet}</dd>
    </div>
  );
}

function formatNumber(value: number | null): string | null {
  return value === null ? null : String(value);
}

function formatBoolean(value: boolean | null, copy: ReturnType<typeof useAgentsCopy>): string | null {
  if (value === null) {
    return null;
  }
  return value ? copy.yes : copy.no;
}

function GenerationChoiceDialog({
  onChoose,
  onClose,
}: {
  onChoose: (generation: "v1" | "v2") => void;
  onClose: () => void;
}) {
  const copy = useAgentsCopy();
  return (
    <Dialog.Root open onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content
          className="dialog-content agent-editor"
          aria-label={copy.generationChoice.title}
        >
          <Dialog.Description className="dialog-description agent-generation-choice__description">
            {copy.generationChoice.description}
          </Dialog.Description>
          <div className="dialog-header">
            <Dialog.Title className="dialog-title">{copy.generationChoice.title}</Dialog.Title>
          </div>
          <div className="agent-generation-choice__options">
            <button
              type="button"
              className="btn"
              onClick={() => onChoose("v1")}
            >
              {copy.generationChoice.v1}
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => onChoose("v2")}
            >
              {copy.generationChoice.v2}
            </button>
          </div>
          <div className="dialog-actions">
            <button type="button" className="btn" onClick={onClose}>
              {copy.generationChoice.cancel}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
