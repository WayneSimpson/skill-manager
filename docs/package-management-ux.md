# Package Management UX

Package context is available from the skill detail view. A normal context read
uses only the local inventory and retained package manifest; it does not fetch
from an upstream repository. The **Resolve package** action is the explicit
operation that may inspect the declared upstream source. Its response is
temporary review data: scratch checkout paths and artifacts are never exposed
or retained by the API.

## States shown to users

- **Standalone** — no deterministic package evidence was found. The skill keeps
  the existing individual-skill controls.
- **Package-backed** — the skill is associated with a discovered or retained
  package. Individual-skill management controls are not offered.
- **Resolved** — an exact upstream source and revision are known.
- **Unresolved** — package evidence exists, but an exact source is not proven.
- **Unavailable** — the source was known but could not be accessed. The reason
  is retained so the user can see why no fallback was performed.
- **Ambiguous** — declarations disagree or identify more than one package.

The package detail view should show the source, revision, evidence, and
limitations returned by the API. It must not turn a package-backed skill into a
standalone skill when resolution or deployment fails.

An already-managed standalone Skill keeps its own controls even if a wider
package is later discovered. It is not silently migrated into package ownership.
Central package deletion is not offered; deployment removal affects only the
named harness, and the UI states that central management is retained.

## Native deployment controls

The managed-package deployment view reports one row per supported harness:

- `absent` — no managed deployment is recorded and the native target is safe to
  use.
- `installed`, `enabled`, or `disabled` — a persisted deployment was freshly
  reconciled and ownership was proved.
- `stale` — the package artifact, upstream revision, or distribution changed.
- `external-existing` — a matching native installation exists without proven
  Skill Manager ownership.
- `conflict` — current native facts do not prove that the persisted deployment
  is still owned by Skill Manager.
- `manual` or `unsupported` — the harness cannot safely be changed with the
  evidence and mechanisms currently available.

Deploy, update, enable, disable, and remove are server-side actions. The
browser sends only the action and, for update, an explicit replacement package
ID. The server performs a fresh plan and reconciliation before mutating a
target. Browser-supplied plans, roots, executables, and native identifiers are
not accepted.

Cursor remains Manual without verified GUI/policy support. OpenCode registers
a staged whole package through its native global file-URL mechanism. External
or unverified installations are reported without takeover or deletion.

## HTTP and runtime setup

Task08 adds `GET /api/skills/{ref}/package-context` and explicit
`POST /api/skills/{ref}/resolve-package`. The existing `manage-package` operation
captures the complete authoritative snapshot. A GitHub locator alone does not
change ordinary standalone Adopt; source review is an explicit operation.

`GET /api/skills/managed-packages/{id}/deployments` returns current plans and
reconciliation, not saved install booleans. `POST` to that URL plus `/{harness}`
accepts only an action and an explicit replacement ID. It calls Task07 directly.
The response follows the replacement package after a successful update. UI caches
are refreshed after both successful and failed native actions; errors disable
controls until current checks succeed. Source-check failures have an explicit retry.

Replacement choices are existing managed snapshots with the same source kind,
explicit GitHub locator or npm registry coordinate, and package path, plus a
successful current owned-target plan. npm's version is separate from its registry
name for this comparison; display names are never used. The server rejects a
replacement outside these currently verified choices. Each replacement requires user selection and confirmation. A
same-snapshot verification-only repeat is not advertised as a version update.
Changed artifacts, pending source candidates and unresolved old snapshots remain
blocked under Task07; the UI does not clear those safeguards or apply candidates.
Unscoped publisher source links are not presented as native harness support.

Native execution is opt-in server configuration, never a browser-supplied path.
For Claude, Codex or OpenCode, an operator may configure:

```text
SKILL_MANAGER_NATIVE_<CLAUDE|CODEX|OPENCODE>_EXECUTABLE=<absolute verified raw binary>
SKILL_MANAGER_NATIVE_<CLAUDE|CODEX|OPENCODE>_ROOT=<intended native configuration directory>
SKILL_MANAGER_NATIVE_<CLAUDE|CODEX|OPENCODE>_ALLOW_MUTATION=true
```

If ROOT is omitted, the existing harness configuration binding supplies it.
Native support is determined by the actual mechanism, complete native inventory,
and adapter schema/provenance checks. Version output may be retained as
diagnostic evidence, but a version string is not a permission gate; unknown or
newer versions are not blocked solely for that reason. The current runner
supports raw Linux ELF binaries only; scripts, command wrappers and linked paths
are rejected. No executable is discovered or invoked automatically.

The runner uses a 30-second timeout, no stdin/shell, an explicit environment and
a temporary HOME/XDG/temp/working directory. `CLAUDE_CONFIG_DIR` or `CODEX_HOME`
binds only the intended configuration. This prevents ambient credentials/preloads
and project working-directory inheritance; it is **not a filesystem/network
sandbox**. Explicit configuration authorizes native inventory queries as well as
later user-confirmed writes. No such setting was enabled for the real user during
Task08 validation. Cursor has no default executor. OpenCode checks the actual
global config binding and plugin command, then registers the whole owned directory
with the singular `plugin` key. Updates retain its exact URI. Removal deletes only
that recorded string, preserving other JSONC bytes, comments and order.
Registration-origin proof uses the existing optional native fingerprint; the
ledger format is unchanged. Auto-loaded JS/TS plugin files and unresolved controls
block incomplete inventory. No OpenCode enable/disable control is offered.
A fresh native process must report the registration without initialization errors.
The isolated verifier uses a read-only disposable config during runtime inspection
to avoid SDK dependency bootstrap. Production code never changes user permissions;
dependency-bearing packages require a working native dependency environment.

## Validation boundary

`tests/integration/test_package_deployment_api.py` drives real HTTP, Task05 store,
Task06 planner and Task07 lifecycle with simulated native process responses only.
It covers two native mechanisms, externally changed enablement, disabled-preserving
replacement, exact separate distributions, legacy OpenCode evidence, stale artifacts,
cache conflicts and retention of central ownership. `test_package_api.py` covers
standalone context, explicit GitHub resolution without a local manifest, and all
source failure states without leaf adoption. Frontend tests cover confirmation,
replacement selection, unavailable/refetching facts and package-aware routing.

Browser checks use `AppTestHarness` with the same isolated fixture and the built
frontend. They are UX/API checks, not new real-harness compatibility claims.
Task07's accepted real CLI evidence remains the support boundary. Full source-to-
real-harness programme verification is still Task09 and was not started here.
