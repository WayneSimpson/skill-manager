# Source-package capability discovery

An individual skill and its source package are separate things. The existing
managed `sourcePath` is still the original **skill directory**, never a package
root. Runtime adoption keeps that path; local adoption now also records the
directory actually copied for standalone adoption. Content-only temporary
directories are not recorded. This layer explains package contents; it does not
resolve upstream authority or install components. The
[source resolver](package-source-resolution.md) reuses it after acquisition, and
the [native planner](native-package-strategies.md) separately decides deployability.

## Root and identity rules

Discovery accepts an original absolute file-backed skill directory. It checks
that directory and at most eight parents for known manifests. It stops at the
first candidate manifest root, `package.json`, or `.git` boundary. It never
crosses the user's home, filesystem root, or configured stop paths (the application
supplies its central Skill store as a stop path). An unrelated
or invalid nearest manifest cannot cause discovery to continue into another
package. A manifest must include this exact resolved skill through its supported
skill paths; merely being an ancestor is insufficient.

All recognised manifests at that root contribute to one package map. The ID is
a hash of the resolved local root: two skills share it, but two separately
installed copies do not. This is not a globally unique repository/package ID.
The display name/version comes from Agent Plugins first, then Claude, Cursor,
Codex. This display preference is not a client loading/overlay policy.

Local capabilities are read afresh for each discovery/detail API request. A request-local
cache parses each root once. There is no persistent capability cache or schema
migration. Added/removed manifests, component paths and MCP/hook entry names are
reflected on the next request. `revision` hashes the structural capability map,
not complete source bytes or credentials; `sourceRevision` is separate per-skill
inventory provenance. Editing a script body alone does not change capabilities.
Managed-package capabilities are the evidence retained during authoritative
acquisition/capture; central refresh checks them for changes separately.

## Verified contracts

Checked 14 September 2026:

| Format | Evidence and implemented locations |
| --- | --- |
| Agent Plugins 1.0.0 | Root `plugin.json`, exact `$schema` `https://agent-plugins.org/schemas/1.0.0/plugin.schema.json`; immediate `skills/<name>/SKILL.md`, `mcp.json` with matching canonical MCP schema. Unknown fields/non-object extensions are reported and ignored; other supported manifest type/name violations reject that manifest. |
| Claude | `.claude-plugin/plugin.json`; default `skills/`, `commands/`, `agents/`, `hooks/hooks.json`, `.mcp.json`. Custom skills add to defaults; commands/agents replace defaults. Root `SKILL.md` fallback when neither skills field nor skills directory exists. Hooks/MCP custom paths or inline objects are represented alongside defaults. |
| Cursor | `.cursor-plugin/plugin.json`; skills, rules, agents, commands, `hooks/hooks.json`, `mcp.json`. Explicit component fields replace defaults. Root `SKILL.md` fallback when neither skills field nor directory exists. |
| Codex | `.codex-plugin/plugin.json`, verified against **runtime loader**, not marketplace ingestion requirements. Default `skills/`, `.mcp.json`, `hooks/hooks.json`, `.app.json`; explicit skill/hook/app/MCP file paths replace defaults; inline MCP coexists with the default file. String/array skill declarations and bounded recursive skill directories are supported. |

Authoritative sources:

- [Agent Plugins specification](https://agent-plugins.org/specification), sections 4–8 and 10.
- [Claude plugin reference](https://code.claude.com/docs/en/plugins-reference),
  component fields, root skill fallback, and path behaviour.
- [Cursor plugin reference](https://cursor.com/docs/reference/plugins),
  supported formats, component discovery and manifest fields.
- OpenAI Codex commit `3fa9039bd70d2e4f6b4e614e11b971e261e604e2`:
  [manifest parser](https://github.com/openai/codex/blob/3fa9039bd70d2e4f6b4e614e11b971e261e604e2/codex-rs/core-plugins/src/manifest.rs)
  and [component loader](https://github.com/openai/codex/blob/3fa9039bd70d2e4f6b4e614e11b971e261e604e2/codex-rs/core-plugins/src/loader.rs).
- [OpenCode plugins](https://opencode.ai/docs/plugins): executable modules and
  configured registrations, not a portable package-manifest contract.

The Codex source establishes the convention independently of any n8n example.
A multi-harness package such as n8n's skills can illustrate co-located manifests,
shared Skills and wider hook content; no package-name-specific rule is used.
OpenCode runtime provenance plus `package.json.main` alone does **not** establish a
portable capability contract. The separate [OpenCode native planner](native-package-strategies.md#opencode-native-contract)
checks a declared server export, or `main` with explicit OpenCode distribution evidence.
It does not add an OpenCode parser to this structural layer.
Legacy command migration, marketplace metadata overlays and
unimplemented client extensions are not reproduced. Vendor skill discovery is
direct-directory/immediate-child except Codex's bounded recursion.

## Read API and UI

- `GET /api/skills/source-packages`: deduplicated `packages` plus per-skill links
  with status, package ID, skill source kind/path/revision, and unresolved reason.
- Existing `GET /api/skills/{skill_ref}` adds `sourcePackage` with that same
  provenance and the package map, or an unresolved result.
- Package components include kind, relative path, declaring manifest, harness,
  deterministic evidence and `supported`. Config components include only MCP
  server/hook event **names**, never values or commands.
- Evidence: `declared_standard`, `declared_harness_manifest`,
  `verified_convention`, `unresolved`. Only standard skill/MCP components with a
  null harness are portable declarations. Unknown extensions are not portable.
- The skill detail UI distinguishes original skill source from package root,
  shows capabilities/evidence, and explains unresolved results.

The component field `supported: true` describes the existing individual-Skill copy
capability, not permission to extract a Skill from a managed package. MCPs, hooks,
apps, rules, agents and commands have no individual installation action here.
Their JSON shape/file presence is inspected, not full runtime transport/event
validity. A native harness may load them as part of a supported **whole package**.
The map is not a security audit, plugin runtime or deployment engine; package-backed
Adopt and native support are governed by their separate ownership and mechanism checks.

## Safety and limits

- No plugin/setup/lifecycle code, hooks, MCP, scripts, installers, package
  managers, LLMs or runtime refreshes run during classification.
- Resolved manifest/component/SKILL paths must remain inside the package root.
  Relative manifest paths reject parent traversal, absolute/drive/UNC paths and
  backslashes. Symlink escapes and loops are rejected. No script bodies are read.
- JSON reads are capped at 1 MiB and regular files only; directory listings at
  1,000 entries. Codex recursive discovery has an eight-level/1,000-entry limit.
  Failures produce structural diagnostics, not raw JSON or parser contents.
- Config values are never returned, expanded, logged or persisted by this layer.
  Source packages and user configuration are never modified by discovery.
- Content-only records, deleted original sources, ambiguous local sightings,
  GitHub-relative paths without retained checkouts, and old managed records
  without original provenance remain unresolved by local capability discovery.
  It fetches no remote checkout; source resolution may separately obtain and inspect
  an authoritative package using explicit provenance.
- Standalone adoption can replace the original folder with a store link under
  existing behaviour. Such a path cannot prove an external package afterward;
  the stored original hint remains, but capabilities become unresolved. Retained
  configured/runtime source folders keep their proven relationship.
- Capability discovery itself performs no adoption or mutation. Once package
  evidence is established, failed package resolution blocks package adoption;
  it does not trigger a standalone leaf-copy fallback. Existing standalone
  management and externally installed content remain separate.
