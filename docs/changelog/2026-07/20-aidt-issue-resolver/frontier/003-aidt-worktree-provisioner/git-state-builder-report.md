# Frontier 003 Git-state builder report

Date: 2026-07-21

Branch: `run/symphony-aidt-orchestrator-20260720`

Scope: bounded Git-state layer only; no commit

## Outcome

PASS. The AIDT worktree package now exposes one lazy Git-state module that observes and mutates only the frozen
repository/worktree identities needed by the later provisioner. It does not implement provisioning, manifests,
runtime/core/workspace wiring, cleanup authority, or lifecycle transitions.

## Theory and implementation

- The product runner accepts a binary argv, canonical service `cwd`, exact allowlisted environment, timeout, and
  stdout/stderr caps. Its default implementation uses no shell or stdin, starts a process group, and kills/reaps on
  timeout or channel overflow. Malformed injected results fail closed without exposing captured output.
- Production fetch is one byte-exact argv with the forced `aidt-prd` refspec, HTTPS/SSH-only origin parsing, exact
  `/dev/null` and askpass settings, and no tags, submodules, FETCH_HEAD, prune, or implicit destination. The injected
  fixture sees the canonical HTTPS origin and production request, then moves only the fixed remote-tracking ref to a
  commit supplied by a temporary local bare repository. No file URL reaches product parsing or the product runner.
- Repository identity binds canonical top-level, root Git directory, common-Git directory, object directory, SHA-1
  format, device/inode identities, and the domain-separated canonical origin digest. Raw origin text is neither
  persisted nor rendered.
- State observations use strict bounded parsers for porcelain-v2 status, refs/upstreams, and NUL worktree registry.
  Snapshots bind root HEAD/symbolic ref, full refs and registry, protected-branch occupancy, target ref/registration,
  fixed base ref, status, index, and dirty content.
- Dirty proof hashes tracked-dirty, untracked, and recursively enumerated ignored regular files, directories, symlink
  payloads, deletion tokens, and the index. Opens are no-follow with before/open/after identity checks. The product
  enforces 10,000 paths, 512 MiB total content, 4,096-byte relative paths, 2,500 refs/registrations, and 1 MiB Git
  metadata channels; special files, administrative paths, races, and cap excess block.
- S0 -> S1 permits only the fixed remote-tracking-ref delta. S1 -> S2 permits only one exact local feature ref and
  one exact worktree registration. Cleanup permits only disappearance of that registration while retaining the
  branch. Root proof, protected occupancy, and unrelated refs/registrations must remain byte-equal.
- Creation runs exact `git worktree add --no-track -b <branch> <path> <sha>`. Removal runs exact plain
  `git worktree remove <path>`. Ticket observation proves exact branch/HEAD, bounded status, no upstream, and base
  ancestry. Target artifact classification is typed `ABSENT|EXACT|AMBIGUOUS` for prepared/removing recovery.
- Pre-mutation checks reject local filter process/smudge/clean, `core.sshCommand`, remote upload-pack, include-based
  indirection, configured hooks paths, symlink hooks, and executable non-sample hooks. Command spies prove there is
  no reset, rebase, switch, checkout, prune, force removal, recursive deletion, or branch deletion path.
- Fetch performs the first post-fetch binding recomputation through an injected observer-compatible seam. The public
  `verify_service_binding` operation provides the separately required second recomputation immediately before add.

## TDD evidence

Red was observed before each vertical slice:

- missing lazy Git-state exports;
- missing repository identity/runner seam;
- missing strict status/ref/worktree parsers;
- missing root dirty-content observation;
- missing fetch/add/remove/delta/classifier/ticket primitives;
- initial AST nesting gate failure at six, reduced to four by replacing the nested status dispatch.

Final focused result: **39 passed, 0 failed**.

The focused suite includes real temporary repositories and linked worktrees, malformed result/parser inputs,
same-status tracked and ignored content mutation, unsafe hook/filter/transport configuration, exact fetch injection,
real add/plain remove, retained feature branch, unrelated-state phase proofs, two spawned-process common-Git lock
serialization, and kernel lock release after process crash.

## Verification

| Gate | Result |
|---|---|
| `tests/test_aidt_worktree_git_state.py` | 39 passed, 0 failed |
| Frontier route/foundation compatibility | 259 passed, 0 failed |
| Compatibility execution | 133 + 74 + 34 + 16 + 1 + 1 shards; the two large object-cap fixtures ran separately to remain under the 30-second command window |
| Ruff, exact product/test scope | all checks passed |
| Pyright, Git-state product/facade | 0 errors, 0 warnings, 0 informations |
| Product AST | 100 functions; maximum 39 lines; maximum control nesting 4 |
| Fresh-process lazy facade | passed; Git-state module absent until its first export is requested |
| Owned-file whitespace/EOF and diff check | passed |

## Owned files

- `src/symphony/aidt_worktree/git_state.py`
- lazy exports only in `src/symphony/aidt_worktree/__init__.py`
- `tests/test_aidt_worktree_git_state.py`
- this report

No live repository, network remote, Jira, AIDT product checkout, manifest transition, provisioner, workspace/core
integration, recursive deletion, branch deletion, or commit was used. Other dirty-worktree files were preserved.
