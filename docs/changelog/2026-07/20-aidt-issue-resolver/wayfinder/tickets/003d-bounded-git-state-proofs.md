# 003d - Bounded Git state and recovery proofs

Route: RESEARCH / HISTORICAL UMBRELLA

Status: historical umbrella; aggregate verification passed

Blocked by: 003b, 003c

Unblocks: 003e

## Goal and interface contract

Own the Git-process seam: bounded binary execution, repository observation, exact fetch/add/remove commands, phase
delta validation, dirty-root/protected-occupancy proof, and prepared/ready/removed recovery classifications. Callers
receive typed observations/proofs; argv, environment, parsers, timeout, capture, kill, and reap stay inside.

## Historical file ownership

- `src/symphony/aidt_routing/git_objects.py` repository-binding observation additions
- `src/symphony/aidt_worktree/git_state.py`
- `tests/test_aidt_routing_git_objects.py`
- `tests/test_aidt_worktree_git_state.py`
- `tests/test_aidt_worktree_recovery_proofs.py`

This executed five-file slice is several thousand lines and is therefore a historical umbrella, not a Build ticket.

## Acceptance and proof

- The runner uses fixed argv/environment, no shell/stdin/prompt/global config, bounded streams and deadlines, and
  kills/reaps overflow or timeout processes without treating partial Git state as success.
- S0/S1/S2 and recovery proofs bind repository identity, exact allowed deltas, dirty content, refs, registration,
  target path, and protected occupancy; ambiguous shapes preserve evidence and block.
- Proof surfaces: the three named test files and `R-LOOP.md` R1/R2 plus fixed-deadline characterization evidence.

## Scope boundary

Does not decide lifecycle transitions, persist ownership records, authorize cleanup, or integrate the orchestrator.
