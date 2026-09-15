# Native whole-package planning (Task 06)

Verified against official documentation/source on **2026-09-15**. This is a
read-only planning layer. No native command, installation, registration, update,
uninstall or plugin code runs here. Task05 remains the sole package authority.

## Preferred strategy matrix

| Harness / proven format | Default strategy and surface | Copy/loading behavior | Lifecycle and verification |
| --- | --- | --- | --- |
| Claude `.claude-plugin/plugin.json` | `native-local`: whole directory under `<Claude home>/skills/skill-manager-<package-id-prefix>`; native identifier from the manifest, `<name>@skills-dir` | Future Task07 places a complete owned copy; Claude discovers it in place. This is Claude's documented **whole-plugin** loader, not an individual Skill copy. | Enable/disable with `claude plugin enable/disable <name>@skills-dir`; removal is deletion of only the owned whole directory, not marketplace uninstall. Replace only an owned whole copy for update; `/reload-plugins` or new session. Verify plugin list/errors and expected namespaced behavior in an isolated session. |
| Cursor `.cursor-plugin/plugin.json` or standard root `plugin.json` | `native-local`: complete copy under `<Cursor home>/plugins/local/skill-manager-<package-id-prefix>` | Cursor discovers this directory after reload. External symlink targets are skipped, so a symlink to the central Task05 artifact is **not** a strategy. | Local import policy must permit it, including Enterprise restrictions. Reload Window then verify Customize and runtime behavior. No documented headless lifecycle CLI; moving/removing an owned local copy and reloading needs Task07 runtime proof, otherwise manual lifecycle. Marketplace/UI lifecycle remains separate. |
| Codex `.codex-plugin/plugin.json` or standard root `plugin.json` | `native-install`: private local marketplace root below `<CODEX_HOME>/skill-manager-marketplaces/<unique-name>`; manifest at **`.agents/plugins/marketplace.json`**, then `codex plugin marketplace add <root>` and `codex plugin add <plugin>@<marketplace> --json` | Task07 would stage one complete owned package under the marketplace root and let the native plugin store install it. No component reconstruction. | `[plugins."name@marketplace"].enabled` controls enablement; `codex plugin remove` removes the native install/cache. Updating requires the selected managed snapshot and native add flow, with runtime verification in a new thread. `codex plugin list --json` exposes installed identifiers and declared sources, not proof of loaded bytes. |
| OpenCode, **explicitly proven** OpenCode distribution/source | `native-install`: v2 `plugins` registration with exact npm version or `github:owner/repo#<full-commit>::path:<subdir>` when applicable | Native package loader maintains its own package/cache copy. No direct central-artifact mutation or local-entrypoint guess. | Remove only the owned registration; v2 ID controls can enable/disable verified IDs. Exact pins are skipped by automatic package update; a later managed snapshot requires explicit reconciliation. Verify active plugin/source and representative behavior in an isolated runtime. |
| Any format without sufficient evidence, policy/version support or safe target | `manual/unsupported`, no actions | No copying/conversion fallback | Explicit blockers; no guessed registration or package family. |

Claude's `--plugin-dir`/`--plugin-url` flags are session-only testing surfaces, not
the selected persistent mechanism. Claude marketplace installation is another
documented route but is unnecessary for the selected local whole-plugin format.
Claude also supports project scope, with workspace trust and narrower runtime
permissions; the initial planner deliberately selects personal/user scope only.

Cursor marketplace installation supports user/project scopes through Customize.
Its local import feature can be disabled by policy and a same-named marketplace
plugin can take precedence. Those facts must be reconciled before marking a
target available. Standard MCP `${PLUGIN_ROOT}`/`${PLUGIN_DATA}` variables are
not expanded by Cursor: the planner blocks that known standard-format case;
it does not rewrite MCP configuration or extract components.
Invalid co-located standard manifests and conflicting Cursor/standard native
identifiers are manual cases rather than guesses about loader precedence.

Codex's current loader gives standard root `plugin.json` precedence and may read a
Codex overlay. The planner follows that root identity. Other legacy fallback
formats are documented in source but are not enabled as extra planner routes.
The marketplace source is `{ "source": "local", "path": "./plugins/<id-prefix>" }`,
relative to the marketplace **root**, not to `.agents/plugins`. A bare root
`marketplace.json` is not a supported Codex marketplace layout.

## OpenCode version distinction

Current v2 docs use plural `plugins`. The old `packages/opencode/src/plugin/shared.ts`
and its `./server`/`./tui` export rules are **not evidence for the v2 loader**.
At pinned commit `e03db9bc6908f75c9334d8aa997deeaac81c0298`, actual v2 code is under
`packages/core/src`. Its external loader imports a module with a default `id`
and `setup` or `effect`, and uses the native npm service for package specs.

There is observable drift between the v2 web docs and that source snapshot,
including local-directory discovery/import details and SDK spelling. Accordingly,
the planner does not infer v2 compatibility from `package.json.main`, a package
name, SDK dependency or old TUI export. An explicit harness-scoped Task05
distribution or exact native registration is required as native intent, and the
caller must positively verify mechanism availability for the selected package
and actual target version. Unknown cases stay manual. No new source provider or
native capability parser is introduced.

## Planner contract

`SkillsQueryService.plan_managed_package(package_id, NativeTarget(...))` calls
`PackageDeploymentPlanner` with the existing `ManagedPackageStore`. It reloads
the Task05 record/fingerprint and never saves another package/source model.

`NativeTarget` supplies:

- harness and its actual user configuration home;
- reconciled native registrations with exact source coordinates or exact root
  references, and occupied native namespace identifiers;
- `inventory_complete` (default false);
- `mechanism_available` (default unknown): positive evidence that the selected
  package/route is supported by the installed version **and** allowed by policy.

Neither flag is inferred from an empty config file or the existence of a binary.
Task07 must gather fresh evidence before executing anything. No full native
inventory adapter/runtime probe is claimed in Task06.

Plans expose the managed ID, selected managed distribution ID, authoritative
source, parent/selected Task05 states, fingerprint, candidate source, strategy,
native surface, whole-package actions, ownership, support, blockers, evidence
and reconciliation requirement. All `runtime_verified` values are false.

An explicit harness distribution selects only a unique, matching existing
managed source snapshot. Missing, unpinned or ambiguous distributions do not
fall back to the parent artifact. An unscoped npm-to-repository relationship is
not a harness distribution. Names never select sources or package families.

Changed/missing/unavailable/not-retained artifacts, non-current upstream state,
unresolved sources and pending candidates suppress every action. A candidate is
reported, never substituted. Conflicts and incomplete inventory also suppress
actions. `supported` describes a proven planning route with supplied preconditions,
not a full native loader audit or a successful runtime test.
`surface` can describe a mechanism even on a blocked plan. Consumers must not run
its command examples when `actions` is empty; Task07 must re-plan with fresh facts.

Native loading can execute plugin setup, hooks and MCP processes. Native installs
can download packages, change caches/configuration and initiate authentication.
Task07 must verify the selected installer's lifecycle behavior in isolation;
Task06 does not claim that native loading is side-effect-free.

## External reconciliation and ownership

Exact native source/revision (or npm version) and package path, or an exact native
root reference to the managed snapshot, can establish `external-existing`.
Disabled-but-still-registered packages remain external and must not be duplicated;
this is registration presence, not a claim of runtime enablement.

A same native identifier with different/unproven source is `conflict`. An unpinned
registration for the same exact package coordinates is likewise unreconciled,
not absent. Display-name similarity is neither a match nor native-purpose proof.
Present Task05 external observations without matching native evidence block
planning until reconciled; a Skill sighting alone is not proof of registration.
Occupied destinations, symlink escapes and non-directory target roots block.

`read_opencode_registrations(explicit_paths)` reads only supplied bounded JSONC
documents, observes both singular/plural package declarations, and returns only
source/root identity—not plugin options or credentials. Unknown specs fail for
manual reconciliation. It does not evaluate native enablement, discover every
project/plugin directory or prove inventory completeness.
Runtime ID/wildcard control entries are retained as unresolved control evidence
and block actions until reconciled. A negative ID alone is not a package source
identity and cannot prove either a matching install or absence.

No deployment persistence is needed before Task07 writes anything. Plans rebuild
deterministically from Task05 plus fresh native facts after restart. Task06 never
returns `managed` native ownership. Future deployment records should reference
the managed package ID and contain only native identifier/path, applied snapshot
fingerprint and verification facts—not another copy of Task05 state.

## Safe OpenCode test route

`opencode_isolation(root)` returns a plan only; it creates no directories and
starts no process. Use a new disposable container/user filesystem namespace with
no host home, credentials, project trees or config mounts. Provide only the
approved whole-package fixture read-only; native writable copies remain inside
the sandbox. Begin with networking disabled; approve dependency downloads
separately if required for Task07 tests.

Before importing/starting the runtime, replace—not merge—the process environment:
HOME/OPENCODE_TEST_HOME, all four XDG roots, TMPDIR/TMP/TEMP and
OPENCODE_CONFIG_DIR point inside that sandbox. Supply only a verified sandbox
binary PATH. Do not inherit OPENCODE_CONFIG, CONFIG_CONTENT, authentication,
NODE_OPTIONS, BUN preload options or host package-manager settings.

The pinned v2 global module creates XDG data/config/state and temporary directories
at import time. `Global.make` uses CONFIG_DIR for its config service; v2 config
also searches ancestor project documents. Older config code treats custom config
inputs differently. Thus neither CONFIG nor CONFIG_DIR is a universal isolation
switch, and the planner does not rely on the legacy project-disable flag.

Tests prove the route contains no inherited secrets, produces no writes/processes,
and keeps existing static config-adapter reads inside fixture paths. **No live
OpenCode/container isolation run is claimed.** Runtime containment and actual
loading must be verified under Task07's approved isolated execution.

## Sources checked during Task06

- [Claude plugins](https://code.claude.com/docs/en/plugins), [persistent skills-directory loader](https://code.claude.com/docs/en/plugins-reference#skills-directory-plugins), [marketplaces](https://code.claude.com/docs/en/plugin-marketplaces).
- [Cursor plugins/local testing and policy](https://cursor.com/docs/plugins), [format reference](https://cursor.com/docs/reference/plugins).
- Codex commit `a8964cb1bad67bc26a826fb07d1bef99c6a3f008`: [CLI](https://github.com/openai/codex/blob/a8964cb1bad67bc26a826fb07d1bef99c6a3f008/codex-rs/cli/src/plugin_cmd.rs), [marketplace layouts/source paths](https://github.com/openai/codex/blob/a8964cb1bad67bc26a826fb07d1bef99c6a3f008/codex-rs/core-plugins/src/marketplace.rs), [manifest precedence/hooks](https://github.com/openai/codex/blob/a8964cb1bad67bc26a826fb07d1bef99c6a3f008/codex-rs/core-plugins/src/manifest.rs), [manager install/store path](https://github.com/openai/codex/blob/a8964cb1bad67bc26a826fb07d1bef99c6a3f008/codex-rs/core-plugins/src/manager.rs).
- [OpenCode v2 plugins](https://opencode.ai/v2/docs/plugins), [config](https://opencode.ai/v2/docs/config), [plugin lifecycle](https://opencode.ai/v2/docs/build/plugins).
- OpenCode commit `e03db9bc6908f75c9334d8aa997deeaac81c0298`: [v2 global paths](https://github.com/anomalyco/opencode/blob/e03db9bc6908f75c9334d8aa997deeaac81c0298/packages/core/src/global.ts), [v2 config discovery](https://github.com/anomalyco/opencode/blob/e03db9bc6908f75c9334d8aa997deeaac81c0298/packages/core/src/config.ts), [v2 external loader](https://github.com/anomalyco/opencode/blob/e03db9bc6908f75c9334d8aa997deeaac81c0298/packages/core/src/config/plugin/external.ts).
