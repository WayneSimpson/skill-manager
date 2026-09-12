# Skill Manager Agent Guide

## Purpose

Maintain our fork of `mode-io/skill-manager`. Preserve upstream behaviour where practical and extend OpenCode integration.

## Work Model

The primary OpenCode agent may delegate small, scoped tasks to configured subagents, including `explore`, `general`, `coder`, `scout`, `reasoner`, `n8n-builder`, and `n8n-builder-advanced`. Subagents must complete their assigned task themselves and must never delegate further.

Use relevant available skills when they materially improve reliability, speed, or correctness. Prefer the smallest viable implementation, minimal effort, and the fewest practical iterations needed to satisfy the task.

Optimise for short review and feedback cycles. Surface a useful first result, plan, prototype, or implementation slice early enough for review rather than working autonomously for an extended period without feedback. Pause for review when early validation could materially change the direction. The exception is work that genuinely needs an extended uninterrupted run or when explicitly instructed to continue autonomously until completion.

## Substantial Work and ClickUp

ClickUp is the source of truth for substantial work:

- Workspace: `9015209298`
- Space: `Product & Engineering`
- List: `Development`
- List ID: `901526423631`

Before implementation, read the full task description, acceptance criteria, linked documents, and relevant comments.

When a ClickUp task is provided, keep it current throughout execution:

- Move the task into the appropriate status as work progresses, using the configured workflow: `backlog`, `scoping`, `in design`, `ready for development`, `in development`, `in review`, `testing`, `blocked`, `done`.
- Post concise progress comments at meaningful checkpoints so the task reflects what has been completed, what is in progress, and what remains.
- Record blockers, material risks, implementation decisions, and any clarification needed on the task rather than leaving them only in the agent session.
- Before marking work complete, post a final summary covering the outcome, validation performed, unresolved issues, and any follow-up work.
- Do not mark a task `done` until its acceptance criteria have been met and the relevant validation has actually been completed.

Keep ClickUp accurate enough that another person or agent can understand the current state of the work without relying on the conversation history.

## Engineering Rules

- Inspect the repository and existing behaviour before changing anything.
- Do not hard-code values, IDs, paths, credentials, or behaviour that should come from configuration or existing data.
- Prefer reusable, configurable, and dynamic solutions.
- Preserve existing functionality and avoid unrelated refactors.
- Do not make unsupported assumptions.
- Ask one focused clarification only when a material requirement is genuinely unclear.

## Validation

Run the relevant backend and frontend checks, tests, linting, type checks, and builds. Use browser verification where applicable. Never claim verification unless it was actually performed.

## Documentation Boundary

This file defines how agents work on the project. PRDs, implementation plans, acceptance criteria, and feature-specific requirements belong in ClickUp and/or `docs/`.
