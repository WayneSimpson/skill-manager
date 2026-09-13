# OpenCode runtime skills

Skill Manager keeps one runtime snapshot in memory for the lifetime of the
server process. It is empty on startup and is never refreshed in the
background. The ordinary `/api/skills` scan reads this snapshot but never
contacts OpenCode.

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
  "error": null
}
```

`serverUrl`, `directory`, and `error` are `null` while disconnected. Passwords
and usernames are never retained or returned.

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

`GET /api/skill?location[directory]=<absolute directory>`

The response context directory must match the requested directory. Invalid
individual records are ignored, but records with a name and no materializable
document remain visible. A valid local `SKILL.md` location is copied from its
exact containing package directory when the user chooses Manage; standalone
`.md` documents are copied as a single `SKILL.md`. The plugin directory itself
is never replaced. Content-only records can be materialized as a standalone
package when their embedded content is present. A record with neither readable
local content nor embedded content is shown but has `actions.canManage: false`
and a `canManageReason`.

If refresh fails, the previous runtime snapshot remains available and status
becomes `error`; static skills remain available. `POST /disconnect` clears the
runtime snapshot and returns the disconnected status.
