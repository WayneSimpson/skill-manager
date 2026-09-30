# OpenCode agents (read-only discovery)

Task 11 adds a first-class, read-only view of OpenCode agents declared in
supported configuration. It builds on the shared OpenCode configuration
foundation and adds no second path/precedence implementation. Both the V1-style
`agent` section and the V2-style `agents` section are normalised into one
internal read-only representation.

## Discovery

`skill_manager/opencode/agents.py::discover_config_agents` reads the merged
`agent` and `agents` sections from `resolve_opencode_config` (Task 01/04
resolver: legacy `~/.opencode/opencode.jsonc`, XDG `opencode.json`, XDG
`opencode.jsonc`, in the established precedence). Per-agent source attribution
comes from the resolver's `entry_sources`, so each agent shows which file
declares it. Discovery performs no writes, does not run OpenCode, and lists at
most 500 agents.

If the same agent name is defined in **both** sections, authoritative OpenCode
precedence for that coexistence is not documented: both definitions are
preserved, shown read-only with a `defined-in-both-v1-and-v2-sections` reason,
and a discovery-level diagnostic is reported. Nothing is guessed or merged.

Markdown agent files (`~/.config/opencode/agents/*.md`, `.opencode/agents/`)
and plugin/runtime-provided agents are **not** discovered yet; the static
limitation is surfaced in the API response.

## Normalised model (both generations)

Canonical fields with per-generation syntax recorded in `schemaGeneration`
(`v1`/`v2`) so later safe editing knows which syntax was read:

| Canonical | V1 source | V2 source |
| --- | --- | --- |
| `instructions` | `prompt` | `system` |
| `permissions` (raw fidelity: object vs rule list) | `permission` object | `permissions` list |
| `disabled` | `disable` | `disabled` |
| `model` + `variant` | `model` + `variant`/`reasoningEffort` extra | `provider/model#variant` string or `{providerID, model, variant}` object |

Typed known fields also include `description`, `mode`, `temperature` (V1),
`top_p` (V1), `steps`, `hidden`, `color`, and V1's deprecated `tools` (raw).
Everything else — including V2 `request` overlays — is preserved verbatim in
`additionalOptions`. V1 `variant`/`reasoningEffort` are surfaced as the
first-class `variant` AND kept raw in `additionalOptions`. The original model
selection is preserved in `modelRaw`. Variant names are never hard-coded; they
are whatever the configuration declares. Unconfirmed V2 model object shapes are
preserved verbatim without normalisation. A definition that is not a JSON
object is retained, flagged invalid and shown read-only.

## Source and editability

Each agent reports its declaring config file, format, and whether that file is
the resolver's selected write target. `editability` is `config` when the agent
is declared in the selected write target, otherwise `read-only` (for example a
definition inherited from the legacy file, or a both-sections name). Task 11
exposes **no mutation controls at all**: every agent is displayed read-only
regardless of editability, and discovery never implies ownership. Editing,
saving, applying and reload/restart arrive in later tasks under the guarded
lifecycle defined by PRD Section 16.

## API

- `GET /api/agents/opencode` — list with write target, per-source status and
  diagnostics.
- `GET /api/agents/opencode/{name}` — one agent (404 when unknown).
- `GET /api/agents/opencode/editor-context` — write target, deterministic
  create-generation decision and current source hash.
- `GET /api/agents/opencode/variant-options?model=` — model-specific variant
  options read from OpenCode's own model catalog when available (never a
  hard-coded set).
- `POST /api/agents/opencode/preview-create` / `preview-update/{name}` —
  human-readable diff of the exact proposed change; writes nothing.
- `POST /api/agents/opencode` / `PUT /api/agents/opencode/{name}` — guarded save.

Both reads are read-only. `prompt`/`permission` are legacy aliases of the canonical
`instructions`/`permissions`. The UI area (`/agents`) lists agents with a
generation badge, shows Instructions and Reasoning/Variant as first-class
fields, preserved additional options, permissions, and source/editability
state, with an explicit read-only badge.

## Safe create/edit persistence (Task 12)

The editor writes only the declaring configuration file, surgically replacing
one agent member so unrelated settings, unknown keys, sibling agents and all
JSONC comments outside the edited definition remain byte-identical. Editing a
legacy-file definition patches it in place; definitions are never migrated
between V1 and V2 syntax, and the read syntax (`schemaGeneration`) is carried
through every write.

Guardrails around every save:

- The complete candidate configuration is validated before anything is written.
- The source hash must match what the user reviewed; concurrent changes are
  blocked with a retry message rather than overwritten.
- A recoverable backup is written outside the OpenCode configuration directory
  under Skill Manager state (`opencode-agent-backups`, directory `0700`, backup
  files `0600`).
- The write is atomic (temporary file + replace) and preserves the original
  file mode; the result is read back and verified, with automatic rollback on
  verification failure and an explicit backup path if rollback itself fails.
- New agents are always created with an explicit `subagent` mode; name changes
  are key renames that block same-section and cross-generation collisions.
- Same-name V1/V2 definitions stay non-guessing: editing requires an explicit
  `schemaGeneration`, otherwise the request is refused.
- Saving never applies, reloads or restarts OpenCode. The UI distinguishes
  unsaved drafts, saved configuration, and a pending-apply note that changes
  take effect only when OpenCode next reloads.

No-op saves write nothing and create no backup. Deletion is not part of this
slice.
