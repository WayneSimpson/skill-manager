# Skill Manager - Native Package-Aware Extension Management

## 1. Purpose

Skill Manager currently works well for standalone Skills: it can discover a Skill, adopt it into central management, and make it available across supported AI harnesses.

The limitation is that many Skills are now distributed as part of wider **plugins or packages** containing additional functionality such as agents, hooks, commands, rules, MCP integrations and harness-specific behaviour.

Managing only the individual Skill can therefore lose important functionality from the original package.

This programme extends Skill Manager so it can understand and manage the **wider package**, then make that package available to supported harnesses using their native plugin/package mechanisms.

## 2. Target Outcome

The intended end-to-end experience is:

**Discover installed extension -> identify its wider package -> resolve the authoritative upstream source -> acquire and manage the package centrally -> deploy it natively to selected harnesses -> verify the harness actually loads it.**

The initial target harnesses are:

- Claude Code
- Codex
- Cursor
- OpenCode

Existing standalone Skill behaviour must continue to work as it does today.
Standalone MCP and command management also remains separate from package deployment.

## 3. Core Architecture

### Standalone Skills

A genuinely standalone portable Skill continues using the existing Skill Manager workflow:

**Discover -> Adopt -> central Skill -> enable/disable per harness**

This programme must not make that workflow more complicated.

### Package-backed Skills

When a discovered Skill belongs to a wider plugin/package, Skill Manager should preserve that wider package relationship.

A locally discovered installation is not automatically considered the authoritative package. It may only be a harness-specific copy, cache or subset.

Skill Manager must therefore distinguish between:

- what is currently installed;
- what wider package it belongs to;
- where the authoritative package came from;
- what version/revision is being managed;
- where that package has been deployed.

### Source Resolution

Skill Manager must deterministically establish the trustworthy upstream source of a discovered package from explicit evidence, never fuzzy or name-only matching.

Evidence may come from native harness metadata, plugin/package manifests, Git or repository information, package registries, marketplaces, or other explicit provenance.

The existing Skill Manager Skills Marketplace and its GitHub/source-resolution capability should be reused where appropriate.

A Skill does **not** need to have originally been installed through the Skill Manager marketplace for source resolution to work.

If the authoritative source cannot be established confidently, Skill Manager must not guess.

The existing installation remains usable, but package-backed adoption and native deployment remain blocked until its source is resolved. There is no fallback to copying the observed leaf Skill. Already-managed standalone Skills retain their existing controls.

## 4. Native Package Deployment

The central architectural principle is:

> **Manage the package centrally, but let each harness consume it through its own supported native package/plugin mechanism.**

The same managed package may therefore be deployed differently to different harnesses.

For example, one harness may load a managed local package directly while another may install or cache its own runtime copy.

Skill Manager owns the package identity, source/version intent and deployment state. It does not need to force every harness to run from one shared physical directory.

Readiness depends on the required native mechanism, policy and ownership checks, not exact harness version numbers. Source revisions and package versions still pin the managed content; harness versions are verification evidence only. Available lifecycle actions differ by harness—an unverified enable/disable control must not be invented. The [native support matrix](native-package-deployment.md#verified-native-support) records the current verified routes and limits.

Where a safe native deployment route cannot be established, the result for the current programme is:

**Manual / Unsupported**

## 5. No Package Decomposition in the Current Programme

Skill Manager will **not**, as part of this programme, attempt to break a package apart and recreate it by independently installing its:

- Skills;
- MCP servers;
- commands;
- hooks;
- agents;
- rules;
- or other package components.

Doing so would require Skill Manager to reproduce the behaviour and semantics of several different harness ecosystems and would introduce significant complexity and risk.

Native whole-package deployment is therefore the required route for package-backed extensions in this phase.

Component-level fallback remains separate future research and should only be pursued if real unsupported use cases justify it.

Future LLM assistance could suggest unresolved source candidates for review, but cannot establish authority or control deployment. Any future component deployment would require separate approval and its own ownership/verification rules; neither participates in the current pipeline.

## 6. Existing Installations and Ownership

Discovery does not imply ownership.

If a plugin/package is already installed in a harness and Skill Manager did not create that deployment, it must be treated as **externally owned** unless Skill Manager can prove otherwise.

Ordinary adoption or deployment must not automatically:

- replace it;
- reinstall it;
- remove it;
- modify it;
- add a duplicate installation;
- or take ownership of it.

Skill Manager may still manage the same package centrally and deploy it to other harnesses.

This protection is particularly important for existing OpenCode plugin and configuration state.

## 7. Package Management

Once a trustworthy upstream package has been resolved, Skill Manager should maintain enough central information to reliably manage it over time.

At programme level this includes:

- package identity;
- source/provenance;
- version or revision;
- managed package content or reproducible source;
- relationship to discovered Skills;
- package capability information;
- deployment state by harness;
- change/update state.

The detailed storage model, lifecycle states and implementation mechanisms belong in the relevant ClickUp tasks.

Capability discovery explains what a package contains. It is not a component deployment engine and does not establish native compatibility on its own. Native dependency handling remains the harness's responsibility.

## 8. User Experience

The application should make the distinction between standalone Skills and package-backed extensions understandable without exposing unnecessary technical detail.

For package-backed extensions, the user should be able to understand:

- which wider package the Skill belongs to;
- whether its upstream source has been resolved;
- whether Skill Manager manages that package;
- which harnesses can consume it natively;
- where it is currently deployed;
- whether an existing deployment is externally owned;
- whether a harness is unsupported or requires manual installation.

Installing a native package should feel like a **package-level operation**, not manual management of all the individual files inside it.

## 9. Real Harness Verification

Successful deployment cannot be determined only by checking that files or configuration were written.

Where safely possible, the actual harness must be used to prove that it:

- discovers the deployed package;
- exposes the expected Skill or Skills;
- loads representative wider package functionality;
- stops exposing Skill Manager-owned deployment after supported disable/remove actions;
- restores it after a valid redeployment.

Testing must not damage existing user installations.

Isolated or disposable harness configuration should be used where appropriate, particularly for OpenCode.

## 10. Existing Foundation

The programme builds on work that already exists.

Tasks 01-04 established the OpenCode discovery and configuration foundation.

Deterministic multi-harness package capability discovery is an accepted foundation.

It should not be restarted simply because the wider programme has evolved.

Any specific compatibility gap should be handled as a focused correction.

## 11. Programme Stages

The programme is organised into these stages; detailed execution status belongs in ClickUp:

**01-04 - OpenCode discovery/configuration foundation**
Already completed.

**04A - Package capability discovery**
Established deterministic structural discovery.

**04B - Upstream package source resolution and acquisition**
Resolve trustworthy package provenance and obtain the authoritative package/version.

**05 - Central managed package model**
Manage the resolved package safely and durably.

**06 - Native deployment strategy**
Establish the correct native package mechanism for each target harness.

**07 - Native package deployment**
Implement the supported deployment mechanisms.

**08 - Package-aware product experience**
Expose package management and native deployment clearly in the UI.

**09 - End-to-end verification**
Prove the complete workflow against real harness behaviour.

**10 - Final documentation and support matrix**
Document only the capabilities actually verified.

**11 - OpenCode agent discovery and read-only UI**
Discover configured OpenCode agents from the supported effective configuration sources and expose their current configuration without mutating the active development environment.

**12 - Safe OpenCode agent create/edit management**
Add guarded creation and editing with validation, backups, concurrent-change detection, atomic writes, preservation of unknown configuration, and safe JSON/JSONC round-tripping.

**13 - Apply/reload lifecycle and isolated verification**
Separate saving configuration from applying it to OpenCode. Prefer supported configuration reload over process restart, require explicit user confirmation before either action, and prove the lifecycle in isolated/disposable OpenCode configuration before any supervised real-environment validation.

This sequence expresses the programme direction only.

The detailed requirements and acceptance criteria for each stage belong in ClickUp.

## 12. Non-Goals

The following are outside the current critical path:

- decomposing packages into independently managed Skills/MCPs/hooks/commands/etc.;
- automatically taking ownership of existing external plugin installations;
- using LLM inference as authoritative package provenance;
- allowing an LLM to control deployment;
- creating a new universal plugin format to replace native harness formats;
- rebuilding the existing standalone Skill, MCP or slash-command management systems.

## 13. Programme Success

The programme is successful when Skill Manager can take a representative package-backed Skill discovered in one harness and reliably:

1. recognise that it belongs to a wider package;
2. determine its trustworthy upstream source;
3. acquire and centrally manage the correct package/version;
4. determine how supported target harnesses consume that package natively;
5. deploy it without reconstructing its individual components;
6. preserve existing unmanaged installations;
7. prove through the real harness that the package works;
8. manage deployment independently across harnesses;
9. retain the existing simple standalone Skill experience.

Unresolved or unsupported cases must be represented honestly rather than guessed.

## 14. Delivery Model

This programme follows the established **ChatGPT -> ClickUp -> Paseo/OpenCode** workflow.

### PRD

This PRD is the durable programme-level description of:

- what we are building;
- why;
- the intended end-to-end workflow;
- the major architectural decisions;
- the boundaries of the programme.

It should remain relatively stable.

It is not the authoritative detailed implementation brief for individual tasks.

### ClickUp

**ClickUp is the source of truth for execution.**

Each investigation or implementation task must contain the complete current context required to perform that task, including its requirements, constraints, acceptance criteria and validation expectations.

An agent should be able to execute a task by reading the current ClickUp record, the relevant project documentation and the codebase without needing access to historical ChatGPT conversations.

Important decisions, blockers, discoveries and validation results must be recorded back into ClickUp as work progresses.

### Continuous Task Review

The task sequence is intentionally reviewed as the programme progresses.

After each substantial task is completed, ChatGPT should:

1. review what was actually implemented and verified;
2. review the next task or tasks in full;
3. compare them with what has been learned;
4. update stale assumptions, scope, dependencies or acceptance criteria;
5. add or remove planned work where justified;
6. only then move to the next approved delegation.

This prevents future tasks from becoming stale as the architecture is proven.

### Paseo/OpenCode Delegation

Implementation is delegated through the existing Paseo/OpenCode project context only after explicit approval.

For each delegated task, the primary agent must read:

- the current ClickUp task in full;
- this PRD as wider programme context;
- relevant dependencies and recent comments;
- project `AGENTS.md`;
- the existing implementation relevant to the task.

The **ClickUp task remains the authoritative execution brief**.

The Paseo handoff provides orchestration instructions and supplementary context; it does not replace the task.

The primary agent owns the task outcome and must keep ClickUp current during execution.

Where specialist subagents are useful, their work should be small and bounded. They must complete their assigned work themselves rather than recursively delegating further.

A task is not considered complete merely because an agent reports that it is finished. Appropriate tests, integration checks, runtime verification or independent review must provide evidence that its acceptance criteria have actually been satisfied.

## 15. Programme Change Management

The PRD should change when a genuine **programme-level architectural decision** changes.

Normal implementation discoveries should instead update the relevant ClickUp task.

If a programme-level assumption changes:

1. update this PRD;
2. review all affected future ClickUp tasks;
3. update those task descriptions and dependencies;
4. then continue implementation.

This keeps the PRD focused on the durable direction while allowing ClickUp to remain the detailed and evolving execution record.


## 16. OpenCode Agent Configuration Management

### Purpose

Skill Manager should provide a first-class management layer for OpenCode agents/sub-agents configured through OpenCode configuration.

The user should be able to inspect configured agents and, where safely supported, create and edit them from Skill Manager without manually editing OpenCode configuration files.

This capability builds on the OpenCode configuration-discovery foundation established by Tasks 01 and 04. It must reuse the existing deterministic OpenCode JSON/JSONC path selection, parsing and mutation behaviour rather than creating a second configuration system.

### Supported configuration sources

The feature must support the OpenCode configuration sources already recognised by the project, including current JSON and JSONC forms.

At minimum this includes:

- `${XDG_CONFIG_HOME}/opencode/opencode.jsonc`;
- `${XDG_CONFIG_HOME}/opencode/opencode.json`;
- supported legacy locations already handled by the shared resolver.

Configuration precedence, selected write target and JSONC preservation must remain deterministic.

### Agent information

For each configured OpenCode agent, Skill Manager should expose the OpenCode-supported fields required for practical management, including where present:

- agent name;
- description;
- prompt/instructions;
- model;
- model variant/reasoning setting;
- mode, including sub-agent mode;
- other relevant configuration needed to preserve the existing agent definition safely.

The UI must preserve unknown or currently unsupported fields rather than discarding them.

Model reasoning/effort choices must not be hard-coded globally. Available model variants differ between models/providers; Skill Manager should use authoritative OpenCode/model metadata where available and preserve existing unknown/custom values.

### Read-first safety model

The active OpenCode configuration and runtime are protected state.

Discovery and read-only display must not mutate OpenCode configuration, restart OpenCode, reload OpenCode, create test agents in the real configuration, or take ownership of external agent definitions.

Automated development and testing must use isolated/disposable OpenCode configuration roots and fixture agents rather than the active user configuration.

Agents discovered from configuration sources that Skill Manager does not yet safely mutate, including externally managed agent files where applicable, may be shown read-only until an explicit management contract exists.

### Safe mutation model

Editing in the UI does not immediately mutate or reload the active OpenCode runtime.

The intended lifecycle is:

**Read -> Edit draft -> Validate -> Preview diff -> Save configuration -> Explicit Apply**

Before any real configuration write, Skill Manager must:

1. re-read the selected configuration source;
2. confirm it has not changed since the user began editing;
3. block and require reconciliation if the source changed concurrently;
4. create a recoverable backup outside the active OpenCode configuration directory;
5. protect backup permissions appropriately because OpenCode configuration may contain sensitive values;
6. generate the updated document while preserving unrelated/unknown configuration and JSONC behaviour;
7. validate the complete resulting configuration;
8. write atomically using a temporary file followed by replacement;
9. verify the resulting file can be read back successfully.

A failure at any point must leave the original configuration usable and provide a recoverable rollback path.

The initial implementation should support safe view, create and edit operations. Destructive deletion is outside the initial slice unless separately reviewed and approved.

New agents created through this workflow must be explicitly created as OpenCode sub-agents rather than relying on an implicit/default mode.

### Apply, reload and restart

Saving configuration and applying configuration to the running OpenCode instance are separate user actions.

Skill Manager must capability-detect the safest supported OpenCode apply mechanism.

Where OpenCode supports a safe configuration reload, reload is preferred over restarting the process.

Skill Manager must never reload or restart OpenCode automatically after saving a change.

Before reload, the user must receive a clear warning that active sessions may observe changed agent configuration and must explicitly confirm.

If a process restart is genuinely required, it is a fallback only. The UI must clearly warn that active OpenCode sessions may be interrupted and require explicit user confirmation immediately before the restart.

During development of this feature, no real OpenCode reload or restart may be performed merely to prove implementation. Real-runtime validation must first be demonstrated against isolated/disposable configuration and may only touch the active development runtime when the current ClickUp task explicitly calls for supervised validation and Wayne has approved that specific operation.

### Concurrency and self-protection

Skill Manager may be developed by the same OpenCode installation it can manage. The product and delivery process must therefore protect against self-disruption.

The implementation must not:

- use the active development agents as mutation fixtures;
- rewrite the whole OpenCode configuration from an incomplete internal model;
- silently replace concurrent user/OpenCode/tool changes;
- reload or restart OpenCode as a side effect of tests;
- create duplicate configuration sources where a selected supported source already exists;
- infer ownership merely because an agent was discovered.

Real user state remains authoritative unless an explicit, validated Skill Manager mutation is confirmed by the user.

### User experience

The initial Agents area should make it easy to:

- view all supported configured agents;
- inspect their description, instructions, model and model variant/reasoning setting;
- distinguish editable managed configuration from external/read-only definitions;
- create a new sub-agent safely;
- edit an existing supported agent;
- review the exact intended changes before save;
- understand whether saved changes are pending application;
- explicitly reload/apply the configuration where supported;
- understand when a restart is required and what will be interrupted.

The UI should not expose raw configuration complexity where a clear field-based editor is sufficient, but the resulting configuration must remain faithful to OpenCode's model.

### Validation

Before this capability is considered complete, verification must include:

- JSON and JSONC fixtures;
- deterministic path and precedence behaviour;
- comment/unknown-field preservation for JSONC;
- model and variant round-trip behaviour;
- create and edit flows;
- stale/concurrent source detection;
- backup creation and rollback;
- atomic write failure handling;
- no-change/no-op handling;
- isolated reload/apply verification where supported;
- restart confirmation/fallback behaviour without unapproved restart of the active development OpenCode instance;
- proof that unrelated Skills, MCPs, plugins, providers and other OpenCode configuration remain unchanged.

The active user configuration must not be used as an automated test fixture.
