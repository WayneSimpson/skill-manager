import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LocaleProvider } from "../../../i18n";
import { PermissionEditor } from "./PermissionEditor";
import type { PermissionRuleDto } from "../api/types";

function renderEditor(
  rules: PermissionRuleDto[],
  onChange: (rules: PermissionRuleDto[]) => void,
  generation: "v1" | "v2" = "v1",
) {
  return render(
    <LocaleProvider>
      <PermissionEditor generation={generation} rules={rules} onChange={onChange} />
    </LocaleProvider>,
  );
}

describe("PermissionEditor", () => {
  it("renders common actions with inherit/allow/ask/deny selectors", () => {
    renderEditor([{ action: "edit", effect: "deny", resource: null }], vi.fn());

    expect(screen.getByText("Edit / write files")).toBeInTheDocument();
    expect(screen.getByText("Shell / bash commands")).toBeInTheDocument();
    expect(screen.getByText("Read files")).toBeInTheDocument();
    expect(screen.getByText("Launch sub-agents (task)")).toBeInTheDocument();

    const editSelect = screen.getByLabelText(/Edit \/ write files/) as HTMLSelectElement;
    expect(editSelect.value).toBe("deny");

    const readSelect = screen.getByLabelText(/Read files/) as HTMLSelectElement;
    expect(readSelect.value).toBe("inherit"); // Not set = inherited.
  });

  it("uses V2 action labels for V2 generation", () => {
    renderEditor([], vi.fn(), "v2");

    expect(screen.getByText("Shell commands")).toBeInTheDocument();
    expect(screen.getByText("Launch sub-agents")).toBeInTheDocument();
    expect(screen.queryByText("Shell / bash commands")).not.toBeInTheDocument();
  });

  it("changing a common action effect calls onChange", () => {
    const onChange = vi.fn();
    renderEditor([], onChange);

    fireEvent.change(screen.getByLabelText(/Shell \/ bash/), { target: { value: "ask" } });
    expect(onChange).toHaveBeenCalledWith([
      expect.objectContaining({ action: "bash", effect: "ask" }),
    ]);
  });

  it("setting inherit on an existing rule keeps an inherit marker for backend removal", () => {
    const onChange = vi.fn();
    renderEditor([{ action: "bash", effect: "deny", resource: null }], onChange);

    fireEvent.change(screen.getByLabelText(/Shell \/ bash/), { target: { value: "inherit" } });
    expect(onChange).toHaveBeenCalledWith([
      expect.objectContaining({ action: "bash", effect: "inherit" }),
    ]);
  });

  it("setting inherit with no existing rule sends nothing (default)", () => {
    const onChange = vi.fn();
    renderEditor([], onChange);

    fireEvent.change(screen.getByLabelText(/Shell \/ bash/), { target: { value: "inherit" } });
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("advanced rules edit/remove targets the correct underlying rule when common rules precede them", () => {
    const onChange = vi.fn();
    // rules[0] = common (edit), rules[1] = advanced (custom wildcard),
    // rules[2] = advanced (pattern bash rule).
    renderEditor(
      [
        { action: "edit", effect: "deny", resource: null },
        { action: "n8n_nccio_*", effect: "deny", resource: null },
        { action: "bash", effect: "ask", resource: "git push" },
      ],
      onChange,
    );

    fireEvent.click(screen.getByText(/Advanced permission rules/));

    // Edit the SECOND advanced rule (source index 2: bash/git push).
    const resourceInputs = screen.getAllByLabelText(/Resource\/pattern/);
    fireEvent.change(resourceInputs[1], { target: { value: "git pull" } });
    expect(onChange).toHaveBeenCalledWith(
      expect.arrayContaining([
        expect.objectContaining({ action: "edit", effect: "deny" }),       // unchanged
        expect.objectContaining({ action: "n8n_nccio_*", effect: "deny" }), // unchanged
        expect.objectContaining({ action: "bash", resource: "git pull" }),  // edited
      ]),
    );

    // Remove the FIRST advanced rule (source index 1: n8n_nccio_*).
    const removeButtons = screen.getAllByLabelText(/Remove rule/);
    fireEvent.click(removeButtons[0]);
    expect(onChange).toHaveBeenCalledWith([
      expect.objectContaining({ action: "edit" }),                          // common kept
      expect.objectContaining({ action: "bash", resource: "git push" }),   // advanced 2 kept
    ]);
  });

  it("advanced rules render action, resource, effect and remove", () => {
    const onChange = vi.fn();
    renderEditor(
      [{ action: "n8n_nccio_*", effect: "deny", resource: null }],
      onChange,
    );

    fireEvent.click(screen.getByText(/Advanced permission rules/));
    expect(
      (screen.getByLabelText(/Action\/tool.*1/) as HTMLInputElement).value,
    ).toBe("n8n_nccio_*");
    expect(
      (screen.getByLabelText(/Effect 1/) as HTMLSelectElement).value,
    ).toBe("deny");
  });

  it("adding a rule calls onChange with a new empty entry", () => {
    const onChange = vi.fn();
    renderEditor([], onChange);

    fireEvent.click(screen.getByText(/Advanced permission rules/));
    fireEvent.click(screen.getByText(/Add rule/));
    expect(onChange).toHaveBeenCalledWith([
      expect.objectContaining({ action: "", effect: "allow" }),
    ]);
  });
});
