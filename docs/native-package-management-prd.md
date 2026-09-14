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

Skill Manager must attempt to establish the trustworthy upstream source of a discovered package.

Evidence may come from native harness metadata, plugin/package manifests, Git or repository information, package registries, marketplaces, or other explicit provenance.

The existing Skill Manager Skills Marketplace and its GitHub/source-resolution capability should be reused where appropriate.

A Skill does **not** need to have originally been installed through the Skill Manager marketplace for source resolution to work.

If the authoritative source cannot be established confidently, Skill Manager must not guess.

The existing Skill can continue to be managed normally, but native cross-harness package deployment should be shown as unavailable until the package source is resolved.

## 4. Native Package Deployment

The central architectural principle is:

> **Manage the package centrally, but let each harness consume it through its own supported native package/plugin mechanism.**

The same managed package may therefore be deployed differently to different harnesses.

For example, one harness may load a managed local package directly while another may install or cache its own runtime copy.

Skill Manager owns the package identity, source/version intent and deployment state. It does not need to force every harness to run from one shared physical directory.

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
- stops exposing Skill Manager-owned deployment after disable/remove;
- restores it after a valid redeployment.

Testing must not damage existing user installations.

Isolated or disposable harness configuration should be used where appropriate, particularly for OpenCode.

## 10. Existing Foundation

The programme builds on work that already exists.

Tasks 01-04 established the OpenCode discovery and configuration foundation.

Task **04A - Discover multi-harness package capabilities** has already been implemented and is in review.

04A should be treated as an existing foundation. It should not be restarted simply because the wider programme has evolved.

If downstream work exposes a specific compatibility gap in 04A, that should be handled as a focused correction.

## 11. Programme Stages

The current programme is expected to progress broadly through:

**01-04 - OpenCode discovery/configuration foundation**  
Already completed.

**04A - Package capability discovery**  
Already implemented and in review.

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
