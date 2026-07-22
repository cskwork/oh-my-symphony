# Frontier 003 Core Integration RED Attack Review

Verdict: REQUEST CHANGES.

The first RED handoff demonstrates missing product integration, but it does not yet prove every frozen Core/workspace boundary. Product implementation must wait until the following gaps are represented by behavioral tests.

## Blocking gaps

1. Prove default runtime construction and the first `start()` publication before manager handoff; manual runtime attachment is insufficient.
2. Exercise owned-admission suppression through `_on_tick`, not only a helper.
3. Trace admission before slot, eligibility, and conflict checks.
4. Exercise real `WorkspaceManager.path_for` results and HANDLED `before_run` suppression of generic hooks.
5. Reject half-paired `_dispatch` generation/admission arguments before path lookup, lease acquisition, or task creation.
6. Enter `_rebuild_backend_for_phase` and prove a captured guard runs before the rebuilt backend turn.
7. Exercise unmanaged initial and retry dispatch through the real `_dispatch` path.
8. Add deny sentinels proving health serialization performs no filesystem, Git, registry, route, tracker, clock, network, Jira, or backend I/O.

## Strengthen where practical

- Cover `OWNED_ERROR` terminal disposition.
- Prove terminal guards use the entry's captured manager even after Core's current manager changes.
- Assert owned failures do not mutate tracker state or enter generic retry paths.

## Accepted evidence

- Existing terminal-path tests exercise real exit, reconcile, and startup paths.
- Those tests prove guard-first behavior, `authorization=None`, and preservation of cleanup flags.
- Accepted RED reruns used deny sentinels and made zero successful external mutations.
