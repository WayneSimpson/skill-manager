import { useMemo } from "react";

import { ErrorBanner } from "../../../components/ErrorBanner";
import { LoadingSpinner } from "../../../components/LoadingSpinner";
import { PageHeader } from "../../../components/PageHeader";
import { useSkillsCopy } from "../i18n";
import { useManagedPackagesQuery, useRefreshManagedPackageMutation } from "../api/queries";
import { ManagedPackageSummary } from "../components/package/ManagedPackageSummary";
import { ManagedPackageDeployments } from "../components/package/ManagedPackageDeployments";

export default function ManagedPackagesPage() {
  const copy = useSkillsCopy().packages;
  const packagesQuery = useManagedPackagesQuery();
  const skillNames = useMemo(
    () => Object.fromEntries(Object.entries(packagesQuery.data?.skills ?? {}).map(([ref, link]) => [ref, link.name])),
    [packagesQuery.data?.skills],
  );

  return (
    <>
      <div className="page-chrome">
        <PageHeader title={copy.title} subtitle={copy.subtitle} />
      </div>

      {packagesQuery.error && packagesQuery.data ? <ErrorBanner message={copy.refreshFailed} /> : null}

      {packagesQuery.isPending && !packagesQuery.data ? (
        <div className="panel-state" aria-busy="true">
          <LoadingSpinner size="md" label={copy.loading} />
        </div>
      ) : packagesQuery.error && !packagesQuery.data ? (
        <div className="panel-state">
          <ErrorBanner message={packagesQuery.error instanceof Error ? packagesQuery.error.message : copy.unableToLoad} />
        </div>
      ) : packagesQuery.data?.packages.length ? (
        <section className="managed-packages-list" aria-label={copy.title}>
          {packagesQuery.data.packages.map((packageData) => (
            <ManagedPackageCard
              key={packageData.id}
              packageData={packageData}
              skillNames={skillNames}
            />
          ))}
        </section>
      ) : (
        <div className="empty-panel">
          <h2 className="empty-panel__title">{copy.emptyTitle}</h2>
          <p className="empty-panel__body">{copy.emptyBody}</p>
        </div>
      )}
    </>
  );
}

function ManagedPackageCard({
  packageData,
  skillNames,
}: {
  packageData: import("../api/package-types").ManagedPackageResponse;
  skillNames: Record<string, string>;
}) {
  const copy = useSkillsCopy().packages;
  const refreshMutation = useRefreshManagedPackageMutation();
  const refreshError = refreshMutation.error instanceof Error ? refreshMutation.error.message : "";

  return (
    <article className="managed-package-card">
      <div className="managed-package-card__header">
        <div>
          <span className="managed-package-card__eyebrow">{copy.managed}</span>
        </div>
        <button
          type="button"
          className="action-pill action-pill--sm"
          disabled={refreshMutation.isPending}
          onClick={() => {
            refreshMutation.reset();
            void refreshMutation.mutate({ packageId: packageData.id });
          }}
          aria-busy={refreshMutation.isPending}
        >
          {refreshMutation.isPending ? <LoadingSpinner size="sm" label={copy.refreshing} /> : null}
          {refreshMutation.isPending ? copy.refreshing : copy.refresh}
        </button>
      </div>
      {refreshError ? <ErrorBanner message={refreshError} onDismiss={() => refreshMutation.reset()} /> : null}
      <ManagedPackageSummary packageData={packageData} skillNames={skillNames} />
       <ManagedPackageDeployments packageId={packageData.id} packageSource={packageData.source} />
    </article>
  );
}
