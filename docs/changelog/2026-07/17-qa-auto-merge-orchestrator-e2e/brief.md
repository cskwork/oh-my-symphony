# QA brief - terminal auto-merge rejection and recovery

- Date: 2026-07-17
- Goal: exercise the patched terminal auto-merge through a real Symphony worker lifecycle and find failures hidden by unit-level coverage.
- Comparison: functional.
- Target: a disposable local clone of this checkout, a disposable file board, a real Codex worker, and a local bare Git remote with a one-shot push rejection.
- Driver: Symphony CLI, HTTP state API, Git CLI, and the real Codex app-server backend.
- Database: skipped; this path has no database dependency.
- Action budget: 100 browser actions; this CLI-only run expects 0.

## Acceptance

1. A real ticket advances through In Progress, Verify, Learn, and attempts Done with contract-valid evidence.
2. A rejected target push returns `push_failed`, moves the ticket to Blocked, preserves the worker workspace, leaves the remote ref stale, and creates exactly one local merge commit.
3. After service restart and removal of the rejection, moving Blocked back to the active Learn state lets a real worker return to Done; the retry pushes and verifies the existing target commit, keeps the merge count at one, and cleans the completed workspace.
4. The final API reports no active or retrying workers, and the original checkout and real board remain unchanged.

## Boundaries

- Must: worker lifecycle, rejection gate, immediate workspace preservation, restart behavior, exact remote readback, idempotent recovery through Blocked -> Learn -> Done, cleanup.
- Should: exercise the configured-empty-capture staged-empty retry path.
- Not covered: hosted Git authentication, hosted branch-protection policies, concurrent Done tickets, browser/TUI presentation, and databases.
- Safety: all board, repo, origin, workspace, hook, and service mutations stay under a new `/private/tmp` run root.
