import { useMemo } from "react";

import type { McpServerDto, ModelCatalogueDto, PermissionRuleDto } from "../api/types";
import { useAgentsCopy } from "../i18n";

interface PermissionEditorProps {
  generation: "v1" | "v2";
  rules: PermissionRuleDto[];
  onChange: (rules: PermissionRuleDto[]) => void;
  catalogue?: ModelCatalogueDto;
  /** Discovered MCP servers; when present, server-wide wildcard rules become first-class rows. */
  mcpServers?: McpServerDto[];
  /** Discovery source ("runtime" | "config" | "unavailable") for the hint line. */
  mcpSource?: string;
  disabled?: boolean;
}

const COMMON_ACTION_LABELS: Record<string, { v1: string; v2: string }> = {
  read: { v1: "Read files", v2: "Read files" },
  edit: { v1: "Edit / write files", v2: "Edit / write files" },
  bash: { v1: "Shell / bash commands", v2: "" },
  shell: { v1: "", v2: "Shell commands" },
  glob: { v1: "File search (glob)", v2: "File search (glob)" },
  grep: { v1: "Content search (grep)", v2: "Content search (grep)" },
  list: { v1: "List directory", v2: "List directory" },
  task: { v1: "Launch sub-agents (task)", v2: "" },
  subagent: { v1: "", v2: "Launch sub-agents" },
  webfetch: { v1: "Fetch web pages", v2: "Fetch web pages" },
  websearch: { v1: "Search the web", v2: "Search the web" },
  skill: { v1: "Load skills", v2: "Load skills" },
  external_directory: { v1: "Access external directories", v2: "Access external directories" },
  question: { v1: "Ask user questions", v2: "Ask user questions" },
  todowrite: { v1: "Manage todos", v2: "Manage todos" },
};

const EFFECTS = ["inherit", "allow", "ask", "deny"] as const;

/**
 * Server-wide wildcard match, mirroring OpenCode's documented per-generation
 * semantics: V1 server-wide rules carry no resource, V2 server-wide rules
 * carry resource "*" ({"action":"context7_*","resource":"*"}). The action must
 * be the canonical `{name}_*` form or the legacy no-underscore `{name}*` form.
 * Exact per-tool rules and other resource-scoped rules do NOT match
 * (they stay in Advanced).
 */
function ruleMatchesServer(
  rule: PermissionRuleDto,
  server: McpServerDto,
  generation: "v1" | "v2",
): boolean {
  if (rule.rawValue !== undefined) return false;
  const expectedResource = generation === "v2" ? "*" : null;
  if (rule.resource !== expectedResource) return false;
  return rule.action === server.wildcard || rule.action === `${server.name}*`;
}

export function PermissionEditor({
  generation,
  rules,
  onChange,
  mcpServers,
  mcpSource,
  disabled,
}: PermissionEditorProps) {
  const copy = useAgentsCopy();

  const { commonActions, advancedRules, mcpRows } = useMemo(() => {
    const common = new Set(
      Object.keys(COMMON_ACTION_LABELS)
        .map((action) => COMMON_ACTION_LABELS[action][generation])
        .filter(Boolean),
    );
    const hasMcp = Array.isArray(mcpServers) && mcpServers.length > 0;
    const commonList: { action: string; label: string; effect: string }[] = [];
    const advanced: PermissionRuleDto[] = [];
    const seen = new Set<string>();
    // Server name → matched rules (first match drives the row's effect).
    const mcpMatches = new Map<string, PermissionRuleDto[]>();

    for (const rule of rules) {
      const label = COMMON_ACTION_LABELS[rule.action]?.[generation];
      const key = `${rule.action}:${rule.resource ?? ""}`;
      const mcpServer = hasMcp
        ? mcpServers.find((server) => ruleMatchesServer(rule, server, generation))
        : undefined;
      if (mcpServer) {
        const list = mcpMatches.get(mcpServer.name) ?? [];
        list.push(rule);
        mcpMatches.set(mcpServer.name, list);
      } else if (label && !rule.resource && !seen.has(rule.action)) {
        commonList.push({ action: rule.action, label, effect: rule.effect });
        seen.add(rule.action);
      } else if (!common.has(rule.action) || rule.resource || seen.has(key)) {
        advanced.push(rule);
      } else if (label && !seen.has(rule.action)) {
        commonList.push({ action: rule.action, label, effect: rule.effect });
        seen.add(rule.action);
      }
    }
    // Ensure all common actions appear even when not set (inherit default).
    for (const [action, labels] of Object.entries(COMMON_ACTION_LABELS)) {
      const label = labels[generation];
      if (label && !seen.has(action)) {
        commonList.push({ action, label, effect: "inherit" });
      }
    }
    // Track the source index of each advanced rule for correct edit/remove.
    const advancedIndexed = advanced.map((rule) => ({
      rule,
      sourceIndex: rules.indexOf(rule),
    }));
    // One row per discovered server; new agents default to inherit.
    const rows = (mcpServers ?? []).map((server) => ({
      server,
      // Inherit markers on the wire render as Inherit, not the marker effect.
      effect: (mcpMatches.get(server.name) ?? []).find((r) => r.effect !== "inherit")?.effect
        ?? "inherit",
    }));
    return { commonActions: commonList, advancedRules: advancedIndexed, mcpRows: rows };
  }, [rules, generation, mcpServers]);

  function setEffect(action: string, effect: PermissionRuleDto["effect"]) {
    const existing = rules.find((r) => r.action === action && !r.resource);
    if (effect === "inherit" && existing) {
      // Keep the rule with effect:"inherit" as an explicit mutation intent so
      // the backend removes the targeted override from the original config.
      onChange(rules.map((r) => (r.action === action && !r.resource ? { ...r, effect } : r)));
    } else if (effect === "inherit") {
      // No existing rule: inherit is the default, nothing to send.
      onChange(rules.filter((r) => !(r.action === action && !r.resource)));
    } else if (existing) {
      onChange(rules.map((r) => (r.action === action && !r.resource ? { ...r, effect } : r)));
    } else {
      onChange([...rules, { action, effect: effect as PermissionRuleDto["effect"], resource: null }]);
    }
  }

  function setMcpEffect(server: McpServerDto, effect: PermissionRuleDto["effect"]) {
    const matches = rules.filter((rule) => ruleMatchesServer(rule, server, generation));
    if (effect === "inherit") {
      if (matches.length > 0) {
        // Explicit inherit markers so the backend removes ONLY the exact
        // wildcard overrides for this server (legacy action form and the
        // generation-native resource form preserved).
        const matched = new Set(matches);
        onChange(rules.map((rule) =>
          matched.has(rule) ? { ...rule, effect: "inherit" as const } : rule));
      } else {
        onChange(rules.filter((rule) => !ruleMatchesServer(rule, server, generation)));
      }
      return;
    }
    if (matches.length > 0) {
      // Update the EXISTING rule(s) in place — spread preserves the action
      // (including legacy forms), resource, and V2 order.
      const matched = new Set(matches);
      onChange(rules.map((rule) => (matched.has(rule) ? { ...rule, effect } : rule)));
      return;
    }
    // New override: append with the canonical wildcard derived server-side,
    // using the generation-native server-wide resource form.
    const nextOrder = rules.reduce((max, rule) => Math.max(max, rule.order ?? 0), -1) + 1;
    onChange([
      ...rules,
      {
        action: server.wildcard,
        effect: effect as PermissionRuleDto["effect"],
        resource: generation === "v2" ? "*" : null,
        order: nextOrder,
      },
    ]);
  }

  function updateAdvanced(sourceIndex: number, patch: Partial<PermissionRuleDto>) {
    onChange(rules.map((r, i) => (i === sourceIndex ? { ...r, ...patch } : r)));
  }

  function removeAdvanced(sourceIndex: number) {
    onChange(rules.filter((_, i) => i !== sourceIndex));
  }

  function addAdvanced() {
    onChange([...rules, { action: "", effect: "allow", resource: "" }]);
  }

  const mcpCopy = copy.editor.permissions.mcp;

  return (
    <div className="permission-editor">
      <table className="permission-editor__table" aria-label={copy.editor.permissions.title}>
        <thead>
          <tr>
            <th>{copy.editor.permissions.capability}</th>
            <th>{copy.editor.permissions.effect}</th>
          </tr>
        </thead>
        <tbody>
          {commonActions.map(({ action, label, effect }) => (
            <tr key={action}>
              <td>{label}</td>
              <td>
                <select
                  value={effect}
                  disabled={disabled}
                  onChange={(event) =>
                  setEffect(action, event.target.value as PermissionRuleDto["effect"])}
                  aria-label={label}
                >
                  {EFFECTS.map((e) => (
                    <option key={e} value={e}>
                      {copy.editor.permissions.effects[e]}
                    </option>
                  ))}
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {mcpServers ? (
        <div className="permission-editor__mcp">
          <h4 className="permission-editor__mcp-title">{mcpCopy.title}</h4>
          {mcpSource ? (
            <p className="permission-editor__hint">
              {mcpCopy.sourceHint[mcpSource] ?? mcpCopy.sourceHint.unavailable}
            </p>
          ) : null}
          {mcpServers.length > 0 ? (
            <table className="permission-editor__table" aria-label={mcpCopy.title}>
              <thead>
                <tr>
                  <th>{mcpCopy.server}</th>
                  <th>{mcpCopy.statusLabel}</th>
                  <th>{mcpCopy.wildcardLabel}</th>
                  <th>{copy.editor.permissions.effect}</th>
                </tr>
              </thead>
              <tbody>
                {mcpRows.map(({ server, effect }) => (
                  <tr key={server.name}>
                    <td>{server.name}</td>
                    <td>
                      <span
                        className={`permission-editor__mcp-status is-${server.status}`}
                        title={server.error ?? undefined}
                      >
                        {mcpCopy.statuses[server.status] ?? server.status}
                        {server.error ? " ⚠" : ""}
                      </span>
                    </td>
                    <td>
                      <code>{server.wildcard}</code>
                    </td>
                    <td>
                      <select
                        value={effect}
                        disabled={disabled}
                        onChange={(event) =>
                          setMcpEffect(server, event.target.value as PermissionRuleDto["effect"])}
                        aria-label={`${mcpCopy.server} ${server.name}`}
                      >
                        {EFFECTS.map((e) => (
                          <option key={e} value={e}>
                            {mcpCopy.effects[e]}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : mcpSource ? null : (
            <p className="permission-editor__hint">{mcpCopy.sourceHint.unavailable}</p>
          )}
        </div>
      ) : null}

      <details className="permission-editor__advanced">
        <summary>{copy.editor.permissions.advanced}</summary>
        <p className="permission-editor__hint">{copy.editor.permissions.advancedHint}</p>
        {advancedRules.map(({ rule, sourceIndex }) => (
          <div key={sourceIndex} className="permission-editor__rule">
            <input
              placeholder={copy.editor.permissions.actionPlaceholder}
              value={rule.action}
              disabled={disabled}
              onChange={(event) => updateAdvanced(sourceIndex, { action: event.target.value })}
              aria-label={`${copy.editor.permissions.actionPlaceholder} ${sourceIndex + 1}`}
            />
            <input
              placeholder={copy.editor.permissions.resourcePlaceholder}
              value={rule.resource ?? ""}
              disabled={disabled}
              onChange={(event) =>
                updateAdvanced(sourceIndex, {
                  resource: event.target.value || null,
                })
              }
              aria-label={`${copy.editor.permissions.resourcePlaceholder} ${sourceIndex + 1}`}
            />
            <select
              value={rule.effect}
              disabled={disabled}
              onChange={(event) =>
                updateAdvanced(sourceIndex, {
                  effect: event.target.value as PermissionRuleDto["effect"],
                })
              }
              aria-label={`${copy.editor.permissions.effect} ${sourceIndex + 1}`}
            >
              {["allow", "ask", "deny"].map((e) => (
                <option key={e} value={e}>
                  {copy.editor.permissions.effects[e]}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="permission-editor__remove"
              disabled={disabled}
              onClick={() => removeAdvanced(sourceIndex)}
              aria-label={`${copy.editor.permissions.remove} ${sourceIndex + 1}`}
            >
              ×
            </button>
          </div>
        ))}
        <button
          type="button"
          className="btn"
          disabled={disabled}
          onClick={addAdvanced}
        >
          {copy.editor.permissions.addRule}
        </button>
      </details>
    </div>
  );
}
