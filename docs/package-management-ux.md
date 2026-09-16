# Package management guide and support matrix

## From a local Skill to a native package

**Observation → deterministic source resolution → authoritative acquisition →
central managed package → whole-package native deployment → runtime verification.**

A local Skill need not have come from Skill Manager's marketplace. It may be an
extracted folder or one harness's copy of a larger package. Explicit repository,
Git or registry evidence can establish its upstream source; marketplace/GitHub
metadata is one route, not a requirement. Names alone never establish authority.

1. Open **Review package source** for a package-backed observation, then **Resolve source**.
2. Review the exact source, acquired revision/version and capability evidence.
   Failed resolution stays explicit; it does not copy the leaf Skill instead.
3. Choose **Manage package** to retain the whole authoritative snapshot centrally.
4. Open **Managed packages** under Skills and review each native target's method,
   ownership and blockers. A central managed copy is not yet a native installation.
5. Confirm an available whole-package action. The server checks fresh facts and
   verifies the result before recording ownership.

Capabilities explain contents; they are not separate install checkboxes. A harness
may read an owned whole copy or maintain its own native cache. This preserves
package-relative resources and lets the harness load its own hooks/agents/MCPs
without reproducing them through standalone managers.

Package context is available from the skill detail view. A normal context read
uses only the local inventory and retained package manifest; it does not fetch
from an upstream repository. The **Resolve source** action is the explicit
operation that may inspect the declared upstream source. Its response is
temporary review data: scratch acquisition paths and artifacts are never exposed
or retained by the API.

## Support by layer

These layers are independent. A discovered capability or resolved source is not
proof of a usable native deployment. The [README standalone tables](../README.md#supported-harnesses)
cover Skill/MCP/command management; native execution has its own Linux runner and
[harness lifecycle matrix](native-package-deployment.md#verified-native-support).

| Layer | Implemented scope | Limit or ownership boundary |
| --- | --- | --- |
| Skill discovery | Harness inventory; OpenCode static/configured roots plus explicit runtime refresh | A sighting is an observation, not source authority or ownership |
| Standalone adoption/binding | Existing shared-Skill store and per-harness links/toggles | Package-backed adoption does not fall back to this path; already-managed standalone Skills retain it |
| Package capability discovery | Deterministic standard/Claude/Cursor/Codex manifest contracts; rerun on acquired source | Structural explanation only; no component deployment or inferred OpenCode portability |
| Source/provenance resolution | Explicit marketplace/GitHub locators, local declarations/Git metadata, exact npm provenance | No fuzzy lookup; missing, conflicting or unsupported evidence remains explicit |
| Authoritative acquisition | Public GitHub pinned archives and exact npm integrity-checked tarballs | No private hosts, custom registries, arbitrary cache import or package execution |
| Central managed adoption | Complete acquired package, source pins, safe capabilities, Skill relationships | Central ownership does not claim the existing external native install |
| Native deployment | Whole-package Claude/Codex/OpenCode routes when all checks pass | Cursor Manual; no version gate; no unsafe-format or decomposition fallback |
| Real runtime verification | Isolated Claude/Codex active visibility; OpenCode server-entry/resource markers | No model-backed invocation claimed; source mocks and browser fixtures are not runtime evidence |
| External-existing detection | Exact source/path/native identity evidence; OpenCode exact-copy checks also prevent duplication | Names alone never match; incomplete evidence blocks instead of assuming absence or takeover |
| Manual/Unsupported cases | Concrete source, artifact, policy, executable, format or ownership blockers | No automatic retries, force-apply, external migration or invented toggle |

For implementation detail, see [source resolution](package-source-resolution.md),
[managed snapshots](managed-source-packages.md) and [native planning](native-package-strategies.md).

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

The managed-package deployment view reports one row for each known harness
(Claude, Codex, Cursor and OpenCode), even when a harness is Manual or
Unsupported:

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
not accepted. Only actions returned by the current plan are offered. OpenCode has
deploy/update/remove and no enable/disable control; Cursor remains Manual.
Claude's service/API update is verified, but its current UI row lacks Update
because the response builder checks for a `reinstall` method that this local-copy
adapter does not expose. Other Claude lifecycle actions and Codex's full set are
available when checks pass. See the [native support matrix](native-package-deployment.md#verified-native-support).

## HTTP and runtime setup

The review API provides `GET /api/skills/{ref}/package-context` and explicit
`POST /api/skills/{ref}/resolve-package`. The existing `manage-package` operation
captures the complete authoritative snapshot. A GitHub locator alone does not
change ordinary standalone Adopt; source review is an explicit operation.

`GET /api/skills/managed-packages/{id}/deployments` returns current plans and
reconciliation, not saved install booleans. `POST` to that URL plus `/{harness}`
accepts only an action and an explicit replacement ID. It calls `PackageDeploymentService`.
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
blocked; the UI does not clear those safeguards or apply candidates.
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
later user-confirmed writes. Cursor has no default executor. For OpenCode,
`XDG_CONFIG_HOME` is bound to the target root's parent and `OPENCODE_CONFIG_DIR`
to the target root; native `debug paths` must confirm that actual global binding.
Unknown auto-loaded JS/TS files or controls block incomplete inventory. This CLI
configuration is separate from the optional [runtime Skill server connection](opencode-runtime-skills.md).

Self-contained OpenCode packages were verified. Packages with dependencies still
need OpenCode's own working dependency environment; missing dependencies or native
errors cannot be reported as verified success. This is a runtime prerequisite,
not a change to package ownership. The [verification boundary](native-package-deployment.md#real-runtime-evidence)
explains the disposable test setup.

## Troubleshooting

| What you see | What to check |
| --- | --- |
| Source unresolved / ambiguous | Review the recorded source evidence and conflicting declarations. A similar marketplace name is not a fix. |
| Source unavailable | Read the reason and supported public-source limits; use the retry/source-check action when access is restored. |
| Managed centrally, native target absent | Adoption and deployment are separate. Review that target's native method and prerequisites. |
| Already installed externally | Leave it external. Central adoption does not grant overwrite/remove rights or add a duplicate. |
| Manual / Unsupported | Check executable/root configuration, operator opt-in, native mechanism and inventory diagnostics. Changing a version number alone cannot authorise a route. |
| Changed artifact, pending candidate or native conflict | Inspect the drift; refresh does not apply candidates or reclaim replaced files. A valid explicit replacement is required, and current blockers still apply. |
| No OpenCode enable/disable | Expected: only whole-package deploy/update/remove is proven for this route. |
| OpenCode load/dependency error | Check the native dependency environment and diagnostics. Skill Manager does not bootstrap dependencies by copying external caches. |
| No Claude Update button | Current UI limitation described above; service/API update support does not imply every UI control is exposed. |
| Remove completed | The central package and other harness installations remain. A fresh deployment can be made when its current checks pass. |

## Validation boundary

`tests/integration/test_package_deployment_api.py` drives real HTTP, managed store,
planner and lifecycle with simulated native process responses only.
It covers two native mechanisms, externally changed enablement, disabled-preserving
replacement, exact separate distributions, legacy OpenCode evidence, stale artifacts,
cache conflicts and retention of central ownership. `test_package_api.py` covers
standalone context, explicit GitHub resolution without a local manifest, and all
source failure states without leaf adoption. Frontend tests cover confirmation,
replacement selection, unavailable/refetching facts and package-aware routing.

Browser checks use `AppTestHarness` with the same isolated fixture and the built
frontend. They are UX/API checks, not new real-harness compatibility claims.
Real-harness evidence and reproducible isolated commands are maintained in the
[deployment guide](native-package-deployment.md#repeatable-real-checks), separately
from these UI fixtures.
