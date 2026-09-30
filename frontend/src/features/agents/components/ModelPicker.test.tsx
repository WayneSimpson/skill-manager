import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LocaleProvider } from "../../../i18n";
import { ModelPicker } from "./ModelPicker";
import type { ModelCatalogueDto } from "../api/types";

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
    {
      id: "openai", name: "OpenAI", source: "env", connected: false,
      models: [
        {
          id: "openai/gpt-5.4", providerId: "openai",
          providerName: "OpenAI", name: "GPT-5.4",
          status: "active", reasoning: true,
          variants: ["none", "low", "medium", "high", "xhigh"],
        },
      ],
    },
  ],
};

function renderPicker(value: string, onChange: (v: string) => void, cat = catalogue) {
  return render(
    <LocaleProvider>
      <ModelPicker value={value} onChange={onChange} catalogue={cat} />
    </LocaleProvider>,
  );
}

describe("ModelPicker", () => {
  it("shows the current value and opens a searchable grouped dropdown", () => {
    const onChange = vi.fn();
    renderPicker("anthropic/claude-sonnet-4-5", onChange);

    const input = screen.getByRole("textbox", { name: /Model/i });
    expect((input as HTMLInputElement).value).toBe("anthropic/claude-sonnet-4-5");

    fireEvent.focus(input);
    expect(screen.getByRole("option", { name: /Inherit parent/ })).toBeInTheDocument();
    expect(screen.getByText("Anthropic")).toBeInTheDocument();
    expect(screen.getByText("OpenAI")).toBeInTheDocument();
    expect(screen.getByText("Claude Sonnet 4.5")).toBeInTheDocument();
  });

  it("filters by search text across provider, name and ID", () => {
    const onChange = vi.fn();
    renderPicker("", onChange);

    const input = screen.getByRole("textbox", { name: /Model/i });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "sonnet" } });

    expect(screen.getByText("Claude Sonnet 4.5")).toBeInTheDocument();
    expect(screen.queryByText("GPT-5.4")).not.toBeInTheDocument();
  });

  it("selects a model from the dropdown", () => {
    const onChange = vi.fn();
    renderPicker("", onChange);

    fireEvent.focus(screen.getByRole("textbox", { name: /Model/i }));
    fireEvent.click(screen.getByRole("option", { name: /GPT-5.4/ }));
    expect(onChange).toHaveBeenCalledWith("openai/gpt-5.4");
  });

  it("offers inherit parent/session model", () => {
    const onChange = vi.fn();
    renderPicker("anthropic/claude-sonnet-4-5", onChange);

    fireEvent.focus(screen.getByRole("textbox", { name: /Model/i }));
    fireEvent.click(screen.getByRole("option", { name: /Inherit parent/ }));
    expect(onChange).toHaveBeenCalledWith("");
  });

  it("shows a custom-value warning for models not in the catalogue", () => {
    renderPicker("custom/unavailable-model", () => undefined);

    expect(screen.getByText(/Custom\/unavailable model/)).toBeInTheDocument();
  });

  it("shows unavailable state when catalogue is missing", () => {
    renderPicker("", () => undefined, { source: "unavailable", providers: [], totalModels: 0 });

    fireEvent.focus(screen.getByRole("textbox", { name: /Model/i }));
    expect(screen.getByText(/catalogue unavailable/i)).toBeInTheDocument();
  });
});


describe("ModelPicker — search is not selection", () => {
  it("typing and blurring does NOT silently change the model", () => {
    const onChange = vi.fn();
    renderPicker("anthropic/claude-sonnet-4-5", onChange);

    const input = screen.getByRole("textbox", { name: /Model/i });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "some-random-text" } });
    fireEvent.blur(input);

    expect(onChange).not.toHaveBeenCalled();
  });

  it("offers an explicit 'Use custom model ID' action for deliberate override", () => {
    const onChange = vi.fn();
    renderPicker("", onChange);

    const input = screen.getByRole("textbox", { name: /Model/i });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "custom/model-x" } });

    const customOption = screen.getByRole("option", { name: /Use custom model ID/ });
    fireEvent.click(customOption);
    expect(onChange).toHaveBeenCalledWith("custom/model-x");
  });

  it("existing custom model value displays and does not change on blur", () => {
    const onChange = vi.fn();
    renderPicker("legacy/custom-model", onChange);

    const input = screen.getByRole("textbox", { name: /Model/i });
    expect((input as HTMLInputElement).value).toBe("legacy/custom-model");
    expect(screen.getByText(/Custom\/unavailable model/)).toBeInTheDocument();

    fireEvent.focus(input);
    fireEvent.blur(input);
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("ModelPicker — connected provider ordering", () => {
  it("renders connected providers before disconnected ones", () => {
    const mixedCatalogue: ModelCatalogueDto = {
      source: "runtime", totalModels: 2,
      providers: [
        { id: "z-disconnected", name: "Z Disconnected", source: "env", connected: false,
          models: [{ id: "z-disconnected/model", providerId: "z-disconnected",
            providerName: "Z Disconnected", name: "Z Model", status: "active",
            reasoning: false, variants: [] }] },
        { id: "a-connected", name: "A Connected", source: "env", connected: true,
          models: [{ id: "a-connected/model", providerId: "a-connected",
            providerName: "A Connected", name: "A Model", status: "active",
            reasoning: true, variants: ["low"] }] },
      ],
    };

    renderPicker("", () => undefined, mixedCatalogue);

    fireEvent.focus(screen.getByRole("textbox", { name: /Model/i }));
    const groupLabels = screen.getAllByText(/Connected|Disconnected/);
    expect(groupLabels[0]).toHaveTextContent("A Connected"); // Connected first.
  });
});
