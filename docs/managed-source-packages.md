# Managed whole packages

Packages have a separate central store from standalone Skills. Only successful
[source resolution](package-source-resolution.md) establishes an authoritative source.
Local observations, native installations and temporary capability-map root IDs do
not establish central ownership.

## Storage and identity

The configured application data directory contains:

```text
packages/
  manifest.json              # version 1: package records and Skill relationships
  manifest.lock
  artifacts/<package-id>/    # whole retained authoritative package
  deployments.json          # separate native deployment records, not source authority
```

The existing `shared/` Skill store, Skill manifest and SQLite scan-configuration
schema are unchanged. No migration or discovery-driven takeover runs at startup.
The package store follows the existing file-lock and atomic JSON-write patterns.

Package IDs hash source kind, canonical locator, exact acquired revision, version,
repository-relative package path and integrity. They exclude temporary paths,
local-root IDs, individual Skill paths and branch aliases. Two independent
acquisitions of the same source snapshot share an ID; another revision has another
ID. Each observed Skill has its own source-Skill path/link to that package.

Records retain authoritative source coordinates, allowlisted source evidence and
distribution relationships, structural capabilities (without temporary root
or ID), and a fingerprint over retained paths, bytes and executable bits. They
also retain the last upstream status, reason and any changed-source candidate.
Relationships do not infer package families or native support from names.

## Capture and ownership

Capture takes place before the acquisition workspace ends. It copies the proven
whole artifact into a staging directory, verifies its fingerprint, renames it to
the owned destination, then atomically writes the ownership manifest. Supporting
files, empty directories and executable bits survive, even when no capability
parser interprets them. No Skill/MCP/hook/command reconstruction takes place.

The acquisition exclusion policy is shared: source-control/dependency caches, common
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
- An observed package or explicit local package provenance routes to
  source resolution and whole-package capture before any standalone binding
  operation. Capture still requires successful authoritative resolution.
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

Native actions are separate operations on the Managed packages page; they do not
turn package-managed observations into standalone bindings. See the
[package guide](package-management-ux.md) for controls and support boundaries.

## Read model and refresh

- `GET /api/skills/managed-packages` returns package records and per-Skill links,
  including failed resolution attempts without fabricated package IDs/artifacts.
- `POST /api/skills/managed-packages/{id}/refresh` checks source state through the resolver.
- The same operations are available on `SkillsQueryService`; capture is handled
  by `ManagedPackageStore` through `SkillsMutationService.manage_source_package`.

`artifactState` is current, changed, missing, unavailable or not-retained.
`upstreamState` independently reports current, changed, unavailable, unresolved
or ambiguous. The overall `state` prioritises a retained artifact problem; it does
not hide an intact retained artifact merely because the source is offline.
`artifactRoot` is derived from the owned store, never a saved temporary path.
A resolved source without an available artifact can retain its coordinates with
an explicit not-retained/unavailable state and null fingerprint.

When an original observation is available, refresh calls the existing resolver
to detect changed declared source/ref/version. A resolved comparison also checks
the artifact fingerprint, capabilities and explicit distributions. Differences
produce a `candidateSource` and changed status; failed resolution instead reports
its failure state and preserves any existing candidate. Neither replaces the
owned source/artifact or alters a harness. If the observation disappears, the
same resolver reacquires from the saved authoritative source, including its exact
revision and package path. Verifying an old
pinned snapshot does not erase a previously known changed-source candidate.
Failed refresh or associating another Skill likewise preserves that candidate.

Local fingerprint checks run on reads. External observation presence is reported
separately. Package records remain queryable when original Skills disappear from
the live inventory. New revisions are separately adoptable records; no automatic
garbage collection or native action runs as part of central refresh.

## Native deployment boundary

Use the managed source coordinates, retained fingerprint/artifact state, recorded
capabilities, explicit distribution links and external observations. Neither
Managed nor a discovered capability means a native deployment strategy is supported.
Source-change candidates require review; refresh never applies them automatically.
The resolver's public GitHub/npm support and unsupported-source limits still apply.

The [native deployment service](native-package-deployment.md) keeps only references
to these authoritative records, plus per-harness placement, ownership and verified
state. It may create an owned whole copy or let a native installer maintain its
own cache; neither replaces the central snapshot. Update requires an explicitly
selected, currently valid managed replacement. Pending candidates and drift block
mutation rather than being force-applied. After a verified removal, a fresh deploy
can restore an owned installation if current source and target checks still pass.
Disabling or removing one harness does not remove central management or other
harness deployments. Component fallback and LLM source selection are not current
deployment mechanisms.
