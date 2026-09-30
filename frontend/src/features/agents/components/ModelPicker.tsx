import { useMemo, useState } from "react";

import type { ModelCatalogueDto, ModelCatalogueEntryDto } from "../api/types";
import { useAgentsCopy } from "../i18n";

interface ModelPickerProps {
  value: string;
  onChange: (value: string) => void;
  catalogue: ModelCatalogueDto | undefined;
  disabled?: boolean;
}

const CUSTOM_PREFIX = "__custom__:";

export function ModelPicker({ value, onChange, catalogue, disabled }: ModelPickerProps) {
  const copy = useAgentsCopy();
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);

  const { grouped, isCustom, customLabel } = useMemo(() => {
    if (!catalogue?.providers?.length) return { grouped: [], isCustom: false, customLabel: "" };
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
    return {
      grouped: groups,
      isCustom: Boolean(value) && !inCatalogue,
      customLabel: value,
    };
  }, [catalogue, search, value]);

  if (disabled) {
    return <input className="agents-editor__input" value={value} disabled />;
  }

  return (
    <div className="model-picker">
      <input
        type="text"
        className="agents-editor__input"
        value={open ? search : value || ""}
        placeholder={copy.editor.modelPicker.placeholder}
        onFocus={() => {
          setOpen(true);
          setSearch("");
        }}
        onBlur={() => {
          setOpen(false);
          // Search text is search text, NOT a model selection.
        }}
        onChange={(event) => setSearch(event.target.value)}
        aria-label={copy.editor.fields.model}
      />
      {isCustom ? (
        <p className="model-picker__custom">{copy.editor.modelPicker.customValue(customLabel)}</p>
      ) : null}
      {open ? (
        <div className="model-picker__dropdown ui-scrollbar" role="listbox">
          <button
            type="button"
            className="model-picker__option model-picker__option--inherit"
            role="option"
            aria-selected={value === ""}
            onClick={() => {
              onChange("");
              setOpen(false);
            }}
          >
            {copy.editor.modelPicker.inherit}
          </button>
          {catalogue?.source === "unavailable" ? (
            <p className="model-picker__unavailable">{copy.editor.modelPicker.unavailable}</p>
          ) : null}
          {search.trim() ? (
            <button
              type="button"
              className="model-picker__option model-picker__option--custom"
              role="option"
              aria-selected={false}
              onClick={() => {
                onChange(search.trim());
                setOpen(false);
              }}
            >
              {copy.editor.modelPicker.useCustom?.(search.trim()) ?? `Use custom model ID: ${search.trim()}`}
            </button>
          ) : null}
          {grouped.map(({ provider, models }) => (
            <div key={provider} className="model-picker__group">
              <div className="model-picker__group-label">{provider}</div>
              {models.slice(0, 200).map((model) => (
                <button
                  key={model.id}
                  type="button"
                  className={`model-picker__option${value === model.id ? " is-selected" : ""}`}
                  role="option"
                  aria-selected={value === model.id}
                  onClick={() => {
                    onChange(model.id);
                    setOpen(false);
                  }}
                >
                  <span className="model-picker__name">{model.name}</span>
                  <span className="model-picker__id">{model.id}</span>
                  {model.variants.length > 0 ? (
                    <span className="model-picker__variants">
                      {model.variants.length} variants
                    </span>
                  ) : null}
                </button>
              ))}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
