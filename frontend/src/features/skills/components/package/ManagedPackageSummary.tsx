import type { ManagedPackageResponse } from "../../api/package-types";
import { useSkillsCopy } from "../../i18n";
import type { ReactNode } from "react";

interface ManagedPackageSummaryProps {
  packageData: ManagedPackageResponse;
  skillNames?: Record<string, string>;
}

export function ManagedPackageSummary({ packageData, skillNames = {} }: ManagedPackageSummaryProps) {
  const copy = useSkillsCopy().packages;
  const packageName = packageData.capabilities?.name ?? packageData.source.package_path ?? packageData.id;
  const capabilityCount = packageData.capabilities?.components.length ?? 0;

  return (
    <div className="managed-package-summary" data-state={packageData.state}>
      <div className="managed-package-summary__identity">
        <div>
          <h3>{packageName}</h3>
          <p className="managed-package-summary__source">
             {copy.sourceSummary(packageData.source.kind, packageData.source.locator)}
          </p>
        </div>
        <span className="package-status-chip" data-state={packageData.state}>
          {copy.status(packageData.state)}
        </span>
      </div>

      <dl className="managed-package-summary__metadata">
         <PackageField label={copy.source} value={sourceDetails(packageData.source, copy)} />
         <PackageField label={copy.version} value={versionDetails(packageData, copy)} />
         <PackageField label={copy.artifact} value={packageData.artifactState} />
        <PackageField label={copy.upstream} value={packageData.upstreamState} />
        <PackageField label={copy.resolution} value={packageData.resolutionStatus} />
        <PackageField label={copy.ownership} value={packageData.ownership} />
      </dl>
      <details>
        <summary>{copy.detailsLabel}</summary>
        <dl className="managed-package-summary__metadata">
          <PackageField label={copy.package} value={packageData.id} />
          <PackageField label={copy.fingerprint} value={packageData.fingerprint ?? copy.notAvailable} />
        </dl>
      </details>

      {packageData.reason ? <p className="managed-package-summary__note">{packageData.reason}</p> : null}

      {packageData.candidateSource ? (
        <div className="managed-package-summary__callout" data-tone="warning">
          <strong>{copy.candidate}</strong>
          <code>{sourceDetails(packageData.candidateSource, copy)}</code>
          <p>{copy.candidatePending}</p>
        </div>
      ) : null}

      <SummaryList title={copy.associatedSkills}>
        {packageData.skillRefs.length > 0 ? (
            <ul className="managed-package-summary__list">
             {packageData.skillRefs.map((skillRef) => (
               <li key={skillRef}>
                 {skillNames[skillRef] ? <strong>{skillNames[skillRef]}</strong> : null}
                 <code className="managed-package-summary__secondary">{skillRef}</code>
               </li>
             ))}
          </ul>
        ) : <p className="managed-package-summary__muted">{copy.noObservations}</p>}
      </SummaryList>

      <SummaryList title={`${copy.capabilities}${capabilityCount > 0 ? ` · ${copy.capabilitySummary(capabilityCount)}` : ""}`}>
        {packageData.capabilities ? (
          <div className="managed-package-summary__capabilities">
            {packageData.capabilities.components.length > 0 ? (
              packageData.capabilities.components.map((component, index) => (
                <span key={`${component.kind}:${component.path}:${index}`} className="package-capability-chip">
                  {component.kind}{component.harness ? ` · ${component.harness}` : ""}
                </span>
              ))
            ) : <p className="managed-package-summary__muted">{copy.noCapabilities}</p>}
          </div>
        ) : <p className="managed-package-summary__muted">{copy.noCapabilities}</p>}
      </SummaryList>

      <SummaryList title={copy.distributions}>
        {packageData.distributions.length > 0 ? (
          <ul className="managed-package-summary__list">
            {packageData.distributions.map((distribution, index) => (
              <li key={`${distribution.harness ?? "shared"}:${distribution.relationship}:${index}`}>
                 <strong>{distribution.harness ?? copy.sharedPackage}</strong>
                <span>{distribution.relationship}</span>
                 <code>{sourceDetails(distribution.source, copy)}</code>
              </li>
            ))}
          </ul>
        ) : <p className="managed-package-summary__muted">{copy.noDistributions}</p>}
      </SummaryList>

      <SummaryList title={copy.observations}>
        {packageData.observations.length > 0 ? (
          <ul className="managed-package-summary__list">
            {packageData.observations.map((observation, index) => (
              <li key={`${observation.harness ?? "unknown"}:${observation.path ?? index}`}>
                 <strong>{observation.harness ?? copy.unknownHarness}</strong>
                <span>{observation.ownership}</span>
                {observation.path ? <code>{observation.path}</code> : null}
              </li>
            ))}
          </ul>
        ) : <p className="managed-package-summary__muted">{copy.noObservations}</p>}
      </SummaryList>

      {packageData.limitations.length > 0 ? (
         <SummaryList title={copy.limitations}>
          <ul className="managed-package-summary__diagnostics">
            {packageData.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}
          </ul>
        </SummaryList>
      ) : null}
    </div>
  );
}

function PackageField({ label, value }: { label: string; value: string }) {
  return (
    <div className="managed-package-summary__field">
      <dt>{label}</dt>
      <dd><code>{value}</code></dd>
    </div>
  );
}

function SummaryList({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="managed-package-summary__subsection">
      <h4>{title}</h4>
      {children}
    </section>
  );
}

function sourceDetails(
  source: ManagedPackageResponse["source"],
  copy: ReturnType<typeof useSkillsCopy>["packages"],
): string {
  return [
    source.locator,
    source.version ? copy.sourceVersion(source.version) : null,
    source.ref ? copy.sourceRef(source.ref) : null,
    source.revision ? copy.sourceRevision(source.revision) : null,
    source.package_path ? copy.sourcePath(source.package_path) : null,
  ].filter(Boolean).join(" · ");
}

function versionDetails(
  packageData: ManagedPackageResponse,
  copy: ReturnType<typeof useSkillsCopy>["packages"],
): string {
  return [
    packageData.source.version ?? packageData.capabilities?.version,
    packageData.source.ref,
    packageData.source.revision,
  ].filter(Boolean).join(" · ") || copy.noVersionDeclared;
}
