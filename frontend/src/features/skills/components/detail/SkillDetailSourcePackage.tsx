import { DetailDisclosure } from "../../../../components/detail/DetailDisclosure";
import { DetailNote } from "../../../../components/detail/DetailNote";
import { DetailSection } from "../../../../components/detail/DetailSection";
import { useSkillsCopy, type SkillsCopy } from "../../i18n";
import type { SkillSourcePackage } from "../../model/types";

interface SkillDetailSourcePackageProps {
  sourcePackage: SkillSourcePackage;
}

type ResolvedPackage = NonNullable<SkillSourcePackage["package"]>;
type PackageComponent = ResolvedPackage["components"][number];
type SourcePackageCopy = SkillsCopy["detail"]["sourcePackage"];

export function SkillDetailSourcePackage({ sourcePackage }: SkillDetailSourcePackageProps) {
  const copy = useSkillsCopy().detail.sourcePackage;
  const resolvedPackage = sourcePackage.package;

  return (
    <DetailSection heading={copy.title}>
      <dl className="skill-detail__source-package-metadata">
        <SourcePackageField label={copy.sourceKind} value={sourcePackage.sourceKind} />
        <SourcePackageField label={copy.originalSkillSource} value={sourcePackage.sourcePath ?? copy.unavailable} />
        <SourcePackageField label={copy.sourceRevision} value={sourcePackage.sourceRevision ?? copy.unavailable} />
        {resolvedPackage ? <ResolvedPackageMetadata packageData={resolvedPackage} copy={copy} /> : null}
      </dl>

      {resolvedPackage ? (
        <>
          <DetailNote>{copy.packageHooksNote}</DetailNote>
          <DetailDisclosure title={copy.components} defaultOpen>
            <div className="skill-detail__source-package-components">
              {resolvedPackage.components.length > 0 ? (
                resolvedPackage.components.map((component) => (
                  <PackageComponentRow key={componentKey(component)} component={component} copy={copy} />
                ))
              ) : (
                <p className="skill-detail__copy">{copy.unavailable}</p>
              )}
            </div>
          </DetailDisclosure>
          <DetailDisclosure title={copy.manifests}>
            <div className="skill-detail__source-package-list">
              {resolvedPackage.manifests.map((manifest) => (
                <div
                  key={`${manifest.path}:${manifest.format}:${manifest.evidence}`}
                  className="skill-detail__source-package-list-row"
                >
                  <code>{manifest.path}</code>
                  <span>{manifest.format}</span>
                  <span>{copy.evidence(manifest.evidence)}</span>
                </div>
              ))}
            </div>
          </DetailDisclosure>
          {resolvedPackage.diagnostics.length > 0 ? (
            <DetailDisclosure title={copy.diagnostics}>
              <ul className="skill-detail__source-package-diagnostics">
                {resolvedPackage.diagnostics.map((diagnostic) => <li key={diagnostic}>{diagnostic}</li>)}
              </ul>
            </DetailDisclosure>
          ) : null}
        </>
      ) : (
        <DetailNote>
          <strong>{copy.unresolvedTitle}</strong>
          <p className="skill-detail__source-package-reason">
            {sourcePackage.reason ?? copy.unavailable}
          </p>
          <p className="skill-detail__source-package-reason">{copy.unresolvedExplanation}</p>
        </DetailNote>
      )}
    </DetailSection>
  );
}

function ResolvedPackageMetadata({
  packageData,
  copy,
}: {
  packageData: ResolvedPackage;
  copy: SourcePackageCopy;
}) {
  return (
    <>
      <SourcePackageField label={copy.sourceRoot} value={packageData.root} />
      <SourcePackageField label={copy.packageName} value={`${packageData.name} · ${copy.packageVersion(packageData.version)}`} />
      <SourcePackageField label={copy.packageEvidence} value={copy.evidence(packageData.evidence)} />
      <SourcePackageField label={copy.packageRevision} value={packageData.revision} />
    </>
  );
}

function SourcePackageField({ label, value }: { label: string; value: string }) {
  return (
    <div className="skill-detail__source-package-field">
      <dt>{label}</dt>
      <dd><code>{value}</code></dd>
    </div>
  );
}

function PackageComponentRow({
  component,
  copy,
}: {
  component: PackageComponent;
  copy: SourcePackageCopy;
}) {
  return (
    <article className="skill-detail__source-package-component">
      <div className="skill-detail__source-package-component-header">
        <strong>{copy.componentKind(component.kind)}</strong>
        <span>{capabilityLabel(component, copy)}</span>
      </div>
      <code className="skill-detail__source-package-path">{component.path}</code>
      {component.entries?.length ? (
        <ul className="skill-detail__source-package-list">
          {component.entries.map((name) => <li key={name}><code>{name}</code></li>)}
        </ul>
      ) : null}
      <span className="skill-detail__source-package-detail">
        {component.manifest} · {copy.evidence(component.evidence)}
      </span>
    </article>
  );
}

function capabilityLabel(
  component: PackageComponent,
  copy: SourcePackageCopy,
): string {
  if (component.harness !== null) {
    return copy.harnessSpecific(component.harness);
  }
  if (component.kind === "skills" || component.kind === "mcp") {
    return copy.portableShared;
  }
  return copy.unknownExtensionNotPortable;
}

function componentKey(component: PackageComponent): string {
  return `${component.kind}:${component.harness ?? "shared"}:${component.path}:${component.manifest}`;
}
