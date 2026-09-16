# Authoritative package source resolution

Local discovery is an observation, not package ownership or proof that the local
copy is complete. `PackageSourceResolver` resolves explicit provenance, downloads
an inert upstream snapshot, establishes its package boundary, and reruns
[capability discovery](source-package-capabilities.md) against that upstream package.
The local Skill may be only a harness-specific subset; it need not have been
installed through Skill Manager's marketplace. Resolution itself does not adopt,
persist, deploy or execute packages.

## Service contract

Use the existing query service explicitly:

```python
with TemporaryDirectory(prefix="package-review-") as directory:
    result = skills_queries.resolve_package_source(skill_ref, work_dir=Path(directory))
    # Inspect/consume result.artifact_root inside this lifetime.
```

The lower-level `PackageSourceResolver.resolve(InventoryEntry, work_dir=...)`
accepts the same inventory provenance; the query entry point supplies the central
store exclusion and existing `SourceFetchService`. `work_dir` must already exist,
be caller-owned, and be separate from the original observation. Each call creates
a private scratch directory. Failures remove only that directory. Successful
artifacts remain until the caller cleans its workspace. The explicit
`POST /api/skills/{ref}/resolve-package` review endpoint uses this service without
exposing scratch paths. Normal inventory/detail reads do not trigger upstream
resolution. See the [HTTP/UI contract](package-management-ux.md#http-and-runtime-setup).

`PackageResolution` contains:

- `status`: `resolved`, `ambiguous`, `unresolved`, or `unavailable`;
- `observation_ref`, `relationship`, and allowlisted `evidence`;
- `source`: canonical kind/locator, intended ref, exact acquired revision, npm
  version/integrity/artifact URL, and repository-relative package/skill paths;
- `artifact_root` and explicit `artifact_lifetime`;
- `capabilities`: the structural map of the acquired package, or null with a limitation;
- `reason`, `limitations`, and explicit `distributions` source relationships.

`resolved` means source was acquired at a proven boundary. It does not mean the
package is supported by any native loader. A package.json-only source can resolve
with no capability map. The map's local-root ID is temporary; the
[managed store](managed-source-packages.md) uses source coordinates for durable
identity instead. Source refs/revisions are distinct from the structural capability
hash. GitHub content is pinned to an exact commit; npm content to an exact package
version and SHA-512 integrity (also stored as its `revision`). These source pins
are not harness executable-version gates.

| Result | Meaning | Effect |
| --- | --- | --- |
| `resolved` | Source acquired at a proven package boundary | Eligible for central capture; native support still needs a separate plan |
| `ambiguous` | Conflicting evidence or multiple possible upstream package/Skill boundaries | No usable artifact is returned; ambiguous authority is not adopted |
| `unresolved` | No usable deterministic source evidence | Original installation stays untouched; no guessed source or leaf-copy fallback |
| `unavailable` | A selected source could not be acquired or validated | Reason retained; no replacement from another source |

## Supported evidence

1. **Existing Skill Manager / skills.sh GitHub source locators.** The existing
   marketplace's `SkillsShSkill.source_locator` and decoded install-token values
   feed the inventory source unchanged. An exact stored skill path/ref is retained.
   Without a stored path, an exact skill directory/frontmatter identifier must be unique within
   the already-proven repository. There is no repository search by display name.
2. **Manually/native-installed file-backed skills.** Existing SKILL frontmatter
   with `source_kind: github` and `source_locator`, supported plugin `repository` declarations and
   package.json `repository` (string or Git object with `url`/`directory`). Plugin
   membership is checked by capability discovery, not inferred solely from an ancestor filename.
3. **Local Git checkout.** Read origin, HEAD, loose refs or packed refs as bounded
   data. No Git command/config includes/hooks/filters/credential helpers run for
   this route. Preserve the observed branch and exact commit; acquire that commit,
   not whatever the branch later points to. Git metadata outside the bounded
   ancestor search is not used. Linked Git worktrees are currently unsupported.
4. **Native npm installs.** A package-lock v2/v3 entry must name the exact observed
   `node_modules` package path and agree with its version. Git locks require a full
   GitHub commit; public npm locks require a matching registry artifact URL and
   SHA-512 integrity. Exact existing `npm:name@version` coordinates also work.
   Registry response and extracted package names/versions must match, and the
   tarball bytes must pass integrity verification. Package name/version alone in
   an arbitrary local directory does not establish registry installation.

An exact native lock is the installation-source authority. When found, that route
returns the lock as its evidence rather than combining it with frontmatter or
repository declarations. Outside that lock route, conflicting explicit locators,
refs, paths or commits remain ambiguous.
Separate declarations of a tag and a detached commit do not prove they agree.
Package manifest versions are not guessed to be Git tags.

## Acquisition and package boundaries

GitHub acquisition extends the existing `GitHubSource` / `SourceFetchService`.
The commits API resolves the specified ref (or HEAD when none was known) to a
40-character commit. Codeload supplies the archive at that immutable commit.
There is no clone/checkout, submodule/LFS fetch or package-manager invocation.
Nested package roots are taken from explicit repository directory metadata or
bounded membership discovery for the pinned skill. When a nested package is
selected, unrelated repository siblings are discarded before returning the artifact.
An arbitrary repository root without a package manifest is not substituted when
membership fails.

npm acquisition downloads the exact version's published tarball, not an invented
Git tag. When registry `repository` metadata is present, its repository/directory
is retained as an explicit distribution-to-source relationship. Only a valid
40-character publisher `gitHead` becomes that link's optional revision; missing
or invalid `gitHead` does not supply a revision. These declarations do **not**
prove byte equivalence or a native harness. No universal cross-repository package
family schema was found: no sibling packages or harness names are inferred.
An optional harness-scoped distribution relationship must identify an exact,
already-managed snapshot before the [native planner](native-package-strategies.md)
can select it. This selection is verified with explicit fixtures; public npm
repository links are unscoped and do not automatically supply it.

## Safety and intentional limits

- Public HTTPS only: GitHub API/codeload and registry.npmjs.org. No redirects,
  userinfo, URL query credentials or interactive authentication. Private,
  inaccessible, moved/redirected and unsupported hosts return a safe failure.
- Downloads: 32 MiB, 10-second socket timeout, checked 30-second elapsed limit;
  JSON metadata: 1 MiB. Archive expansion: 100 MiB, 10,000 entries and 64 path levels.
- Archives require one wrapper directory. Reject traversal, drive/absolute paths,
  backslashes, duplicates, links, special files and malformed archives. Failed
  extraction is removed. Retain ordinary supporting files and executable bits
  without executing anything.
- Exclude `.git`, `node_modules`, caches, common credential/config directories,
  `.env*`, `.npmrc`, `.pypirc`, `.netrc`, `.pem` and `.key` files. Content comes
  from upstream, never a recursive copy of arbitrary machine state. This is not
  a general-purpose secret scanner for arbitrary upstream file contents.
- Local observations and metadata symlink aliases are rejected by resolution; original
  installations and the central store are never mutated. Existing standalone
  management and local capability discovery remain separate and unchanged.
- Undocumented installed_plugins/OpenCode cache formats, custom registries,
  npm range/tag resolution, SHA-1-only tarballs, Git worktree indirection and
  unproven catalogue-to-install mappings are not guessed. Native npm and explicit
  repository provenance work without Skill Manager marketplace registration.
- Acquired snapshots use explicit-root inspection and an acquisition boundary.
  Existing manifest/component parsers are reused without duplication.

## Verified references

- [GitHub get a commit](https://docs.github.com/en/rest/commits/commits#get-a-commit)
- [npm package-lock packages entries](https://docs.npmjs.com/cli/v10/configuring-npm/package-lock-json)
- [npm repository and directory](https://docs.npmjs.com/cli/v10/configuring-npm/package-json#repository)
- [npm registry Version/dist contract](https://github.com/npm/registry/blob/master/docs/REGISTRY-API.md)
- [npm publish integrity construction](https://github.com/npm/cli/blob/latest/workspaces/libnpmpublish/lib/publish.js)
- [npm optional publisher gitHead](https://github.com/npm/cli/blob/latest/lib/commands/publish.js)
- Plugin repository declarations use the [verified capability contracts](source-package-capabilities.md).

Tests use local fixtures and mocked public bytes, including real isolated Git
metadata, partial Codex observation/full upstream manifests, exact refs, npm lock
integrity, ambiguous evidence, unavailable sources, malformed metadata/archives,
containment, no execution and original-install preservation. No live OpenCode
configuration or plugin cache is used by those tests. Accepted end-to-end checks
also include a live public GitHub acquisition with no capability manifest; this
proves acquisition, not native compatibility. Durable ownership is documented in
[managed packages](managed-source-packages.md), and native execution in
[deployment and verification](native-package-deployment.md).
