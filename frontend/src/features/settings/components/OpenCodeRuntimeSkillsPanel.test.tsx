import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { errorJson, okJson } from "../../../test/fetch";
import { renderWithAppProviders } from "../../../test/render";
import { OpenCodeRuntimeSkillsPanel } from "./OpenCodeRuntimeSkillsPanel";

const fetchMock = vi.fn();

const disconnectedStatus = {
  status: "disconnected",
  serverUrl: null,
  directory: null,
  skillCount: 0,
  error: null,
} as const;

describe("OpenCodeRuntimeSkillsPanel", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  it("requires explicit consent before it can request a runtime refresh", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url === "/api/opencode/runtime-skills/status") {
        return okJson(disconnectedStatus);
      }
      throw new Error(`Unhandled URL ${url}`);
    });

    renderWithAppProviders(<OpenCodeRuntimeSkillsPanel />);

    expect(await screen.findByText("No runtime skill snapshot is connected.")).toBeInTheDocument();
    expect(screen.getByText(/This request may initialize OpenCode/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh OpenCode snapshot" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows refresh errors and clears the submitted password", async () => {
    let statusCallCount = 0;
    fetchMock.mockImplementation(async (input: RequestInfo, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url === "/api/opencode/runtime-skills/status") {
        statusCallCount += 1;
        return statusCallCount === 1
          ? okJson(disconnectedStatus)
          : okJson({
              status: "error",
              serverUrl: "http://127.0.0.1:4096",
              directory: "/tmp/opencode",
              skillCount: 0,
              error: "OpenCode server is unavailable.",
            });
      }
      if (url === "/api/opencode/runtime-skills/refresh") {
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({
          consent: true,
          serverUrl: "http://127.0.0.1:4096",
          directory: "/tmp/opencode",
          username: "operator",
          password: "secret",
        });
        return errorJson("OpenCode server is unavailable.", { status: 502 });
      }
      throw new Error(`Unhandled URL ${url}`);
    });

    renderWithAppProviders(<OpenCodeRuntimeSkillsPanel />);
    await screen.findByText("No runtime skill snapshot is connected.");

    fireEvent.change(screen.getByLabelText("Local OpenCode server URL"), {
      target: { value: "http://127.0.0.1:4096" },
    });
    fireEvent.change(screen.getByLabelText("Absolute OpenCode directory"), {
      target: { value: "/tmp/opencode" },
    });
    fireEvent.change(screen.getByLabelText("Basic username (optional)"), {
      target: { value: "operator" },
    });
    fireEvent.change(screen.getByLabelText("Basic password (optional)"), {
      target: { value: "secret" },
    });
    fireEvent.click(screen.getByLabelText("I explicitly allow Skill Manager to contact this local server."));
    fireEvent.click(screen.getByRole("button", { name: "Refresh OpenCode snapshot" }));

    await waitFor(() => expect(screen.getByLabelText("Basic password (optional)")).toHaveValue(""));
    expect(screen.getByLabelText("Basic username (optional)")).toHaveValue("");
    expect(await screen.findByRole("alert")).toHaveTextContent("OpenCode server is unavailable.");
  });

  it("renders a ready snapshot and lets the user clear it", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url === "/api/opencode/runtime-skills/status") {
        return okJson({
          status: "ready",
          serverUrl: "http://127.0.0.1:4096",
          directory: "/tmp/opencode",
          skillCount: 2,
          error: null,
        });
      }
      if (url === "/api/opencode/runtime-skills/disconnect") {
        expect(init?.method).toBe("POST");
        return okJson(disconnectedStatus);
      }
      throw new Error(`Unhandled URL ${url}`);
    });

    renderWithAppProviders(<OpenCodeRuntimeSkillsPanel />);

    expect(await screen.findByText("2 runtime skills in the current snapshot.")).toBeInTheDocument();
    expect(screen.getByText("http://127.0.0.1:4096")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear snapshot" }));

    expect(await screen.findByText("No runtime skill snapshot is connected.")).toBeInTheDocument();
  });
});
