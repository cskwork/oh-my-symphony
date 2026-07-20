# 006 - Dev merge promotion

Route: GREENFIELD

Status: pending

Blocked by: 004, 005

Unblocks: 007, 010

## Goal

Promote one verified service source SHA to origin/aidt-dev through an isolated temporary merge worktree,
with post-merge proof and non-force race handling.

## Acceptance criteria

- Promotion serializes per service, fetches origin/aidt-dev, records its SHA, and never checks out local
  aidt-dev in a main service directory.
- A temporary merge branch/worktree starts from the remote target and merges the exact source with --no-ff.
- Conflicts or post-merge test/build failures block with preserved evidence and no push.
- Push uses normal non-force HEAD:aidt-dev; rejection requires fetch/rebuild/retest, never force.
- The verified remote merge SHA is recorded as the only deployment-authorized SHA.
- Cleanup affects only ticket-created resources and proves protected-branch occupancy did not change.

## Proof commands and surfaces

- pytest -q tests/test_aidt_dev_promotion.py
- Temporary bare remotes for merge, conflict, failed tests, concurrent rejection, resume, SHA proof, and cleanup.
- Promotion evidence with target-before, source, merge, and remote-after SHAs.

## Scope boundaries

- Owns aidt-dev merge construction and non-force promotion only.
- Does not create source worktrees, trigger Jenkins, deploy, perform dev QA, or clean user-owned state.

## External blockers

- Temporary-remote proof is unblocked.
- Live fetch/push requires remote access and explicit issue authorization; infrastructure approval is insufficient.
