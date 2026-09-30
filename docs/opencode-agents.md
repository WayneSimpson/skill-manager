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

## Reserved API route names

Single-segment names that match static API routes under `/api/agents/opencode/`
are reserved and cannot be used for new agents or renames:

`editor-context`, `variant-options`, `model-catalogue`, `permission-actions`,
`apply-capability`, `apply-status`.

Route matching is method-aware, so names matching POST-only routes (such as
`apply`) are not reserved — an agent named `apply` is still addressable via
`GET/PUT /opencode/apply` without ambiguity.

Creating or renaming an agent to a reserved name returns a clear validation
error before any configuration is touched. An externally-authored config that
already contains a reserved-name agent remains discoverable in the listing
without corruption; such an agent is shown read-only with a
`reserved-api-route-name` reason because the static route takes precedence
over the `GET /opencode/{name}` detail path.

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

## Apply lifecycle (Task 13)

Saving and applying stay separate; saving never reloads or restarts anything.

Capability detection parses the authoritative CLI command registry from
`opencode --help` (never bare exit codes — unknown subcommands fall through to
the default command and exit zero, which would fake availability). On the
current OpenCode (verified live on 1.18.32) the registry contains no reload
command, so the detected mechanism is truthfully **restart-manual**:
configuration is loaded at startup, and no automated apply is offered. If a
future runtime really exposes a supported reload command, detection
automatically prefers it.

Mechanisms:

- `reload` — preferred when a supported reload command exists; executing it
  requires explicit confirmation, and pending state clears only after the saved
  configuration is verified still on disk and reported active.
- `restart-managed` — only when a `ManagedRuntimeRegistry` controller owns a
  process handle it created. The registry refuses handles nobody owns, so it
  can never target arbitrary or live foreign processes. Restart requires
  explicit confirmation plus per-handle verification against the saved config
  hash before pending clears.
- `restart-manual` — current default. The UI explains that changes are saved
  but the user must restart OpenCode themselves; no fake executable action is
  offered. An explicitly confirmed acknowledgement can clear the pending state
  as a user assertion.
- `unavailable` — capability unknown.

Pending-apply state is durable (`opencode-agent-apply.json`, atomic write,
mode 0600) and tracked per configuration target — including
legacy/non-write-target declaring files edited in place. `apply-status`
reports an aggregate pending flag plus per-target detail and the pending
target list, so a declaring-file edit can never disappear from the Apply UI.
The state survives UI refresh and backend restarts.

Before ANY apply, restart or manual acknowledgement, the service resolves the
complete pending set and re-hashes every pending file: nothing pending or a
missing/externally changed file is refused (409) before any runtime action,
with pending state untouched. The manual acknowledgement is only offered when
the detected mechanism is `restart-manual` (refused for reload/managed), is an
explicit user assertion rather than runtime verification, and acknowledges
every current pending target after the same preflight checks.

A reload is only safely executable when both an executor AND a runtime
verifier are configured; pending clears only after execution succeeds AND the
verifier confirms the saved configuration state for every pending target.
`capability` exposes `canExecute` accordingly: 1.18.32 unmanaged is
restart-manual with `canExecute: false`; a detected reload command without
executor+verifier reports `reloadAvailable: true` but `canExecute: false`;
managed restart is executable only while an owned controller handle exists.
The UI gates its executable Apply action on `canExecute` and never offers one
for manual restart. Failed or unverified applies never clear pending and never
touch the saved configuration. Both the apply and acknowledgement endpoints
enforce explicit `confirm: true` server-side; anything less is refused without
executing.
