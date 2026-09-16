# Native whole-package planning

The planner consumes the [central managed package](managed-source-packages.md)
and fresh native facts. Planning itself performs no installation, registration,
update, removal or plugin execution. Native adapters gather the runtime facts;
the [deployment service](native-package-deployment.md) executes permitted actions.

## Package formats and placement

Every route uses the exact managed snapshot selected by the planner, never the
observed leaf folder or a same-name marketplace result. `<root>` means the
configured native target root, not the central artifact directory.

| Harness / package evidence | Planned whole-package placement | Native loading |
| --- | --- | --- |
| Claude: `.claude-plugin/plugin.json` | `native-local`: `<root>/skills/skill-manager-<id-prefix>` | Claude reads the owned whole copy in place as `<name>@skills-dir`; this is its whole-plugin loader, not leaf-Skill copying |
| Codex: `.codex-plugin/plugin.json` or standard root `plugin.json` | `native-install`: private marketplace under `<root>/skill-manager-marketplaces/<unique-name>` | First-party marketplace/plugin add commands create a separate native cache copy |
| OpenCode: declared server export, or explicit OpenCode distribution with a valid entry | `native-install`: `<root>/skill-manager-packages/<id-prefix>` | Native global registration of the exact owned `file://` directory; OpenCode reads this whole copy in place |
| Cursor: `.cursor-plugin/plugin.json` or standard root `plugin.json` | **Manual**. Planner/local-copy fixtures cover `<root>/plugins/local/skill-manager-<id-prefix>` only | No verified automated native runtime lifecycle; the candidate route is not enabled by the default runtime factory |

No source/format proof, unavailable mechanism, or unsafe target means
`manual/unsupported` with no actions. There is no component-copy fallback.
See [verified native support](native-package-deployment.md#verified-native-support)
for lifecycle operations and the scope of real-runtime evidence.

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

## OpenCode native contract

The verified route uses `opencode plugin <file-URI> --global` and singular `plugin`
configuration. The planner accepts `exports["./server"]` (a string or import/default
entry), or `main` when explicit OpenCode distribution evidence permits it. The
entry must remain inside the acquired package. The runtime fixture exports a
default object with `id` and `server()`. Directory auto-discovery beneath `plugins/`
was not proven; the explicit registration is used instead.

Version strings do not grant or deny deployment. The adapter checks the native
plugin command and actual global configuration binding. Source snapshots remain
pinned in the central store. Exact external copies/registrations are not claimed; unknown
auto-loaded JS/TS plugins, controls and ambiguous inventory block mutation.
The existing ownership ledger protects both whole-copy identity and registration
origin. No version allow-list, minimum-version table or compatibility registry is used.

## Planner contract

`SkillsQueryService.plan_managed_package(package_id, NativeTarget(...))` calls
`PackageDeploymentPlanner` with the existing `ManagedPackageStore`. It reloads
the managed record/fingerprint and never saves another package/source model.

`NativeTarget` supplies:

- harness and its actual user configuration home;
- reconciled native registrations with exact source coordinates or exact root
  references, and occupied native namespace identifiers;
- `inventory_complete` (default false);
- `mechanism_available` (default unknown): positive evidence that the selected
  package/route's actual mechanism is available **and** allowed by policy.

Neither flag is inferred from an empty config file or the existence of a binary.
The service obtains fresh adapter evidence before every operation. An incomplete
inventory cannot establish that the target is absent or safe to change.

Plans expose the managed ID, selected managed distribution ID, authoritative
source, parent/selected managed states, fingerprint, candidate source, strategy,
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
its command examples when `actions` is empty; execution must re-plan with fresh facts.

Native loading can execute plugin setup, hooks and MCP processes. Native installs
can download packages, change caches/configuration and initiate authentication.
The [isolated verification scripts](native-package-deployment.md#repeatable-real-checks)
test the selected mechanisms without exposing real user state. Native loading
is not side-effect-free merely because planning is read-only.

## External reconciliation and ownership

Exact native source/revision (or npm version) and package path, or an exact native
root reference to the managed snapshot, can establish `external-existing`.
Disabled-but-still-registered packages remain external and must not be duplicated;
this is registration presence, not a claim of runtime enablement.

A same native identifier with different/unproven source is `conflict`. An unpinned
registration for the same exact package coordinates is likewise unreconciled,
not absent. Display-name similarity is neither a match nor native-purpose proof.
Present central-record external observations without matching native evidence block
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

Plans rebuild from the central store plus fresh native facts after restart.
`managed` native ownership requires the deployment service's saved verified record,
unchanged target identity/fingerprint and matching native evidence. Creation is
recorded only after verification. The [deployment ledger](native-package-deployment.md#ownership-and-persistence)
references central packages; it does not duplicate source authority.

## Reference contracts

These documentation/source references establish format and placement rules, not
blanket compatibility with all releases. Actual runtime evidence is recorded in
the [deployment guide](native-package-deployment.md#verified-native-support).

- [Claude plugins](https://code.claude.com/docs/en/plugins), [persistent skills-directory loader](https://code.claude.com/docs/en/plugins-reference#skills-directory-plugins), [marketplaces](https://code.claude.com/docs/en/plugin-marketplaces).
- [Cursor plugins/local testing and policy](https://cursor.com/docs/plugins), [format reference](https://cursor.com/docs/reference/plugins).
- Codex commit `a8964cb1bad67bc26a826fb07d1bef99c6a3f008`: [CLI](https://github.com/openai/codex/blob/a8964cb1bad67bc26a826fb07d1bef99c6a3f008/codex-rs/cli/src/plugin_cmd.rs), [marketplace layouts/source paths](https://github.com/openai/codex/blob/a8964cb1bad67bc26a826fb07d1bef99c6a3f008/codex-rs/core-plugins/src/marketplace.rs), [manifest precedence/hooks](https://github.com/openai/codex/blob/a8964cb1bad67bc26a826fb07d1bef99c6a3f008/codex-rs/core-plugins/src/manifest.rs), [manager install/store path](https://github.com/openai/codex/blob/a8964cb1bad67bc26a826fb07d1bef99c6a3f008/codex-rs/core-plugins/src/manager.rs).
- [OpenCode plugins](https://opencode.ai/docs/plugins), [configuration](https://opencode.ai/docs/config); actual installed command help and isolated Task09A runtime evidence take precedence over incompatible documentation snapshots.
