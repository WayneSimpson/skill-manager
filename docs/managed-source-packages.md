# Managed whole packages (Task 05)

Packages have a separate central store from standalone Skills. Only a successful
04B `PackageResolution` establishes an authoritative source. Local observations,
native installations and temporary 04A root IDs do not establish central ownership.

## Storage and identity

The configured application data directory now contains:

```text
packages/
  manifest.json              # version 1: package records and Skill relationships
  manifest.lock
  artifacts/<package-id>/    # whole retained authoritative package
```

The existing `shared/` Skill store, Skill manifest and SQLite scan-configuration
schema are unchanged. No migration or discovery-driven takeover runs at startup.
The package store follows the existing file-lock and atomic JSON-write patterns.

Package IDs hash source kind, canonical locator, exact acquired revision, version,
repository-relative package path and integrity. They exclude temporary paths,
04A root IDs, individual Skill paths and branch aliases. Two independent 04B
acquisitions of the same source snapshot share an ID; another revision has another
ID. Each observed Skill has its own source-Skill path/link to that package.

Records retain authoritative source coordinates, allowlisted 04B evidence and
distribution relationships, 04A structural capabilities (without temporary root
or ID), and a fingerprint over retained paths, bytes and executable bits. They
also retain the last upstream status, reason and any changed-source candidate.
Relationships do not infer package families or native support from names.

## Capture and ownership

Capture takes place before the 04B temporary workspace ends. It copies the proven
whole artifact into a staging directory, verifies its fingerprint, renames it to
the owned destination, then atomically writes the ownership manifest. Supporting
files, empty directories and executable bits survive, even when no capability
parser interprets them. No Skill/MCP/hook/command reconstruction takes place.

04B's exclusion policy is shared: source-control/dependency caches, common
credential/config paths, `.env*`, `.pem` and `.key` files are excluded. `.envrc`
is included in the environment-file exclusion. Links and special files are
rejected; size/file/depth limits apply. These checks never execute package code.
This is not a content-based secret scanner or native-loader validation.

Only paths derived from the configured store and a validated ID are destinations.
Symlinked storage is rejected. Existing artifacts without an ownership record
are not overwritten or adopted. Modified managed artifacts are not overwritten.
A failed manifest write removes only the artifact created by that operation.
A process crash leaving an unrecorded artifact requires manual inspection; startup
does not claim or remove it automatically. Successful snapshots survive original
source disappearance and restart.

## Adopt routing

- Ordinary Adopt (`POST /api/skills/{ref}/manage`) retains the existing standalone
  and content-only workflow when there is no package evidence.
- An observed 04A package or explicit local package provenance from 04B routes to
  whole-package capture before any standalone binding operation.
- A failed package resolution is recorded as unresolved/ambiguous/unavailable and
  returns a conflict. It never falls back to copying the observed leaf Skill.
- `POST /api/skills/{ref}/manage-package` explicitly requests whole-package Adopt,
  including marketplace/Git source locators whose local leaf has no package
  manifest. A Git locator alone does not imply that ordinary standalone Adopt
  should become a package operation.
- Existing centrally managed standalone Skills are not silently migrated by this
  endpoint. Their ownership/deployment lifecycle remains independent.

Package-managed observations appear Managed, but their harness cells remain
found/non-interactive. Individual-Skill enable/disable operations are rejected.
The detail view explains central package ownership and external native installs.
Package adoption never invokes harness adapters to install, replace, delete or
take ownership of original paths. `external-existing` describes observed harness
files; it is not proof that a native plugin registration/runtime was verified.

Two 04A API tests retain their capability/provenance assertions but now exercise
authoritative package capture and rejected leaf toggles. Their old leaf-copy
expectation for a package-backed Skill was intentionally superseded by Task05.
The ordinary standalone adoption regressions remain unchanged.

## Read model and refresh

- `GET /api/skills/managed-packages` returns package records and per-Skill links,
  including failed resolution attempts without fabricated package IDs/artifacts.
- `POST /api/skills/managed-packages/{id}/refresh` checks source state through 04B.
- The same operations are available on `SkillsQueryService`; capture is handled
  by `ManagedPackageStore` through `SkillsMutationService.manage_source_package`.

`artifactState` is current, changed, missing, unavailable or not-retained.
`upstreamState` independently reports current, changed, unavailable, unresolved
or ambiguous. The overall `state` prioritises a retained artifact problem; it does
not hide an intact retained artifact merely because the source is offline.
`artifactRoot` is derived from the owned store, never a saved temporary path.
A resolved source without an available artifact can retain its coordinates with
an explicit not-retained/unavailable state and null fingerprint.

When an original observation is available, refresh calls the existing 04B resolver
to detect changed declared source/ref/version. Such changes only create a
`candidateSource` and changed status: they do not replace the owned source/artifact
or alter a harness. If the observation disappears, 04B reacquires from the saved
authoritative source, including its exact revision and package path. This is a
small contract integration, not another provenance resolver. Verifying an old
pinned snapshot does not erase a previously known changed-source candidate.
Failed refresh or associating another Skill likewise preserves that candidate.

Local fingerprint checks run on reads. External observation presence is reported
separately. Package records remain queryable when original Skills disappear from
the live inventory. New revisions are separately adoptable records; no automatic
garbage collection or deployment reconciliation is implemented here.

## Boundaries for Task 06

Use the managed source coordinates, retained fingerprint/artifact state, recorded
capabilities, explicit distribution links and external observations. Neither
Managed nor a 04A capability means a native deployment strategy is supported.
Source-change candidates require review; refresh never applies them automatically.
04B's public GitHub/npm support and unsupported-source limits still apply.
No native deployment, component fallback, private registry support or LLM source
selection has been added.
