import { NeedsReviewRow } from "../../../../components/cards/NeedsReviewRow";
import { UiTooltip } from "../../../../components/ui/UiTooltip";
import { getHarnessPresentation } from "../../../../components/harness/harnessPresentation";
import { useSkillsCopy } from "../../i18n";
import { useSkillPackageContextQuery } from "../../api/queries";
import type { StructuralSkillAction } from "../../model/pending";
import type { HarnessCell, SkillListRow } from "../../model/types";

interface SkillNeedsReviewCardProps {
  row: SkillListRow;
  pendingStructuralAction: StructuralSkillAction | null;
  bulkActionPending: boolean;
  selected: boolean;
  onOpenSkill: (skillRef: string) => void;
  onManageSkill: (skillRef: string) => Promise<void>;
  onManagePackage?: (skillRef: string) => Promise<void>;
}

function HarnessLogo({ cell, zIndex }: { cell: HarnessCell; zIndex: number }) {
  const presentation = getHarnessPresentation(cell.logoKey ?? cell.harness);
  return (
    <UiTooltip content={cell.label}>
      <span className="harness-stack__item" style={{ zIndex }}>
        {presentation ? (
          <img src={presentation.logoSrc} alt="" aria-hidden="true" />
        ) : (
          <span className="harness-stack__fallback">{cell.label.slice(0, 1)}</span>
        )}
      </span>
    </UiTooltip>
  );
}

export function SkillNeedsReviewCard({
  row,
  pendingStructuralAction,
  bulkActionPending,
  selected: _selected,
  onOpenSkill,
  onManageSkill,
  onManagePackage,
}: SkillNeedsReviewCardProps) {
  const copy = useSkillsCopy();
  const packageContextQuery = useSkillPackageContextQuery(row.skillRef);
  const found = row.cells.filter((cell) => cell.state === "found");
  const managing = pendingStructuralAction === "manage";
  const packageContext = packageContextQuery.data;
  const packageBacked = packageContext?.packageBacked === true;
  const packageManaged = packageBacked && Boolean(packageContext.managedPackage);
  const packageReady = packageBacked && packageContext.resolution?.status === "resolved" && !packageManaged;
  const packageNeedsReview = packageBacked && !packageManaged && !packageReady;
  const checkingPackage = (packageContextQuery.isPending || packageContextQuery.isFetching) && !packageContextQuery.isError;
  const packageContextFailed = packageContextQuery.isError;
  const canManagePackage = packageReady && typeof onManagePackage === "function";
  const metaText = `Found in ${found.length} harness${found.length === 1 ? "" : "es"}`;
  const actionLabel = checkingPackage
    ? copy.detail.checkingPackage
    : packageContextFailed
      ? copy.detail.reviewPackageSource
    : packageReady
      ? copy.detail.managePackage
      : packageNeedsReview
        ? copy.detail.reviewPackageSource
        : copy.detail.adopt;

  return (
    <NeedsReviewRow
      name={row.name}
      logos={
        <span className="harness-stack">
          {found.map((cell, index) => (
            <HarnessLogo key={cell.harness} cell={cell} zIndex={found.length - index} />
          ))}
        </span>
      }
      metaText={metaText}
      description={row.description}
      actionLabel={actionLabel}
      actionTitle={
        packageContextFailed
          ? copy.detail.packageContextUnavailable
          : packageReady
          ? copy.detail.packageManagement.managePackageTitle
          : packageNeedsReview
            ? copy.detail.packageManagement.reviewPackageSourceTitle
            : row.actions.canManage
          ? "Add this skill to Skill Manager"
          : row.actions.canManageReason ?? "This skill cannot be adopted automatically"
      }
      actionUnavailableReason={
        row.actions.canManageReason
          ? copy.detail.capabilityLimitation(row.actions.canManageReason)
          : undefined
      }
      pending={managing}
      actionDisabled={
        bulkActionPending ||
        pendingStructuralAction !== null ||
        checkingPackage ||
        (!packageBacked && !packageContextFailed && !checkingPackage && !row.actions.canManage) ||
        (packageReady && !canManagePackage)
      }
      onOpen={() => onOpenSkill(row.skillRef)}
      onAction={() => {
        if (packageContextFailed || packageNeedsReview) {
          onOpenSkill(row.skillRef);
        } else if (packageReady) {
          void onManagePackage?.(row.skillRef);
        } else {
          void onManageSkill(row.skillRef);
        }
      }}
    />
  );
}
