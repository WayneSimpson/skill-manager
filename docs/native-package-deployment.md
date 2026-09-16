# Native whole-package execution and support

The backend execution entry point is `SkillsMutationService.package_deployments`,
or `PackageDeploymentService(existing_managed_package_store)`. It consumes the
existing [planner](native-package-strategies.md) and [managed store](managed-source-packages.md). HTTP endpoints and package UX call
this service without supplying plans or native paths; their response and state
contract is documented in the [package guide](package-management-ux.md).

## Operations

```python
adapter = ClaudeNativePackageAdapter(native_home, configured_runner,
                                     mechanism_available=True)
service = mutations.package_deployments
result = service.deploy(package_id, adapter)
deployment_id = result['deployment']['deploymentId']
service.disable(deployment_id, adapter)
service.update(deployment_id, explicitly_selected_new_package_id, adapter)
service.enable(deployment_id, adapter)
service.reconcile(deployment_id, adapter)  # read current ownership/verification
service.remove(deployment_id, adapter)
```

The example's availability flag is a **verified prerequisite**, not a switch to
skip checks. Check the actual native mechanism, applicable policy and complete
inventory first. CLI adapters default to unavailable and invoke no runner until
availability is confirmed. Runners must target the supplied home/configuration,
be bounded/non-interactive, and avoid importing unrelated user configuration.
There is deliberately no default process runner using the agent's home.

The service acquires the existing package lock, reloads package/native facts and
re-plans. It never accepts a previously serialized plan to execute. Staging is
followed by another fresh check before promotion. Updates require both old and
explicitly selected new managed snapshots to pass their checks. Pending source
candidates, changed/missing artifacts, incomplete inventory, external matches and
conflicts cannot be bypassed for removal or toggling.

Update retains the previously planned owned directory/namespace, with an explicit
reference to the initial placement snapshot. It does not guess a package family,
follow a candidate, rename a conflicting native identity, or choose a distribution
from a name. Separate harness distributions use the planner's selected managed ID.

## Verified native support

Support is conditional on explicit operator setup, usable native commands, safe
roots, complete inventory, valid source snapshots and proven ownership. **No
harness version controls readiness**: there is no exact/minimum-version table or
compatibility registry. The production runner currently accepts raw Linux ELF
binaries only; this matrix does not extend native mutation support to macOS or
Windows. Standalone platform support is separate.

| Harness | Deploy / repeat | Update | Enable / disable | Remove / repeat |
| --- | --- | --- | --- | --- |
| Claude | Verified | Verified service/API; UI limitation below | Verified | Verified |
| Codex | Verified | Verified | Verified | Verified |
| OpenCode | Verified | Verified, stable file URI | Not offered; no safe native control proven | Verified |
| Cursor | Manual | Manual | Manual | Manual |

- **Claude**: the service places and replaces the whole owned directory. Native
  `plugin enable/disable` controls it; removal deletes only the owned directory.
  The current UI does not offer Update for the real Claude adapter: the response
  builder requires an adapter `reinstall` method, while Claude replacement is
  service-owned. Explicit service/API updates were verified. This UI limitation
  does not change the native lifecycle contract.
- **Codex**: first-party `plugin marketplace add`, `plugin add` and `plugin remove`
  operate on a private marketplace and native cache. Update preserves disabled
  state. Enable/disable changes only the owned explicit TOML boolean, preserving
  unrelated bytes and file mode; an unfamiliar layout remains Manual.
- **OpenCode**: `plugin <file-URI> --global` adds the exact owned directory to
  singular `plugin` configuration. Update replaces the complete owned copy and
  verifies loading at the same URI. Removal deletes only that exact recorded
  string and owned directory, preserving unrelated JSONC bytes/comments/mode.
  There is no fabricated native remove command or enable/disable flag;
  verified installed state has `enabled: null`.
- **Cursor**: local-copy fixtures cover planning/service safety, but no automated
  native GUI/policy lifecycle was verified. The default factory remains read-only.
  Installing a different version does not remove this mechanism limitation.

Format, directory and distribution selection rules are in
[native planning](native-package-strategies.md#package-formats-and-placement).
Native clients may need reload or a new session after changes.

### Real runtime evidence

Versions below are **versions used for verification only**, not required versions.
Evidence comes from accepted Tasks 09 and 09A in isolated disposable state.

| Harness | Verification version | What the real runtime proved |
| --- | --- | --- |
| Claude | 2.1.273 (earlier chain: 2.1.269) | Exact plugin identity/path; initialization reported one namespaced Skill and agent when enabled, none when disabled/removed, and restored them after update/redeploy. Plugin details also reported the hook declaration. |
| Codex | 0.154.0 | Fresh app-server `skills/list` and `plugin/read` reported the namespaced Skill, hook and updated package version; visibility followed enable/disable/remove/redeploy. Native cache/source ownership was checked. |
| OpenCode | 1.18.31 | `debug info` reported the exact URI; the fixture's `server()` read a bundled resource and wrote versioned markers for install/update. A fresh process after removal did not load it. Repeat operations and stale-registration refusal passed. |
| Cursor | No live verification | No production support inferred from fixture success. |

Claude/Codex tests included disabled-preserving updates, restart reconciliation
and redeployment. All three supported routes retained central snapshots and
unrelated external fixtures. The final isolated runs left the selected real
OpenCode config/cache/plugin trees hash-identical before and after.

No model-backed Skill invocation or arbitrary hook execution is claimed. The
OpenCode marker proves execution of that self-contained fixture's server entry,
not arbitrary packages. Dependency-bearing OpenCode packages still require its
own working dependency environment. The verifier makes only its disposable
config read-only during runtime inspection to avoid automatic SDK bootstrap;
production code never changes user-directory permissions or reimplements
dependency installation.

## Ownership and persistence

`packages/deployments.json` is a versioned, atomically written deployment ledger,
serialized under the central package lock. It contains only deployment IDs,
managed/selected/placement package references, harness/root/native target identity,
strategy, applied/target/native-cache fingerprints, directory identity, and the
last verified enablement/time. Source coordinates, capabilities and artifacts
remain exclusively in the central managed store.

No managed record is saved before successful mutation and native verification.
The read-only planner's ownership extension requires a saved verified record,
matching directory identity/fingerprint and fresh native evidence. External
evidence is still checked even when an owned entry is present. A matching name
without native path/source evidence cannot establish ownership. Codex's declared
Git/npm coordinates reuse source validators; parsing them performs no acquisition.

OpenCode ownership includes both whole-copy identity and the exact registration's
document/key/value fingerprint. Moving that registration is drift, not permission
to claim it again. Exact matching external copies are used only to block duplicate
deployment. Unknown auto-loaded JS/TS files, control entries and incomplete native
inventory suppress mutation rather than being treated as absence. Discovery and
central adoption never convert external config, plugins or caches into owned state.

Staging and backups stay outside native loader directories. A placed directory
replaced during verification, even with identical bytes, is not claimed or
deleted. Changed native caches and unrecorded occupied paths remain conflicts.
Completed removals retain a small reference-only receipt for safe idempotent
retries. A failed final removal-state write can be retried only after a fresh
healthy plan and verified absence; a replacement is never deleted.

## Failure handling

Local failure rollback removes only the newly created, unchanged directory. A
native installer call can partly succeed before raising, or succeed before the ledger
write fails. Those uncertain native artifacts are **not** automatically reclaimed
or deleted. The next fresh inspection reports conflict/manual. This follows the
existing no-takeover rule for unrecorded crash leftovers: source coordinates or
matching bytes alone are not a creation receipt.

An interrupted native update likewise may leave a replacement and a private
backup; the old record no longer proves ownership of the changed target.
`reconcile()` reports the mismatch without repairing the target or rewriting
ownership. Native verification can still start the client and, for OpenCode,
load package code; it is not a side-effect-free static scan. Manual inspection is
required where successful creation/removal cannot be proved. Stored `verified`
is the last successful check, not a substitute for fresh reconciliation. No
automatic installer retry or component fallback is used.

## Repeatable real checks

| Script | Scope |
| --- | --- |
| [verify_native_package_sandbox.py](../scripts/verify_native_package_sandbox.py) | Empty-runtime startup and selected real-tree hashes; takes Claude, Codex and OpenCode binaries |
| [verify_native_package_lifecycle.py](../scripts/verify_native_package_lifecycle.py) | Service-level Claude/Codex lifecycle and external fixtures; also takes all three binaries |
| [verify_package_source_to_native.py](../scripts/verify_package_source_to_native.py) | Fixture HTTP bytes at acquisition boundary, real resolver/store/planner/HTTP actions and real Claude/Codex runtimes; native outputs are not mocked |
| [verify_opencode_package_lifecycle.py](../scripts/verify_opencode_package_lifecycle.py) | Real OpenCode service/adapter registration, marker loading, repeat, update, conflict refusal and removal |

From the repository root, supply locally verified paths and an already-present
Docker image. These examples deliberately contain no developer-machine paths:

```bash
.venv/bin/python scripts/verify_package_source_to_native.py \
  --image "$TEST_IMAGE" --claude "$CLAUDE_ELF" --codex "$CODEX_ELF" \
  --snapshot-root "$OPENCODE_CONFIG_TREE" \
  --snapshot-root "$OPENCODE_CACHE_TREE" \
  --snapshot-root "$OPENCODE_PLUGIN_TREE"

.venv/bin/python scripts/verify_opencode_package_lifecycle.py \
  --image "$TEST_IMAGE" --opencode "$OPENCODE_ELF" \
  --snapshot-root "$OPENCODE_CONFIG_TREE" \
  --snapshot-root "$OPENCODE_CACHE_TREE" \
  --snapshot-root "$OPENCODE_PLUGIN_TREE"
```

The scripts use Docker with no pulls/network, read-only image, UID/GID 65534,
all capabilities dropped, no-new-privileges, memory/PID/time limits, and an empty
environment populated only with sandbox HOME/XDG/temp/config paths. Only raw ELF
binaries and a fresh test tree are mounted; no host home, credentials, user
config/cache or project trees are exposed. Source and central-artifact subtrees,
when present, are mounted read-only; owned native copies stay writable inside the
disposable tree. Test-created native files have their permissions released inside the
sandbox for host cleanup; this never targets real user state.

Bookend hashes include file contents, paths, modes and link targets. Active
session databases/logs are outside the explicitly chosen plugin/config/cache
roots. No statement that the ongoing agent session's database remains unchanged
is made. A mismatch fails verification; it must be investigated, not masked by
restoring files or excluding unexplained changes. These checks are opt-in and do
not run native commands in normal backend unit/integration tests.
