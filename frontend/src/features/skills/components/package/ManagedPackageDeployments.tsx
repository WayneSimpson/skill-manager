import { useRef, useState, type ReactNode } from "react";

import { ConfirmActionDialog } from "../../../../components/ConfirmActionDialog";
import { ErrorBanner } from "../../../../components/ErrorBanner";
import { LoadingSpinner } from "../../../../components/LoadingSpinner";
import { useSkillsCopy } from "../../i18n";
import {
  usePackageDeploymentMutation,
  usePackageDeploymentsQuery,
} from "../../api/queries";
import type {
  PackageDeploymentActionRequest,
  PackageDeploymentHarnessResponse,
  PackageReplacementOption,
  PackageSourceResponse,
} from "../../api/package-types";

interface ManagedPackageDeploymentsProps {
  packageId: string;
  packageSource?: PackageSourceResponse | null;
}

type DeploymentAction = PackageDeploymentActionRequest["action"];
type Harness = PackageDeploymentHarnessResponse["harness"];
type PendingAction = {
  harness: Harness;
  action: DeploymentAction;
  replacementPackageId?: string;
} | null;

const BLOCKED_STATES = new Set<PackageDeploymentHarnessResponse["state"]>([
  "stale",
  "conflict",
  "external-existing",
  "manual",
  "unsupported",
]);

export function ManagedPackageDeployments({ packageId, packageSource = null }: ManagedPackageDeploymentsProps) {
  const copy = useSkillsCopy().deployment;
  const deploymentsQuery = usePackageDeploymentsQuery(packageId);
  const mutation = usePackageDeploymentMutation();
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [confirmationError, setConfirmationError] = useState("");
  const returnFocus = useRef<HTMLButtonElement | null>(null);
  const sectionRef = useRef<HTMLElement | null>(null);
  const factsUnavailable = deploymentsQuery.isFetching || Boolean(deploymentsQuery.error);
  const controlsDisabled = factsUnavailable || mutation.isPending;

  if (deploymentsQuery.isPending && !deploymentsQuery.data) {
    return (
      <section className="managed-package-deployments" aria-busy="true">
        <h3>{copy.title}</h3>
        <LoadingSpinner size="sm" label={copy.loading} />
      </section>
    );
  }

  if (deploymentsQuery.error && !deploymentsQuery.data) {
    return (
      <section className="managed-package-deployments">
        <h3>{copy.title}</h3>
        <ErrorBanner message={deploymentsQuery.error instanceof Error ? deploymentsQuery.error.message : copy.unableToLoad} />
      </section>
    );
  }

  const harnesses = deploymentsQuery.data?.harnesses ?? [];
  const selectedHarness = pendingAction
    ? harnesses.find((candidate) => candidate.harness === pendingAction.harness) ?? null
    : null;
  const mutationError = mutation.error instanceof Error ? mutation.error.message : "";

  async function confirmAction(): Promise<void> {
    if (!pendingAction) return;
    setConfirmationError("");
    const latest = await deploymentsQuery.refetch();
    if (latest.error || !latest.data) {
      setConfirmationError(copy.actionNoLongerAvailable);
      return;
    }
    const deployment = latest.data.harnesses.find((candidate) => candidate.harness === pendingAction.harness);
    if (!deployment || !isActionAllowed(deployment, pendingAction.action)) {
      setConfirmationError(copy.actionNoLongerAvailable);
      return;
    }
    const replacementPackageId = pendingAction.action === "update"
      ? replacementForAction(deployment, pendingAction.replacementPackageId)
      : undefined;
    if (pendingAction.action === "update" && replacementPackageId === null) {
      setConfirmationError(copy.actionNoLongerAvailable);
      return;
    }
    try {
      await mutation.mutateAsync({
        packageId,
        harness: deployment.harness,
        action: pendingAction.action,
        replacementPackageId: replacementPackageId ?? undefined,
      });
      setPendingAction(null);
    } catch {
      // Keep the confirmation open; partial native failure must be inspected manually.
    }
  }

  return (
    <section ref={sectionRef} tabIndex={-1} className="managed-package-deployments" aria-labelledby={`deployments-${packageId}`}>
      <div className="managed-package-deployments__header">
        <div>
          <h3 id={`deployments-${packageId}`}>{copy.title}</h3>
          <p>{copy.confirmDescription}</p>
        </div>
      </div>
      {deploymentsQuery.error ? (
        <ErrorBanner
          message={deploymentsQuery.error instanceof Error ? deploymentsQuery.error.message : copy.unableToLoad}
        />
      ) : deploymentsQuery.isFetching ? (
        <p className="managed-package-deployments__refreshing" role="status">{copy.refreshing}</p>
      ) : null}
      {!pendingAction && mutationError ? <ErrorBanner message={mutationError} onDismiss={() => mutation.reset()} /> : null}
      <div className="managed-package-deployments__rows">
        {harnesses.map((deployment) => (
          <DeploymentRow
            key={deployment.harness}
            deployment={deployment}
            disabled={controlsDisabled}
            onAction={(action, trigger) => {
              returnFocus.current = trigger;
              mutation.reset();
              setConfirmationError("");
              setPendingAction({ harness: deployment.harness, action });
            }}
          />
        ))}
      </div>
      {pendingAction && selectedHarness ? (
         <ConfirmActionDialog
           open
           title={copy.confirmTitle(
             pendingAction.action,
             harnessLabel(selectedHarness.harness),
              false,
           )}
           confirmLabel={copy.confirm}
           pendingLabel={copy.pending}
           isPending={mutation.isPending}
            confirmDisabled={factsUnavailable || Boolean(confirmationError) || !isActionAllowed(selectedHarness, pendingAction.action)
              || (pendingAction.action === "update" && !pendingAction.replacementPackageId)}
           confirmTone={pendingAction.action === "remove" ? "danger" : "primary"}
            description={(
              <>
              {mutationError || confirmationError ? <ErrorBanner message={mutationError || confirmationError} /> : null}
              <DeploymentConfirmationDetails
               deployment={selectedHarness}
                packageId={packageId}
                packageSource={packageSource}
                action={pendingAction.action}
               replacementPackageId={pendingAction.replacementPackageId}
               onReplacementChange={(replacementPackageId) => setPendingAction((current) => (
                 current ? { ...current, replacementPackageId: replacementPackageId || undefined } : current
               ))}
              />
              </>
           )}
           note={pendingAction.action === "remove" || pendingAction.action === "disable" ? copy.centralNote : undefined}
            onOpenChange={(open) => {
             if (!open && !mutation.isPending) setPendingAction(null);
            }}
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              (returnFocus.current?.isConnected ? returnFocus.current : sectionRef.current)?.focus();
            }}
          onConfirm={confirmAction}
        />
      ) : null}
    </section>
  );
}

function DeploymentRow({
  deployment,
  disabled,
  onAction,
}: {
  deployment: PackageDeploymentHarnessResponse;
  disabled: boolean;
  onAction: (action: DeploymentAction, trigger: HTMLButtonElement) => void;
}) {
  const copy = useSkillsCopy().deployment;
  const hasExternalOwnership = deployment.state === "external-existing";
  const isBlocked = !isActionableState(deployment) || deployment.actions.length === 0;

  return (
    <article className="managed-package-deployment-row" data-state={deployment.state}>
      <div className="managed-package-deployment-row__header">
        <div>
          <h4>{harnessLabel(deployment.harness)}</h4>
          <span className="managed-package-deployment-row__method">{methodLabel(deployment.strategy, copy)}</span>
        </div>
        <span className="package-status-chip" data-state={deployment.state}>
          {hasExternalOwnership ? copy.external : copy.status(deployment.state)}
        </span>
      </div>
      <details><summary>{copy.detailsLabel}</summary>
      <dl className="managed-package-deployment-row__metadata">
        <div><dt>{copy.support}</dt><dd>{copy.status(deployment.support)}</dd></div>
        <div><dt>{copy.ownership}</dt><dd>{deployment.state === 'manual' && deployment.ownership === 'conflict'
          ? copy.notVerified : copy.status(deployment.ownership)}</dd></div>
        {deployment.selectedPackageId ? <div><dt>{copy.selectedPackage}</dt><dd><code>{deployment.selectedPackageId}</code></dd></div> : null}
      </dl>
      </details>
      {hasExternalOwnership ? <p className="managed-package-deployment-row__note">{copy.externalNote}</p> : null}
      {deployment.preflight.length > 0 ? (
        <div className="managed-package-deployment-row__details">
          <strong>{copy.preflight}</strong>
          <ul>{deployment.preflight.map((item) => <li key={item}>{formatDiagnostic(item)}</li>)}</ul>
        </div>
      ) : null}
      {deployment.blockers.length > 0 ? (
        <div className="managed-package-deployment-row__details" data-tone="warning">
          <strong>{copy.blockers}</strong>
          <ul>{deployment.blockers.map((item) => <li key={item}>{formatDiagnostic(item)}</li>)}</ul>
        </div>
      ) : null}
      {isBlocked ? (hasExternalOwnership ? null : (
        <p className="managed-package-deployment-row__note">
          {copy.noActions}
        </p>
      )) : (
        <div className="managed-package-deployment-row__actions">
          {deployment.actions.map((action) => (
            <button
              key={action}
              type="button"
              className={`action-pill action-pill--sm${action === "remove" ? " action-pill--danger" : ""}`}
              disabled={disabled}
              onClick={(event) => onAction(action, event.currentTarget)}
            >
               {actionLabel(copy, action, deployment)}
            </button>
          ))}
        </div>
      )}
    </article>
  );
}

function harnessLabel(value: Harness): string {
  return {
    claude: "Claude",
    codex: "Codex",
    cursor: "Cursor",
    opencode: "OpenCode",
  }[value];
}

function methodLabel(value: PackageDeploymentHarnessResponse["strategy"], copy: ReturnType<typeof useSkillsCopy>["deployment"]): string {
  if (value === "native-local") return copy.localMethod;
  if (value === "native-install") return copy.installMethod;
  return copy.unsupported;
}

function actionLabel(
  copy: ReturnType<typeof useSkillsCopy>["deployment"],
  action: DeploymentAction,
  deployment: PackageDeploymentHarnessResponse,
): string {
  if (action === "deploy") return copy.deploy;
  if (action === "enable") return copy.enable;
  if (action === "disable") return copy.disable;
  if (action === "remove") return copy.remove;
  return deployment.replacementOptions?.length ? copy.update : copy.redeployCurrent;
}

function isActionableState(deployment: PackageDeploymentHarnessResponse): boolean {
  return deployment.support === "supported" && !BLOCKED_STATES.has(deployment.state);
}

function isActionAllowed(
  deployment: PackageDeploymentHarnessResponse,
  action: DeploymentAction,
): boolean {
  return isActionableState(deployment) && deployment.actions.includes(action);
}

function replacementForAction(
  deployment: PackageDeploymentHarnessResponse,
  explicitReplacementPackageId: string | undefined,
): string | null {
  if (!explicitReplacementPackageId) {
    return null;
  }
  const isProvenOption = deployment.replacementOptions?.some(
    (option) => option.packageId === explicitReplacementPackageId,
  );
  return isProvenOption ? explicitReplacementPackageId : null;
}

function DeploymentConfirmationDetails({
  deployment,
  packageId,
  packageSource,
  action,
  replacementPackageId,
  onReplacementChange,
}: {
  deployment: PackageDeploymentHarnessResponse;
  packageId: string;
  packageSource: PackageSourceResponse | null;
  action: DeploymentAction;
  replacementPackageId?: string;
  onReplacementChange: (packageId: string) => void;
}): ReactNode {
  const copy = useSkillsCopy().deployment;
  const options = deployment.replacementOptions ?? [];
  const snapshot = replacementPackageId ?? deployment.selectedPackageId ?? packageId;
  const selectedSource = options.find((option) => option.packageId === replacementPackageId)?.source;
  return (
    <div className="managed-package-deployment-confirmation">
      <p>{copy.confirmDescription}</p>
      <dl>
        <div><dt>{copy.confirmSource}</dt><dd>{sourceDetails(selectedSource ?? deployment.source ?? packageSource, copy.confirmNoSource)}</dd></div>
        <div><dt>{copy.confirmSnapshot}</dt><dd><code>{snapshot}</code></dd></div>
        <div><dt>{copy.confirmMethod}</dt><dd>{methodLabel(deployment.strategy, copy)}</dd></div>
      </dl>
      {action === "update" ? (
        options.length > 0 ? (
          <label className="managed-package-deployment-confirmation__replacement">
            <span>{copy.replacement}</span>
            <select
              value={replacementPackageId ?? ""}
              onChange={(event) => onReplacementChange(event.target.value)}
            >
              <option value="" disabled>{copy.replacement}</option>
              {options.map((option) => (
                <option key={option.packageId} value={option.packageId}>
                  {replacementLabel(option, copy.confirmNoSource)}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <p className="managed-package-deployment-confirmation__muted">{copy.noReplacementOptions}</p>
        )
      ) : null}
      <div className="managed-package-deployment-confirmation__preflight">
        <strong>{copy.confirmPreflight}</strong>
        {deployment.preflight.length > 0 ? (
          <ul>{deployment.preflight.map((item, index) => <li key={`${item}:${index}`}>{item}</li>)}</ul>
        ) : <p>{copy.confirmNoPreflight}</p>}
      </div>
    </div>
  );
}

function sourceDetails(
  source: PackageDeploymentHarnessResponse["source"],
  unavailable: string,
): string {
  if (!source) return unavailable;
  return [
    source.kind,
    source.locator,
    source.version ? `v${source.version}` : null,
    source.ref ? `ref ${source.ref}` : null,
    source.revision,
    source.package_path ? `path ${source.package_path}` : null,
  ].filter(Boolean).join(" · ");
}

function replacementLabel(option: PackageReplacementOption, unavailable: string): string {
  if (option.label) return option.label;
  return sourceDetails(option.source, unavailable) || option.packageId;
}

function formatDiagnostic(value: string): string {
  return value.replace(/[-_]/g, " ");
}
