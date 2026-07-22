# GOAL - Frontier 003 AIDT Worktree Provisioner

Bind each provisionable AIDT route child to one isolated service worktree created from the exact freshly fetched
`origin/aidt-prd` commit, without changing any user checkout or allowing generic workspace cleanup to touch it.

## Success Criteria

- [x] Default-off configuration preserves every existing Symphony path and rejects unsafe AIDT profile settings.
- [x] Only a canonical pending route child can cross the dispatch filter; coordinators, review, stale, retained, and
  malformed cards remain blocked.
- [x] Worker-time re-attestation, repository identity, fresh fetch, and route/base equality all pass before backend
  construction.
- [x] New creation uses the routed service, exact branch convention, frozen base SHA, contained path, no upstream,
  and an atomic `prepared -> ready` ownership manifest.
- [x] Exact resume preserves ticket changes and performs no fetch, reset, rebase, recreation, hook, or cleanup.
- [x] Collision, drift, interruption ambiguity, protected-branch occupancy change, or dirty-root mutation fails closed
  with bounded diagnostics and no generic fallback.
- [x] Cleanup requires exact ready ownership plus explicit completed-ticket authorization, removes only the registered
  ticket worktree, and never force-removes, prunes, deletes a branch, or recursively removes an AIDT path.
- [x] Temporary local Git fixtures prove create, resume, concurrency, collision, dirty root, interruption, cleanup,
  initial/retry dispatch, reload, health, and unmanaged-workspace parity.
- [x] Fresh verification passes affected/full tests, Ruff, Pyright, structure, whitespace, doctor, and literal gate
  within the accepted repository baseline.

## Scope

This frontier owns typed route-child dispatch attestation, AIDT worktree configuration and health, manifest/Git
provisioning, the generic workspace delegate boundary, and the pre-backend worker barrier. It does not edit AIDT
product repositories, merge, push, deploy, update Jira, choose implementation files, or run live provisioning.
