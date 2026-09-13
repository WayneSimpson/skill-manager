import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mcpManagementKeys } from "../../mcp/public";
import { skillsKeys } from "../../skills/public";
import { okJson } from "../../../test/fetch";
import { renderWithAppProviders } from "../../../test/render";
import {
  settingsKeys,
  useDisconnectOpenCodeRuntimeSkillsMutation,
  useHarnessSupportMutation,
  useRefreshOpenCodeRuntimeSkillsMutation,
} from "./queries";

const fetchMock = vi.fn();

function HarnessSupportProbe() {
  const mutation = useHarnessSupportMutation();

  return (
    <button
      type="button"
      onClick={() => mutation.mutate({ harness: "codex", enabled: false })}
    >
      Disable Codex support
    </button>
  );
}

function RuntimeSkillsMutationProbe() {
  const refreshMutation = useRefreshOpenCodeRuntimeSkillsMutation();
  const disconnectMutation = useDisconnectOpenCodeRuntimeSkillsMutation();

  return (
    <>
      <button
        type="button"
        onClick={() =>
          refreshMutation.mutate({
            consent: true,
            serverUrl: "http://127.0.0.1:4096",
            directory: "/tmp/opencode",
          })
        }
      >
        Refresh runtime skills
      </button>
      <button type="button" onClick={() => disconnectMutation.mutate()}>
        Clear runtime snapshot
      </button>
    </>
  );
}

describe("settings queries", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  it("invalidates settings, skills, and MCP after harness support changes", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url === "/api/settings/harnesses/codex/support") {
        expect(init?.method).toBe("PUT");
        expect(JSON.parse(String(init?.body))).toEqual({ enabled: false });
        return okJson({ ok: true, enabled: false });
      }
      throw new Error(`Unhandled URL ${url}`);
    });

    const { queryClient } = renderWithAppProviders(<HarnessSupportProbe />);
    queryClient.setQueryData(settingsKeys.detail(), {
      storage: {
        platform: "linux",
        configDir: "/tmp/config/skill-manager",
        dataDir: "/tmp/data/skill-manager",
        stateDir: "/tmp/state/skill-manager",
        skillsStorePath: "/tmp/data/skill-manager/shared",
        marketplaceCachePath: "/tmp/data/skill-manager/marketplace",
        settingsPath: "/tmp/config/skill-manager/settings.json",
      },
      harnesses: [
        {
          harness: "codex",
          label: "Codex",
          logoKey: "codex",
          supportEnabled: true,
          installed: true,
          managedLocation: "/tmp/codex",
        },
      ],
    });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    fireEvent.click(screen.getByRole("button", { name: "Disable Codex support" }));

    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: mcpManagementKeys.all }),
    );
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: settingsKeys.all });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: skillsKeys.list() });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: skillsKeys.detailPrefix() });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: skillsKeys.sourceStatusPrefix() });
  });

  it("invalidates skills after refreshing or disconnecting runtime skills", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      expect(init?.method).toBe("POST");
      if (url === "/api/opencode/runtime-skills/refresh") {
        return okJson({
          status: "ready",
          serverUrl: "http://127.0.0.1:4096",
          directory: "/tmp/opencode",
          skillCount: 1,
          error: null,
        });
      }
      if (url === "/api/opencode/runtime-skills/disconnect") {
        return okJson({
          status: "disconnected",
          serverUrl: null,
          directory: null,
          skillCount: 0,
          error: null,
        });
      }
      throw new Error(`Unhandled URL ${url}`);
    });

    const { queryClient } = renderWithAppProviders(<RuntimeSkillsMutationProbe />);
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    fireEvent.click(screen.getByRole("button", { name: "Refresh runtime skills" }));
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: skillsKeys.list() }));
    fireEvent.click(screen.getByRole("button", { name: "Clear runtime snapshot" }));
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalledTimes(6));
  });
});
