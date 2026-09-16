# OpenCode runtime skills

This optional server query discovers the Skills the running agent exposes. It is
separate from [static discovery](opencode-static-config.md) and the native CLI
[whole-package deployment route](native-package-deployment.md). Connecting the
runtime server neither grants package ownership nor enables native mutation.

Skill Manager saves the last successful deliberate refresh to
`opencode-runtime-skills.json` in its resolved state directory (including any
`SKILL_MANAGER_STATE_DIR` override), using an atomic file replacement. Startup
restores that snapshot locally as last-known/stale data, without contacting
OpenCode. Ordinary `/api/skills` scans never contact OpenCode.

The saved file contains only skill records, server URL, directory, format version
and UTC retrieval timestamp. Basic-auth credentials are never saved. Missing or
invalid saved data does not prevent static discovery; restore errors are shown
in runtime status. Snapshots can contain skill document text and local paths
and belong to the user's private Skill Manager state.

## API

All routes are under `/api/opencode/runtime-skills`.

### `GET /status`

Returns the current runtime connection state without making a network call:

```json
{
  "status": "disconnected | ready | error",
  "serverUrl": "http://127.0.0.1:4096",
  "directory": "/absolute/plugin/directory",
  "skillCount": 0,
  "error": null,
  "lastRefreshed": "2026-09-14T10:00:00+00:00",
  "stale": true
}
```

`serverUrl`, `directory`, and `error` are `null` while disconnected. Passwords
and usernames are never retained or returned.
`lastRefreshed` is null before a successful refresh. `stale` is true after
restoration or a failed refresh, and false following a successful live refresh.
Settings displays the timestamp and last-known warning independently of errors.

### `POST /refresh`

Requires an explicit consent flag and the selected local server connection:

```json
{
  "consent": true,
  "serverUrl": "http://127.0.0.1:4096",
  "directory": "/absolute/plugin/directory",
  "username": "optional-user",
  "password": "<password>"
}
```

The server URL must be an HTTP(S) loopback URL without URL credentials,
queries, or fragments. Username and password must be supplied together. The
request uses Basic authentication only for this request. Redirects are
blocked, and the response has a timeout and size limit.

Skill Manager requests OpenCode's release endpoint:

`GET /skill?directory=<absolute directory>`

This is the agent-facing `Skill.Service` surface previously verified on OpenCode
1.18.30; that version is historical diagnostic evidence only, not a deployment
gate.
It returns a bare array of skill records; it does not echo the directory.
The separate `/api/skill` surface returns a `{location, data}` wrapper and
can omit skills available to the actual agent. Skill Manager does not union
these endpoints or accept that wrapper as an agent inventory response. Invalid
non-filesystem location markers (including the agent API's built-in marker)
are not used as paths; their skill content is still retained. Malformed
individual records are ignored, but records with a name and no materializable
document remain visible. For standalone adoption, a valid local `SKILL.md` is
copied with its containing Skill directory; standalone `.md` documents are copied
as a single `SKILL.md`. If deterministic package evidence is found, adoption
instead uses [source review and whole-package management](package-management-ux.md)
without changing the original plugin directory. Content-only records without
package evidence can be materialized as standalone Skills when embedded content
is present. A record with neither readable
local content nor embedded content is shown but has `actions.canManage: false`
and a `canManageReason`.

If refresh fails, the previous runtime snapshot remains available and status
becomes `error`; static skills remain available. `POST /disconnect` clears the
runtime snapshot from memory and disk and returns the disconnected status.
Failures never overwrite the last successful saved snapshot, including a valid
empty snapshot. A failed refresh retains its original timestamp and connection
context. After restart the last success is restored as stale; transient refresh
errors are not saved. A save failure is reported and retains the previous snapshot.

## Source provenance after adoption

For a readable runtime `SKILL.md` adopted as a standalone Skill, the managed manifest's existing
`source_path` field retains the original skill directory, including when the
runtime sighting duplicates a static sighting. It identifies only that skill's
source directory, not an inferred plugin/package root. Adoption copies from it;
it does not write to it. Content-only skills have no invented source-path hint
or auxiliary files. [Package-capability discovery](source-package-capabilities.md)
uses this original location as bounded evidence, not an assumed package root.

## Detail locations

Configured and runtime sightings are retained internally for provenance and
adoption. The detail presenter displays each resolved physical path once per
location kind/harness, so overlapping observations do not become identical
OpenCode rows. Different physical paths and different owners remain distinct.
