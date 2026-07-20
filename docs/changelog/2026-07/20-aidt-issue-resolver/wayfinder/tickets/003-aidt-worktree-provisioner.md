# 003 - AIDT worktree provisioner

Route: GREENFIELD

Status: drafting

Blocked by: 002

Unblocks: 004, 005

## Goal

Create/resume one service-local ticket worktree from a recorded origin/aidt-prd SHA while preserving dirty
user state and preventing new occupancy of aidt-dev or aidt-stg.

## Acceptance criteria

- Provisioning runs only in the routed service repository and freezes the fetched origin/aidt-prd SHA.
- Backend/frontend branch names follow A20 feature/fix conventions and never use protected names.
- Resume requires stored service, branch, base SHA, worktree path, and scope manifest; mismatch blocks without
  reset, rebase, recreation, or cleanup.
- Pre/post worktree evidence proves no new protected-branch occupancy or mutation of dirty/unrelated checkouts.
- Cleanup removes only worktrees registered to the completed ticket and never deletes branches automatically.

## Proof commands and surfaces

- pytest -q tests/test_aidt_worktree_provisioner.py
- Temporary Git fixtures for create, resume, collision, protected branch, dirty root, interruption, and cleanup.
- git worktree list --porcelain snapshots before and after each fixture.

## Scope boundaries

- Owns ticket worktree identity, creation, resume, manifest, and scoped cleanup.
- Does not route, edit product code, merge to aidt-dev, push, or trigger Jenkins.

## External blockers

- Temporary-repository proof is unblocked.
- Live provisioning requires accessible service checkouts and fetchable origin/aidt-prd; unexpected state blocks.
