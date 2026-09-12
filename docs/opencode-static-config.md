# Static OpenCode config resolution

Task 01 provides a read-only resolver for future skill and MCP readers. It
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
retrieve live runtime state, or include plugin-added paths. A later task should
add live retrieval only when the caller needs the effective runtime
configuration. The resolver does not change the existing MCP mutation paths;
in particular, the XDG `.jsonc` path is not added to MCP write discovery here.
