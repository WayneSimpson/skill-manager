# Native whole-package execution (Task 07)

The backend execution entry point is `SkillsMutationService.package_deployments`,
or `PackageDeploymentService(existing_managed_package_store)`. It consumes the
accepted Task06 planner and Task05 store. HTTP endpoints and package UX call
this service without supplying plans or native paths; their response and state
contract is documented in `docs/package-management-ux.md`.

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

## Implemented mechanisms and verified scope

| Harness | Execution | Verification / limits |
| --- | --- | --- |
| Claude | Whole-copy native-local placement; native enable/disable; owned whole-directory update/remove | Real Claude **2.1.269** `plugin list --json` confirms identity, exact `installPath`, version and enablement. `plugin details` confirmed Skill, agent and SessionStart hook. Missing native path is not inferred from a name. |
| Cursor | Same whole-copy local engine, requiring a verified native inspector/verifier | No Cursor runtime/GUI exists on this host. The default read-only adapter remains Manual; local import policy and actual GUI inventory must be verified before opting in. No invented CLI or alternate disabled directory. Fixture tests cover the complete copy/remove/distribution path. |
| Codex | Whole local marketplace under the Task06 namespace, first-party marketplace add/plugin add/remove, complete source update | Real **0.154.0** CLI verifies registered marketplace source, installed package and native cache bytes. Native `skills/list` and `plugin/read` confirmed the updated namespaced Skill and hook. Enable/disable changes only the owned explicit TOML boolean, preserving other bytes and file mode; unfamiliar TOML layout is manual. |
| OpenCode | Whole-copy staging and native global `plugin <file-URI> --global` registration | Task09A verified actual **1.18.31** loading, update and removal in disposable state. Uses singular `plugin`, not a separate product/version assumption. Native mechanism/global-root checks replace version gates. No enable/disable control is exposed; incomplete inventory stays Manual. |

Native clients may need reload/new sessions after changes. Registration/cache
verification is not a claim that arbitrary plugin code or a model turn ran.
The fixture hook is `/usr/bin/true`; no model calls or credentials are required.
Codex reads use its documented app-server protocol (pinned source
`a8964cb1bad67bc26a826fb07d1bef99c6a3f008`,
`codex-rs/app-server-protocol/src/protocol/v2/plugin.rs`).

## Ownership and persistence

`packages/deployments.json` is a versioned, atomically written deployment ledger,
serialized under the Task05 package lock. It contains only deployment IDs,
managed/selected/placement package references, harness/root/native target identity,
strategy, applied/target/native-cache fingerprints, directory identity, and the
last verified enablement/time. Source coordinates, capabilities and artifacts
remain exclusively in Task05.

No managed record is saved before successful mutation and native verification.
The read-only planner's ownership extension requires a saved verified record,
matching directory identity/fingerprint and fresh native evidence. External
evidence is still checked even when an owned entry is present. A matching name
without native path/source evidence cannot establish ownership. Codex's declared
Git/npm coordinates reuse 04B validators; no resolution or downloads occur.

Staging and backups stay outside native loader directories. A placed directory
replaced during verification, even with identical bytes, is not claimed or
deleted. Changed native caches and unrecorded occupied paths remain conflicts.
Completed removals retain a small reference-only receipt for safe idempotent
retries. A failed final removal-state write can be retried only after a fresh
healthy plan and verified absence; a replacement is never deleted.

## Failure handling

Local failure rollback removes only the newly created, unchanged directory. A
native Codex call can partly succeed before raising, or succeed before the ledger
write fails. Those uncertain native artifacts are **not** automatically reclaimed
or deleted. The next fresh inspection reports conflict/manual. This follows the
existing no-takeover rule for unrecorded crash leftovers: source coordinates or
matching bytes alone are not a creation receipt.

An interrupted native update likewise may leave a replacement and a private
backup; the old record no longer proves ownership of the changed target.
`reconcile()` reports the mismatch without writing anything. Manual inspection is
required where successful creation/removal cannot be proved. Stored `verified`
is the last successful check, not a substitute for fresh reconciliation. No
automatic installer retry or component fallback is used.

## Repeatable real checks

`scripts/verify_native_package_sandbox.py` checks empty-runtime startup and hashes
specified real configuration/plugin/cache trees. `scripts/verify_native_package_lifecycle.py`
also runs the complete Claude/Codex lifecycle through the service, preserving
separately installed external native fixtures and Task05's manifest. Supply:

```text
--image <already-present-image>
--claude <raw-Claude-ELF> --codex <raw-Codex-ELF> --opencode <raw-OpenCode-ELF>
--snapshot-root <real-OpenCode-config-tree>
--snapshot-root <real-OpenCode-package-cache-tree>
--snapshot-root <other-relevant-plugin-tree>
```

The scripts use Docker with no pulls/network, read-only image, UID/GID 65534,
all capabilities dropped, no-new-privileges, memory/PID/time limits, and an empty
environment populated only with sandbox HOME/XDG/temp/config paths. Only raw ELF
binaries and a fresh test tree are mounted; no host home, credentials, user
config/cache or project trees are exposed. Managed fixture artifacts are mounted
read-only. Test-created native files have their permissions released inside the
sandbox for host cleanup; this never targets real user state.

Bookend hashes include file contents, paths, modes and link targets. Active
session databases/logs are outside the explicitly chosen plugin/config/cache
roots. No statement that the ongoing agent session's database remains unchanged
is made. These checks are opt-in and do not run native commands in normal backend
unit/integration tests.
