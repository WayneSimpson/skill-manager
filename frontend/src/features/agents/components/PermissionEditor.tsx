import { useMemo } from "react";

import type { ModelCatalogueDto, PermissionRuleDto } from "../api/types";
import { useAgentsCopy } from "../i18n";

interface PermissionEditorProps {
  generation: "v1" | "v2";
  rules: PermissionRuleDto[];
  onChange: (rules: PermissionRuleDto[]) => void;
  catalogue?: ModelCatalogueDto;
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

export function PermissionEditor({
  generation,
  rules,
  onChange,
  disabled,
}: PermissionEditorProps) {
  const copy = useAgentsCopy();

  const { commonActions, advancedRules } = useMemo(() => {
    const common = new Set(
      Object.keys(COMMON_ACTION_LABELS)
        .map((action) => COMMON_ACTION_LABELS[action][generation])
        .filter(Boolean),
    );
    const commonList: { action: string; label: string; effect: string }[] = [];
    const advanced: PermissionRuleDto[] = [];
    const seen = new Set<string>();

    for (const rule of rules) {
      const label = COMMON_ACTION_LABELS[rule.action]?.[generation];
      const key = `${rule.action}:${rule.resource ?? ""}`;
      if (label && !rule.resource && !seen.has(rule.action)) {
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
    return { commonActions: commonList, advancedRules: advancedIndexed };
  }, [rules, generation]);

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

  function updateAdvanced(sourceIndex: number, patch: Partial<PermissionRuleDto>) {
    onChange(rules.map((r, i) => (i === sourceIndex ? { ...r, ...patch } : r)));
  }

  function removeAdvanced(sourceIndex: number) {
    onChange(rules.filter((_, i) => i !== sourceIndex));
  }

  function addAdvanced() {
    onChange([...rules, { action: "", effect: "allow", resource: "" }]);
  }

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
                updateAdvanced(sourceIndex, { resource: event.target.value || null })
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
