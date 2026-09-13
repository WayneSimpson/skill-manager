import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithAppProviders } from "../../../../test/render";
import type { SkillListRow } from "../../model/types";
import { SkillNeedsReviewCard } from "./SkillNeedsReviewCard";

describe("SkillNeedsReviewCard", () => {
  it("shows why a non-materializable runtime skill cannot be adopted", () => {
    const row: SkillListRow = {
      skillRef: "unmanaged:runtime-only",
      name: "Runtime Only",
      description: "Loaded from OpenCode.",
      displayStatus: "Unmanaged",
      actions: {
        canManage: false,
        canStopManaging: false,
        canDelete: false,
        canManageReason: "Runtime skill has no readable SKILL.md or embedded content and cannot be copied.",
      },
      cells: [{ harness: "opencode", label: "OpenCode", state: "found", interactive: false }],
    };

    renderWithAppProviders(
      <SkillNeedsReviewCard
        row={row}
        pendingStructuralAction={null}
        bulkActionPending={false}
        selected={false}
        onOpenSkill={vi.fn()}
        onManageSkill={vi.fn(async () => undefined)}
      />,
    );

    expect(screen.getByText(/Capability limitation: Runtime skill has no readable SKILL\.md/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Adopt" })).toBeDisabled();
  });
});
