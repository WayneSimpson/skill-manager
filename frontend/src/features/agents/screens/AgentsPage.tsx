import { useMemo, useState } from "react";

import { ErrorBanner } from "../../../components/ErrorBanner";
import { LoadingSpinner } from "../../../components/LoadingSpinner";
import { PageHeader } from "../../../components/PageHeader";
import { useAgentsCopy } from "../i18n";
import { useOpenCodeAgentsQuery } from "../api/queries";
import type { OpenCodeAgentDto } from "../api/types";

export default function AgentsPage() {
  const query = useOpenCodeAgentsQuery();
  const copy = useAgentsCopy();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

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
        <PageHeader title={copy.title} subtitle={copy.subtitle} />
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
                    <span className="agents-badge">{copy.readOnlyBadge}</span>
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
            <AgentDetail agent={selectedAgent} onClose={() => setSelected(null)} />
          ) : null}
        </div>
      )}

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

function AgentDetail({ agent, onClose }: { agent: OpenCodeAgentDto; onClose: () => void }) {
  const copy = useAgentsCopy();
  const additionalEntries = Object.entries(agent.additionalOptions ?? {});

  return (
    <aside className="agents-detail ui-scrollbar" aria-label={copy.detailTitle(agent.name)}>
      <header className="agents-detail__header">
        <div>
          <h3 className="agents-detail__name">{agent.name}</h3>
          <span className="agents-badge">
            {agent.schemaGeneration === "v2" ? copy.generationV2 : copy.generationV1}
          </span>
          <span className="agents-badge">{copy.readOnlyBadge}</span>
          {!agent.valid ? (
            <span className="agents-badge agents-badge--warn">{copy.invalidBadge}</span>
          ) : null}
        </div>
        <button type="button" className="agents-detail__close" onClick={onClose}>
          {copy.closeDetail}
        </button>
      </header>

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
