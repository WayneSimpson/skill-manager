# Authoritative package source resolution (Task 04B)

Local discovery is an observation, not package ownership or proof that the local
copy is complete. `PackageSourceResolver` resolves explicit provenance, downloads
an inert upstream snapshot, establishes its package boundary, and calls 04A's
`SourcePackageDiscovery`. It does not adopt, persist, deploy or execute packages.

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
artifacts remain until the caller cleans its workspace. No HTTP/UI contract was
added, and normal inventory/detail reads do not trigger upstream resolution.

`PackageResolution` contains:

- `status`: `resolved`, `ambiguous`, `unresolved`, or `unavailable`;
- `observation_ref`, `relationship`, and allowlisted `evidence`;
- `source`: canonical kind/locator, intended ref, exact acquired revision, npm
  version/integrity/artifact URL, and repository-relative package/skill paths;
- `artifact_root` and explicit `artifact_lifetime`;
- `capabilities`: the existing 04A map, or null with a limitation;
- `reason`, `limitations`, and explicit `distributions` source relationships.

`resolved` means source was acquired at a proven boundary. It does not mean the
package is supported by any native loader. A package.json-only source can resolve
with no 04A capability map. The map's local-root ID is temporary; Task05 should
persist the resolved source locator/revision/path, not treat that ID as a global
package identity. Source refs/revisions are distinct from 04A's structural hash.

## Supported evidence

1. **Existing Skill Manager / skills.sh GitHub source locators.** The existing
   marketplace's `SkillsShSkill.source_locator` and decoded install-token values
   feed the inventory source unchanged. An exact stored skill path/ref is retained.
   Without a stored path, an exact skill directory/frontmatter identifier must be unique within
   the already-proven repository. There is no repository search by display name.
2. **Manually/native-installed file-backed skills.** Existing SKILL frontmatter
   `source_kind`/`source_locator`, supported plugin `repository` declarations and
   package.json `repository` (string or Git object with `url`/`directory`). Plugin
   membership is checked by 04A, not inferred solely from an ancestor filename.
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

An exact native lock is the installation-source authority; package repository
metadata describes its source rather than overriding the locked distribution.
Other conflicting explicit locators, refs, paths or commits remain ambiguous.
Separate declarations of a tag and a detached commit do not prove they agree.
Package manifest versions are not guessed to be Git tags.

## Acquisition and package boundaries

GitHub acquisition extends the existing `GitHubSource` / `SourceFetchService`.
The commits API resolves the specified ref (or HEAD when none was known) to a
40-character commit. Codeload supplies the archive at that immutable commit.
There is no clone/checkout, submodule/LFS fetch or package-manager invocation.
Nested package roots are taken from explicit repository directory metadata or
04A's bounded membership discovery for the pinned skill. Unrelated repository
siblings are discarded before returning the artifact. An arbitrary repository
root without a package manifest is not substituted when membership fails.

npm acquisition downloads the exact version's published tarball, not an invented
Git tag. Registry repository/directory and optional publisher-provided `gitHead`
are retained as an explicit distribution-to-source relationship. They do **not**
prove byte equivalence or a native harness. No universal cross-repository package
family schema was found: no sibling packages or harness names are inferred.

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
- Local observations and metadata symlink aliases fail closed in 04B; original
  installations and the central store are never mutated. Existing standalone
  management and 04A discovery behavior remain separate and unchanged.
- Undocumented installed_plugins/OpenCode cache formats, custom registries,
  npm range/tag resolution, SHA-1-only tarballs, Git worktree indirection and
  unproven catalogue-to-install mappings are not guessed. Native npm and explicit
  repository provenance work without Skill Manager marketplace registration.
- 04A's only compatibility additions are explicit-root inspection and an optional
  source boundary. Its manifest/component parsers are reused without duplication.

## Verified references

- [GitHub get a commit](https://docs.github.com/en/rest/commits/commits#get-a-commit)
- [npm package-lock packages entries](https://docs.npmjs.com/cli/v10/configuring-npm/package-lock-json)
- [npm repository and directory](https://docs.npmjs.com/cli/v10/configuring-npm/package-json#repository)
- [npm registry Version/dist contract](https://github.com/npm/registry/blob/master/docs/REGISTRY-API.md)
- [npm publish integrity construction](https://github.com/npm/cli/blob/latest/workspaces/libnpmpublish/lib/publish.js)
- [npm optional publisher gitHead](https://github.com/npm/cli/blob/latest/lib/commands/publish.js)
- Plugin repository declarations use the [04A verified contracts](source-package-capabilities.md).

Tests use local fixtures and mocked public bytes, including real isolated Git
metadata, partial Codex observation/full upstream manifests, exact refs, npm lock
integrity, ambiguous evidence, unavailable sources, malformed metadata/archives,
containment, no execution and original-install preservation. No live OpenCode
configuration or plugin cache is used. Durable ownership and native deployment
remain Task05 and later work.
