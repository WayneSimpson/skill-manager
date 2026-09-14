# Static OpenCode config resolution

Task 01 provides a read-only resolver shared by skill and MCP readers. It
reads these files in low-to-high precedence order:

1. `~/.opencode/opencode.jsonc` (legacy)
2. `$XDG_CONFIG_HOME/opencode/opencode.json`
3. `$XDG_CONFIG_HOME/opencode/opencode.jsonc`

Existing valid objects are merged recursively. Nested objects keep distinct
keys; arrays and scalar values from the higher-precedence file replace lower
values. Missing, invalid, or unreadable files are reported as non-sensitive
source diagnostics and do not prevent other valid files from being read.

This is a static compatibility policy, not a complete implementation of
OpenCode's upstream configuration resolution. It does not invoke OpenCode,
retrieve live runtime state, or include plugin-added paths during ordinary
scans. Explicit runtime retrieval is documented separately in
`docs/opencode-runtime-skills.md`.

## Task 02 skill discovery

Skill Manager reads the merged `skills.paths` list from this resolver for
OpenCode skill discovery only. It accepts only absolute string paths that are
currently directories; malformed values, missing directories, and paths that
cannot be inspected are skipped independently. Relative paths are skipped
because this static global inventory has no OpenCode project directory against
which to resolve them. The canonical OpenCode root (including its environment
override) and the Claude/Agents compatibility roots remain in the scan, and
physical duplicate roots (including symlinks) are scanned once.

Configured roots are read again when a skills scan is performed, so changes to
the static config are visible after the normal read-model refresh. They are
never used as enable/disable mutation targets. This remains discovery-only: it
does not invoke OpenCode, use live APIs or plugins, mutate user config, or
change MCP behavior.

## Task 04 MCP discovery and writes

MCP discovery consumes the same merged static `mcp` section and existing
OpenCode local/remote codec. Partial overrides, including `enabled: false`,
inherit omitted fields from lower-precedence files. Invalid/unreadable files
produce a diagnostic while valid files remain visible. This never contacts
OpenCode or invokes an MCP server.

The write target is the highest-precedence **existing** supported config file.
If none exists, Skill Manager creates `$XDG_CONFIG_HOME/opencode/opencode.jsonc`.
Selection is evaluated again at each operation, including when an external edit
creates a new higher-precedence file. Harness `configPath` describes this write
target. An individual server's source/config choice describes its highest
declaring file; its effective fields can also be inherited from lower files.

Enabling/updating writes the complete selected server definition to the target
and removes that server from other supported files, avoiding leftover overrides.
Disabling removes it from every supported file so a lower-priority copy cannot
reappear. Other servers and unrelated settings are retained. Malformed or
unreadable config sources block mutation before any config is written.

Writes retain the existing per-file locks and atomic replacement, not a
multi-file transaction. An I/O failure is reported; a failure during cleanup
can require retry after fixing the filesystem problem. JSONC input is supported
through the shared parser, but modified files are serialized as formatted JSON:
comments and original formatting are **not preserved**. No secrets are expanded
or logged by this discovery layer.
