# Frontier 003 Git-state verifier report

Date: 2026-07-21

## Verdict

FAIL.

The focused suite and every static gate pass, and the exact fetch/add/remove happy paths are sound. The completed
Git-state slice nevertheless has five MUST defects and two SHOULD defects at literal trust-boundary seams. In
particular, an existing remote-tracking feature ref is classified as absent, S2 accepts a locked registration,
malformed Git protocol records are accepted, percent-encoded control text reaches the approved-origin identity, and
ignored-directory proof is not actually no-follow. Green happy-path coverage cannot override these frozen-contract
violations.

No product or test file was changed by this verifier.

## Findings

### MUST-1 - Remote-tracking feature refs are classified as absent

Path: `src/symphony/aidt_worktree/git_state.py:1104`

`_target_artifacts` searches only `refs/heads/<branch>` at line 1110. It never checks an existing
`refs/remotes/<remote>/<branch>`, although the frozen branch/path contract explicitly rejects both local and
remote-tracking feature refs before create/recovery. A direct public probe supplied
`refs/remotes/origin/fix/A20-1188` and no local artifacts; `classify_target_artifacts(...)` returned `absent`.
`git worktree add -b` may therefore create the local feature branch despite the declared remote collision.

Required correction: classify any remote-tracking ref for the exact derived feature branch as `AMBIGUOUS` and add
origin/other-remote fixtures before add and prepared recovery.

### MUST-2 - Create/removal delta proof accepts a locked target registration

Path: `src/symphony/aidt_worktree/git_state.py:828`

`_created_target` excludes detached and prunable registrations but omits `not item.locked` at line 832. The stricter
target classifier rejects locked worktrees, so phase proof is internally inconsistent. A constructed exact S1/S2
probe with the sole new ref/registration and `locked=True` returned a valid create-delta digest instead of blocking.
The same helper is reused by removal proof. An external `git worktree lock` between add and S2 can consequently be
accepted as the expected creation delta.

Required correction: require the exact unlocked registration in `_created_target` and bind locked S2/cleanup-pre
regressions.

### MUST-3 - Command-specific parsers accept records Git cannot emit

Paths: `src/symphony/aidt_worktree/git_state.py:1426`,
`src/symphony/aidt_worktree/git_state.py:1496`

The status validator allows `?` and `!` inside a type-1/type-u `XY` field. A direct probe of a type-1 `??` record
returned `StatusEntry(kind='tracked', ...)`; untracked/ignored entries have separate `? `/`! ` record types. The ref
validator is also weaker than Git ref grammar: both `refs/heads/.hidden` and `refs/heads/@` were accepted as
`RefRecord` values. Similar looseness exists in the unrestricted three-digit rename score and generic six-octal
mode patterns.

The plan requires strict command-specific parsing and malformed injected results to fail closed. These accepted
records feed root/ref/registration delta decisions, so merely bounding their bytes is insufficient.

Required correction: bind each porcelain record type to its exact XY/submodule/mode/score grammar and implement the
complete relevant `check-ref-format` restrictions in the fixed ref parser. Add the demonstrated rows to the malformed
matrix.

### MUST-4 - Approved-origin parsing accepts percent-encoded control bytes

Path: `src/symphony/aidt_worktree/git_state.py:1627`

`_valid_origin_path` percent-decodes the path but then rejects only backslash and dot segments. It does not reject
decoded C0/DEL bytes. The direct probe
`canonical_origin_digest("https://example.test/repo%0Aevil.git")` returned a 64-hex digest instead of
`protocol_invalid`. This violates the exact origin rule forbidding control text and lets a semantically controlled
path become the repository identity used to authorize the fixed `origin` fetch.

Required correction: reject decoded control/DEL bytes (and test raw/encoded controls across path and permitted SSH
userinfo) before producing the normalized origin digest.

### MUST-5 - Ignored-directory proof follows a pathname across the no-follow seam

Path: `src/symphony/aidt_worktree/git_state.py:989`

`_walk_ignored` hashes the current node, then calls `os.scandir(directory)` by pathname at line 998. It neither opens
the directory with `O_DIRECTORY|O_NOFOLLOW` nor rechecks the directory identity around enumeration. A directory can
be replaced by a symlink between `_is_directory`/the initial lstat and `scandir`; `scandir` follows that symlink.
This is the exact race Amendment N's recursive no-follow proof is intended to exclude. Per-child parent checks do
not close an empty-directory target or a replace-and-restore race, and no directory identity is compared after the
scan.

Required correction: enumerate from a no-follow directory descriptor, verify before/open/after identity, and add
fault seams for directory-to-symlink replacement before open, during enumeration, and before the second status read.

### SHOULD-1 - Entry caps are checked only after unbounded directory materialization

Paths: `src/symphony/aidt_worktree/git_state.py:998`,
`src/symphony/aidt_worktree/git_state.py:609`

Ignored entries are fully consumed and sorted before `_ContentBudget` can enforce the 10,000-path cap. Hook entries
are converted to a complete list before the 2,500-entry check. Very large user-controlled directories can therefore
consume unbounded memory before the advertised bounded failure. Iterate with cap-plus-one accounting first, then
sort only the bounded collection.

### SHOULD-2 - The hooks-directory object itself is followed

Path: `src/symphony/aidt_worktree/git_state.py:605`

The preflight checks each hook entry with no-follow stat, but `hooks.exists()` and `os.scandir(hooks)` follow a
symlink used as the common-Git `hooks` directory. An empty or non-executable symlink target passes, so the builder
report's blanket “symlink hooks” rejection is not established. The fixed `core.hooksPath=/dev/null` prevents hook
execution on the approved mutations, which limits this to SHOULD, but the conservative preflight should lstat and
require the hooks root itself to be an exact directory before scanning.

### NIT

None.

## Verified invariants

- `FETCH_ARGV` is byte-for-byte the Amendment L vector, including all ordered global options and the forced
  `+refs/heads/aidt-prd:refs/remotes/origin/aidt-prd` refspec.
- `git_environment()` is the exact allowlist with `/dev/null`, false askpass programs, prompt/global/system config
  suppression, optional `SYSTEMROOT`, and no sentinel environment leakage.
- The default runner uses `stdin=DEVNULL`, no shell, and a new process session. A fresh timeout/overflow probe returned
  timeout `True`, return code `-9`, elapsed under one second, and stdout overflow `True` with exactly the 32-byte cap;
  both children were reaped.
- Happy-path repository identity binds top-level, Git/common/object directories, SHA-1 format, device/inode path
  digests, and the canonical origin digest. A symlink-parent service-root probe failed closed as `identity_invalid`.
- Root proof detects same-porcelain tracked and ignored content changes, hashes the index, rejects special files, and
  binds S0/S1/S2/cleanup snapshots. MUST-5 prevents accepting the complete no-follow claim.
- Real `worktree add --no-track -b` and plain `worktree remove` passed; root dirty/untracked/ignored state stayed
  equal and the feature branch remained after removal.
- The product source contains no reset, rebase, switch, checkout, prune invocation, forced removal, recursive delete,
  or branch-delete path. Unsafe local filter/SSH/upload-pack configuration and executable hook entries block before
  mutation.
- POSIX advisory-lock fixtures prove two-process common-Git serialization and kernel release after process crash;
  lock ordering helper is common-Git then manifest.
- The public facade stays lazy: `git_state` is absent after package import and appears only on first Git-state export.

## Verification evidence

| Gate | Exact result |
|---|---|
| Focused `tests/test_aidt_worktree_git_state.py` | 39 passed in 17.55s |
| Ruff over Git-state product/facade/test | `All checks passed!` |
| Pyright over Git-state product/facade | 0 errors, 0 warnings, 0 informations |
| Independent product AST gate | 100 functions; maximum 39 lines; maximum nesting 4 |
| Tracked `git diff --check` | exit 0, no output |
| No-index checks for three untracked owned files | expected exit 1 for content difference; no whitespace output |
| Fresh default-runner timeout/overflow probe | timeout and overflow killed/reaped at the exact caps |
| Remote-ref/parser/origin semantic probe | reproduced MUST-1, MUST-3, and MUST-4 |
| Locked S2 delta semantic probe | reproduced MUST-2 with a returned digest |

The focused tests do not contain the demonstrated remote-feature collision, locked-registration delta, encoded
control origin, impossible type-1 status, invalid ref-name, or ignored-directory replacement seams.

## Scope and audit notes

Read: repository `AGENTS.md`, all three binding amendments in `PLAN.md`, every plan-attack recheck,
`git-state-builder-report.md`, Git-state product/facade, focused tests, and the narrow lock/snapshot definitions they
invoke. `CLAUDE.md` is absent from the worktree (`rg --hidden --files -g CLAUDE.md` returned no path).

The three builder-owned product/test paths were already untracked in the shared Frontier worktree. This verifier
wrote only this report through `apply_patch`. No product/test edit, live repository, network access, AIDT checkout,
commit, branch/ref mutation outside pytest temporary fixtures, or activation occurred.
