import { describe, expect, it } from "vitest";

import type { SkillDetailDto } from "./types";
import { mapSkillDetail } from "./mappers";

describe("mapSkillDetail", () => {
  it("preserves the optional source package payload", () => {
    const sourcePackage: NonNullable<SkillDetailDto["sourcePackage"]> = {
      status: "resolved",
      sourceKind: "local",
      sourcePath: "/workspace/trace-lens/skills/trace-lens",
      sourceRevision: "source-revision",
      reason: null,
      package: {
        id: "pkg-trace-lens",
        root: "/workspace/trace-lens",
        name: "trace-lens",
        version: "1.2.0",
        evidence: "declared_standard",
        manifests: [],
        components: [],
        diagnostics: [],
        revision: "capability-revision",
      },
    };
    const detail: SkillDetailDto = {
      skillRef: "unmanaged:trace-lens",
      name: "Trace Lens",
      description: "Trace review workflow",
      displayStatus: "Unmanaged",
      attentionMessage: null,
      actions: {
        canManage: true,
        canManageReason: null,
        stopManagingStatus: null,
        stopManagingHarnessLabels: [],
        canDelete: false,
        deleteHarnessLabels: [],
      },
      harnessCells: [],
      locations: [],
      sourceLinks: null,
      documentMarkdown: null,
      sourcePackage,
    };

    expect(mapSkillDetail(detail).sourcePackage).toBe(sourcePackage);
  });
});
