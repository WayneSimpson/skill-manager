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

describe("PermissionEditor — MCP permissions section", () => {
  const servers = [
    { name: "clickup", status: "connected", error: null, wildcard: "clickup_*" },
    { name: "n8n_nccio", status: "connected", error: null, wildcard: "n8n_nccio_*" },
    { name: "playwright", status: "failed", error: "MCP error -32000", wildcard: "playwright_*" },
  ];

  function renderMcp(
    rules: PermissionRuleDto[],
    onChange: (rules: PermissionRuleDto[]) => void,
    opts: { servers?: typeof servers | undefined; source?: string } = {},
  ) {
    return render(
      <LocaleProvider>
        <PermissionEditor
          generation="v1"
          rules={rules}
          onChange={onChange}
          mcpServers={opts.servers}
          mcpSource={opts.source}
        />
      </LocaleProvider>,
    );
  }

  it("renders one row per runtime MCP server with status and wildcard", () => {
    renderMcp([], vi.fn(), { servers, source: "runtime" });

    expect(screen.getByText("MCP server permissions")).toBeInTheDocument();
    expect(screen.getByText("clickup")).toBeInTheDocument();
    expect(screen.getByText("n8n_nccio")).toBeInTheDocument();
    expect(screen.getAllByText("Connected")).toHaveLength(2); // clickup + n8n_nccio.
    expect(screen.getByText(/Failed/)).toBeInTheDocument(); // playwright status + error.
    expect(screen.getByText("clickup_*")).toBeInTheDocument();
    expect(screen.getByText("n8n_nccio_*")).toBeInTheDocument();
    expect(screen.getByText(/Live status from the OpenCode runtime/)).toBeInTheDocument();
  });

  it("existing n8n_nccio_* allow rule appears as Allow in its row", () => {
    renderMcp(
      [{ action: "n8n_nccio_*", effect: "allow", resource: null }],
      vi.fn(),
      { servers },
    );

    const select = screen.getByLabelText(/MCP server n8n_nccio/) as HTMLSelectElement;
    expect(select.value).toBe("allow");
  });

  it("legacy playwright* rule is associated with the server row truthfully", () => {
    renderMcp(
      [{ action: "playwright*", effect: "ask", resource: null }],
      vi.fn(),
      { servers },
    );

    const select = screen.getByLabelText(/MCP server playwright/) as HTMLSelectElement;
    expect(select.value).toBe("ask");
  });

  it("changing Allow → Ask updates the exact existing rule in place", () => {
    const onChange = vi.fn();
    renderMcp(
      [
        { action: "edit", effect: "deny", resource: null },
        { action: "n8n_nccio_*", effect: "allow", resource: null },
      ],
      onChange,
      { servers },
    );

    fireEvent.change(screen.getByLabelText(/MCP server n8n_nccio/), {
      target: { value: "ask" },
    });
    expect(onChange).toHaveBeenCalledWith([
      { action: "edit", effect: "deny", resource: null },
      { action: "n8n_nccio_*", effect: "ask", resource: null },
    ]);
  });

  it("changing to Inherit emits an inherit marker for ONLY that server's exact rule", () => {
    const onChange = vi.fn();
    renderMcp(
      [
        { action: "n8n_nccio_*", effect: "allow", resource: null, order: 0 },
        { action: "clickup_*", effect: "deny", resource: null, order: 1 },
        { action: "read", effect: "allow", resource: null, order: 2 },
      ],
      onChange,
      { servers },
    );

    fireEvent.change(screen.getByLabelText(/MCP server n8n_nccio/), {
      target: { value: "inherit" },
    });
    expect(onChange).toHaveBeenCalledWith([
      { action: "n8n_nccio_*", effect: "inherit", resource: null, order: 0 },
      { action: "clickup_*", effect: "deny", resource: null, order: 1 },
      { action: "read", effect: "allow", resource: null, order: 2 },
    ]);
  });

  it("new agent defaults every discovered MCP to Inherit; explicit Allow appends canonical wildcard", () => {
    const onChange = vi.fn();
    renderMcp([], onChange, { servers });

    for (const server of ["clickup", "n8n_nccio", "playwright"]) {
      const select = screen.getByLabelText(
        new RegExp(`MCP server ${server}`),
      ) as HTMLSelectElement;
      expect(select.value).toBe("inherit");
    }

    fireEvent.change(screen.getByLabelText(/MCP server playwright/), {
      target: { value: "deny" },
    });
    expect(onChange).toHaveBeenCalledWith([
      expect.objectContaining({ action: "playwright_*", effect: "deny", resource: null }),
    ]);
  });

  it("orphan and custom wildcard rules stay in Advanced untouched", () => {
    renderMcp(
      [
        { action: "n8n_nccio_*", effect: "allow", resource: null },
        { action: "orphan_tool_*", effect: "deny", resource: null },
        { action: "clickup_exact_tool", effect: "ask", resource: null },
      ],
      vi.fn(),
      { servers },
    );

    fireEvent.click(screen.getByText(/Advanced permission rules/));
    // The MCP-managed wildcard is hoisted out of Advanced; the orphan and
    // exact per-tool rules stay in their original order.
    const inputs = screen.getAllByLabelText(/Action\/tool/) as HTMLInputElement[];
    expect(inputs.map((input) => input.value)).toEqual([
      "orphan_tool_*",
      "clickup_exact_tool",
    ]);
  });

  it("without MCP data the wildcard rules remain in Advanced (no data loss)", () => {
    renderEditor([{ action: "n8n_nccio_*", effect: "deny", resource: null }], vi.fn());

    expect(screen.queryByText("MCP server permissions")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/Advanced permission rules/));
    expect(
      (screen.getByLabelText(/Action\/tool.*1/) as HTMLInputElement).value,
    ).toBe("n8n_nccio_*");
  });

  it("config-fallback source hint is shown", () => {
    renderMcp([], vi.fn(), { servers: servers.slice(0, 1), source: "config" });

    expect(
      screen.getByText(/servers derived from configuration/),
    ).toBeInTheDocument();
  });

  it("unavailable source shows the Advanced-preservation hint", () => {
    renderMcp([], vi.fn(), { servers: [], source: "unavailable" });

    expect(
      screen.getByText(/remain under Advanced/),
    ).toBeInTheDocument();
  });
});

describe("PermissionEditor — V2 MCP server-wide resource:* semantics", () => {
  const servers = [
    { name: "context7", status: "connected", error: null, wildcard: "context7_*" },
    { name: "n8n_nccio", status: "connected", error: null, wildcard: "n8n_nccio_*" },
  ];

  function renderV2Mcp(
    rules: PermissionRuleDto[],
    onChange: (rules: PermissionRuleDto[]) => void,
  ) {
    return render(
      <LocaleProvider>
        <PermissionEditor
          generation="v2"
          rules={rules}
          onChange={onChange}
          mcpServers={servers}
          mcpSource="runtime"
        />
      </LocaleProvider>,
    );
  }

  it("V2 server-wide rule with resource:* appears in its MCP row as Allow", () => {
    renderV2Mcp(
      [
        { action: "n8n_nccio_*", effect: "allow", resource: "*", order: 0 },
        { action: "read", effect: "deny", resource: null, order: 1 },
      ],
      vi.fn(),
    );

    const select = screen.getByLabelText(/MCP server n8n_nccio/) as HTMLSelectElement;
    expect(select.value).toBe("allow");
  });

  it("Allow → Ask preserves resource:* and V2 order", () => {
    const onChange = vi.fn();
    renderV2Mcp(
      [
        { action: "read", effect: "deny", resource: null, order: 0 },
        { action: "n8n_nccio_*", effect: "allow", resource: "*", order: 1 },
        { action: "shell", effect: "ask", resource: null, order: 2 },
      ],
      onChange,
    );

    fireEvent.change(screen.getByLabelText(/MCP server n8n_nccio/), {
      target: { value: "ask" },
    });
    expect(onChange).toHaveBeenCalledWith([
      { action: "read", effect: "deny", resource: null, order: 0 },
      { action: "n8n_nccio_*", effect: "ask", resource: "*", order: 1 },
      { action: "shell", effect: "ask", resource: null, order: 2 },
    ]);
  });

  it("Inherit emits a marker removing only that V2 rule, preserving others/order", () => {
    const onChange = vi.fn();
    renderV2Mcp(
      [
        { action: "n8n_nccio_*", effect: "allow", resource: "*", order: 0 },
        { action: "context7_*", effect: "deny", resource: "*", order: 1 },
        { action: "read", effect: "allow", resource: null, order: 2 },
      ],
      onChange,
    );

    fireEvent.change(screen.getByLabelText(/MCP server n8n_nccio/), {
      target: { value: "inherit" },
    });
    expect(onChange).toHaveBeenCalledWith([
      { action: "n8n_nccio_*", effect: "inherit", resource: "*", order: 0 },
      { action: "context7_*", effect: "deny", resource: "*", order: 1 },
      { action: "read", effect: "allow", resource: null, order: 2 },
    ]);
  });

  it("newly-created V2 MCP override carries resource:*", () => {
    const onChange = vi.fn();
    renderV2Mcp(
      [{ action: "read", effect: "deny", resource: null, order: 0 }],
      onChange,
    );

    fireEvent.change(screen.getByLabelText(/MCP server context7/), {
      target: { value: "deny" },
    });
    expect(onChange).toHaveBeenCalledWith([
      { action: "read", effect: "deny", resource: null, order: 0 },
      expect.objectContaining({
        action: "context7_*", effect: "deny", resource: "*", order: 1,
      }),
    ]);
  });

  it("resource-specific and per-tool V2 rules stay in Advanced", () => {
    renderV2Mcp(
      [
        { action: "context7_*", effect: "allow", resource: "*", order: 0 },
        { action: "clickup_create_task", effect: "ask", resource: null, order: 1 },
        { action: "shell", effect: "deny", resource: "git push", order: 2 },
      ],
      vi.fn(),
    );

    fireEvent.click(screen.getByText(/Advanced permission rules/));
    const inputs = screen.getAllByLabelText(/Action\/tool/) as HTMLInputElement[];
    expect(inputs.map((input) => input.value)).toEqual([
      "clickup_create_task",
      "shell",
    ]);
  });

  it("noncompliant V2 wildcard without resource stays in Advanced (not normalized)", () => {
    renderV2Mcp(
      [{ action: "n8n_nccio_*", effect: "allow", resource: null, order: 0 }],
      vi.fn(),
    );

    // Row shows Inherit; the rule itself remains visible in Advanced.
    const select = screen.getByLabelText(/MCP server n8n_nccio/) as HTMLSelectElement;
    expect(select.value).toBe("inherit");
    fireEvent.click(screen.getByText(/Advanced permission rules/));
    const inputs = screen.getAllByLabelText(/Action\/tool/) as HTMLInputElement[];
    expect(inputs.map((input) => input.value)).toEqual(["n8n_nccio_*"]);
  });
});
