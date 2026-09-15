import { DetailNote } from "../../../../components/detail/DetailNote";
import { DetailSection } from "../../../../components/detail/DetailSection";
import { LoadingSpinner } from "../../../../components/LoadingSpinner";
import { useSkillsCopy } from "../../i18n";
import type { StructuralSkillAction } from "../../model/pending";
import type { SkillPackageContextResponse } from "../../api/package-types";
import { ManagedPackageSummary } from "./ManagedPackageSummary";

interface PackageAwareSkillPanelProps {
  context: SkillPackageContextResponse;
  pendingStructuralAction: StructuralSkillAction | null;
  onManagePackage: () => void;
  onResolvePackage: () => void;
  disabled?: boolean;
  canManagePackage?: boolean;
}

export function PackageAwareSkillPanel({
  context,
  pendingStructuralAction,
  onManagePackage,
  onResolvePackage,
  disabled = false,
  canManagePackage = true,
}: PackageAwareSkillPanelProps) {
  const copy = useSkillsCopy().detail.packageManagement;
  const resolution = context.resolution;
  const managedPackage = context.managedPackage;
  const isManaging = pendingStructuralAction === "manage";
  const isResolving = pendingStructuralAction === "resolve";
  const hasObservedPackage = context.observation?.package !== null && context.observation?.package !== undefined;
  const sourceResolved = resolution?.status === "resolved";
   const canManage = canManagePackage && context.packageBacked && sourceResolved && managedPackage === null;
  const canResolve = context.packageBacked && !sourceResolved;
   const actionsDisabled = disabled || isManaging || isResolving;

  return (
    <DetailSection heading={copy.title}>
      <div className="package-context-card">
        <div className="package-context-card__lifecycle">
           <LifecycleStep label={copy.localObservation} value={observationLabel(context, copy)} />
          <LifecycleStep
            label={copy.upstreamSource}
            value={resolution ? resolutionLabel(copy, resolution.status) : copy.sourceNotManaged}
            tone={resolution?.status === "resolved" ? "positive" : "warning"}
          />
          <LifecycleStep
            label={copy.centralManagement}
            value={managedPackage ? copy.managePackage : copy.notManaged}
            tone={managedPackage ? "positive" : "neutral"}
          />
        </div>

        {context.observation ? <details><summary>{copy.observationDetails}</summary>
          <ObservationSummary observation={context.observation} />
        </details> : null}

        {resolution ? (
          <ResolutionSummary resolution={resolution} showLimitations={!managedPackage} />
        ) : hasObservedPackage ? (
          <DetailNote>{copy.sourceNotManaged}</DetailNote>
        ) : (
          <DetailNote>{copy.nativeUnavailable}</DetailNote>
        )}

        {canManage || canResolve ? (
          <div className="package-context-card__actions">
            {canManage ? <DetailNote>{copy.readyToManage}</DetailNote> : null}
            {canResolve ? (
              <button
                type="button"
                className="action-pill action-pill--md"
                onClick={onResolvePackage}
                disabled={actionsDisabled}
                aria-busy={isResolving}
              >
                {isResolving ? <LoadingSpinner size="sm" label={copy.resolvingSource} /> : null}
                {isResolving ? copy.resolvingSource : copy.resolveSource}
              </button>
            ) : null}
            {canManage ? (
              <button
                type="button"
                className="action-pill action-pill--md action-pill--accent"
                onClick={onManagePackage}
                disabled={actionsDisabled}
                aria-busy={isManaging}
              >
                {isManaging ? <LoadingSpinner size="sm" label={copy.managingPackage} /> : null}
                {isManaging ? copy.managingPackage : copy.managePackage}
              </button>
            ) : null}
          </div>
        ) : context.packageBacked && !managedPackage && !hasObservedPackage ? (
          <div className="package-context-card__actions">
            <DetailNote>{copy.noSourceToManage}</DetailNote>
          </div>
        ) : null}
      </div>

      {managedPackage ? (
        <div className="package-context-card package-context-card--managed">
          <DetailNote>{copy.managedSummary}</DetailNote>
          <ManagedPackageSummary packageData={managedPackage} />
        </div>
      ) : null}
    </DetailSection>
  );
}

function LifecycleStep({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "positive" | "warning" | "neutral";
}) {
  return (
    <div className="package-context-card__step" data-tone={tone ?? "neutral"}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ObservationSummary({ observation }: { observation: NonNullable<SkillPackageContextResponse["observation"]> }) {
  const copy = useSkillsCopy().detail.packageManagement;
  const packageData = observation.package;
  return (
    <dl className="package-context-card__metadata">
      <ContextField label={copy.observedSourceKind} value={observation.sourceKind} />
      <ContextField label={copy.observedSkill} value={observation.sourcePath ?? copy.unavailable} />
      {observation.sourceRevision ? <ContextField label={copy.observedRevision} value={observation.sourceRevision} /> : null}
      {packageData ? <ContextField label={copy.observedPackage} value={`${packageData.name} · ${packageData.root}`} /> : null}
    </dl>
  );
}

function ResolutionSummary({
  resolution,
  showLimitations,
}: {
  resolution: NonNullable<SkillPackageContextResponse["resolution"]>;
  showLimitations: boolean;
}) {
  const copy = useSkillsCopy().detail.packageManagement;
  return (
    <div className="package-context-card__resolution" data-status={resolution.status}>
      <div className="package-context-card__resolution-header">
        <strong>{resolutionLabel(copy, resolution.status)}</strong>
        {resolution.source ? <code>{sourceDetails(resolution.source, copy)}</code> : null}
      </div>
      {resolution.reason ? <p>{resolution.reason}</p> : null}
      {showLimitations && resolution.limitations.length > 0 ? (
        <ul>
          {resolution.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}
        </ul>
      ) : null}
      {resolution.evidence.length > 0 ? (
        <ul className="package-context-card__evidence">
          {resolution.evidence.map((evidence, index) => (
            <li key={`${evidence.kind}:${evidence.location}:${index}`}>
              <strong>{evidence.kind}</strong><span>{evidence.location}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {resolution.evidence.length === 0 && resolution.status !== "resolved" ? <p>{copy.noEvidence}</p> : null}
    </div>
  );
}

function ContextField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd><code>{value}</code></dd>
    </div>
  );
}

function observationLabel(
  context: SkillPackageContextResponse,
  copy: ReturnType<typeof useSkillsCopy>["detail"]["packageManagement"],
): string {
  return context.observation?.package?.name ?? context.observation?.sourcePath ?? copy.observationDetected;
}

function resolutionLabel(
  copy: ReturnType<typeof useSkillsCopy>["detail"]["packageManagement"],
  status: string,
): string {
  if (status === "resolved") return copy.sourceResolved;
  if (status === "ambiguous") return copy.sourceAmbiguous;
  if (status === "unavailable") return copy.sourceUnavailable;
  return copy.sourceUnresolved;
}

function sourceDetails(
  source: NonNullable<SkillPackageContextResponse["resolution"]>["source"],
  copy: ReturnType<typeof useSkillsCopy>["detail"]["packageManagement"],
): string {
  if (!source) return copy.unavailable;
  return [
    source.kind,
    source.locator,
    source.version ? copy.sourceVersion(source.version) : null,
    source.ref ? copy.sourceRef(source.ref) : null,
    source.revision,
    source.package_path ? copy.sourcePath(source.package_path) : null,
  ].filter(Boolean).join(" · ");
}
