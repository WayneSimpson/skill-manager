import { useId, useMemo, useRef, useState } from "react";

import type { ModelCatalogueDto, ModelCatalogueEntryDto } from "../api/types";
import { useAgentsCopy } from "../i18n";

interface ModelPickerProps {
  value: string;
  onChange: (value: string) => void;
  catalogue: ModelCatalogueDto | undefined;
  disabled?: boolean;
}

const CUSTOM_PREFIX = "__custom__:";

type FlatOption = { value: string; kind: "inherit" | "custom" | "model" };

export function ModelPicker({ value, onChange, catalogue, disabled }: ModelPickerProps) {
  const copy = useAgentsCopy();
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(-1);
  const rootRef = useRef<HTMLDivElement>(null);
  const listId = useId();

  const { grouped, isCustom, customLabel, flat } = useMemo(() => {
    if (!catalogue?.providers?.length) {
      return { grouped: [], isCustom: false, customLabel: "", flat: [] as FlatOption[] };
    }
    const needle = search.trim().toLowerCase();
    // Sort connected providers first (backend already sorts, but be defensive).
    const sorted = [...catalogue.providers].sort((a, b) => {
      const aConnected = a.connected === true ? 0 : 1;
      const bConnected = b.connected === true ? 0 : 1;
      return aConnected - bConnected;
    });
    const groups: { provider: string; models: ModelCatalogueEntryDto[] }[] = [];
    for (const provider of sorted) {
      const models = provider.models.filter(
        (m) =>
          !needle ||
          m.id.toLowerCase().includes(needle) ||
          m.name.toLowerCase().includes(needle) ||
          provider.name.toLowerCase().includes(needle),
      );
      if (models.length > 0) {
        groups.push({ provider: provider.name, models });
      }
    }
    const inCatalogue = catalogue.providers.some((p) =>
      p.models.some((m) => m.id === value),
    );
    // Keyboard traversal order matches the rendered list exactly: Inherit,
    // then the explicit custom-model action, then grouped catalogue models.
    const options: FlatOption[] = [{ value: "", kind: "inherit" }];
    if (search.trim()) options.push({ value: search.trim(), kind: "custom" });
    for (const group of groups) {
      for (const model of group.models.slice(0, 200)) {
        options.push({ value: model.id, kind: "model" });
      }
    }
    return {
      grouped: groups,
      isCustom: Boolean(value) && !inCatalogue,
      customLabel: value,
      flat: options,
    };
  }, [catalogue, search, value]);

  if (disabled) {
    return <input className="agents-editor__input" value={value} disabled />;
  }

  // Browsers move focus (and fire input blur) DURING mousedown, before the
  // click event dispatches. Preventing default on the option's mousedown keeps
  // focus on the search input, so the dropdown stays mounted and the click
  // reliably reaches the option handler. The blur handler additionally only
  // closes when focus is genuinely leaving the picker (relatedTarget outside),
  // covering keyboard and programmatic focus moves.
  function select(value_: string) {
    onChange(value_);
    setSearch("");
    setHighlight(-1);
    setOpen(false);
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      if (flat.length === 0) return;
      setHighlight((current) => {
        if (current === -1) return event.key === "ArrowDown" ? 0 : flat.length - 1;
        return event.key === "ArrowDown"
          ? Math.min(current + 1, flat.length - 1)
          : Math.max(current - 1, 0);
      });
      return;
    }
    if (event.key === "Enter") {
      // Enter commits ONLY an explicitly highlighted option; committing raw
      // search text would blur the search/selection distinction.
      if (open && highlight >= 0 && highlight < flat.length) {
        event.preventDefault();
        select(flat[highlight].value);
      }
      return;
    }
    if (event.key === "Escape" && open) {
      event.preventDefault();
      setOpen(false);
      setHighlight(-1);
    }
  }

  function optionProps(index: number) {
    return {
      id: `${listId}-option-${index}`,
      // Keep the highlighted option visible in the scrolling dropdown.
      ref: (node: HTMLButtonElement | null) => {
        if (node && index === highlight && typeof node.scrollIntoView === "function") {
          node.scrollIntoView({ block: "nearest" });
        }
      },
    };
  }

  let optionIndex = -1;

  return (
    <div className="model-picker" ref={rootRef}>
      <input
        type="text"
        className="agents-editor__input"
        value={open ? search : value || ""}
        placeholder={copy.editor.modelPicker.placeholder}
        onFocus={() => {
          setOpen(true);
          setSearch("");
          setHighlight(-1);
        }}
        onBlur={(event) => {
          const next = event.relatedTarget as Node | null;
          if (next && rootRef.current?.contains(next)) return; // Focus within picker.
          setOpen(false);
          // Search text is search text, NOT a model selection.
        }}
        onChange={(event) => {
          setSearch(event.target.value);
          setHighlight(-1); // The filtered list changed; re-highlight explicitly.
        }}
        onKeyDown={handleKeyDown}
        aria-label={copy.editor.fields.model}
        aria-expanded={open}
        aria-controls={open ? `${listId}-listbox` : undefined}
        aria-activedescendant={open && highlight >= 0 ? `${listId}-option-${highlight}` : undefined}
      />
      {isCustom ? (
        <p className="model-picker__custom">{copy.editor.modelPicker.customValue(customLabel)}</p>
      ) : null}
      {open ? (
        <div className="model-picker__dropdown ui-scrollbar" role="listbox" id={`${listId}-listbox`}>
          {(() => {
            optionIndex += 1;
            const index = optionIndex;
            return (
              <button
                {...optionProps(index)}
                type="button"
                className={`model-picker__option model-picker__option--inherit${index === highlight ? " is-highlighted" : ""}`}
                role="option"
                aria-selected={value === ""}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => select("")}
              >
                {copy.editor.modelPicker.inherit}
              </button>
            );
          })()}
          {catalogue?.source === "unavailable" ? (
            <p className="model-picker__unavailable">{copy.editor.modelPicker.unavailable}</p>
          ) : null}
          {search.trim() ? (
            (() => {
              optionIndex += 1;
              const index = optionIndex;
              return (
                <button
                  {...optionProps(index)}
                  type="button"
                  className={`model-picker__option model-picker__option--custom${index === highlight ? " is-highlighted" : ""}`}
                  role="option"
                  aria-selected={false}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => select(search.trim())}
                >
                  {copy.editor.modelPicker.useCustom?.(search.trim()) ?? `Use custom model ID: ${search.trim()}`}
                </button>
              );
            })()
          ) : null}
          {grouped.map(({ provider, models }) => (
            <div key={provider} className="model-picker__group">
              <div className="model-picker__group-label">{provider}</div>
              {models.slice(0, 200).map((model) => {
                optionIndex += 1;
                const index = optionIndex;
                return (
                  <button
                    key={model.id}
                    {...optionProps(index)}
                    type="button"
                    className={`model-picker__option${value === model.id ? " is-selected" : ""}${index === highlight ? " is-highlighted" : ""}`}
                    role="option"
                    aria-selected={value === model.id}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => select(model.id)}
                  >
                    <span className="model-picker__name">{model.name}</span>
                    <span className="model-picker__id">{model.id}</span>
                    {model.variants.length > 0 ? (
                      <span className="model-picker__variants">
                        {model.variants.length} variants
                      </span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
