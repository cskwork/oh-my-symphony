# PLAN - Frontier 002a Routing Result Output Closure

## Approval

- Status: auto-approved under the user's accepted infrastructure plan.
- Source: Frontier 002 iteration-3 verifier report.
- Max Build/Verify iterations: 2.

## Theory

`AidtRoutingResult` is a trust boundary because the orchestrator copies it into dispatch decisions, health, logs, and
repr. Type annotations do not validate runtime values. Sanitizing only status/error fields leaves arbitrary objects
and text able to cross every public surface. The smallest root-cause fix is one closed constructor invariant, not
defensive checks in each consumer.

## Frozen Contract

1. Valid result requires exact `bool` for `enabled` and `global_allow_dispatch`.
2. `blocked_identifiers` is an exact `frozenset` of at most `MAX_COORDINATORS + MAX_CHILDREN` exact strings. Each
   complete string is at most `MAX_VALUE_BYTES == 256` UTF-8 bytes and is either `_CARD_KEY` or
   `_CARD_KEY--_SERVICE_ID` with exactly one separator. The coordinator/key segment uses the same exact `_CARD_KEY`
   grammar and 256-byte cap; a child service segment additionally uses `_SERVICE_ID` and
   `MAX_ID_BYTES == 48`. Exact 256/257 full blocked values and exact 48/49 service values are binding tests. The same
   bounded key predicate validates `card:` refs; `service:` refs use the same 48-byte service predicate.
3. `routed_count` and `review_count` are exact non-boolean integers in `[0, MAX_COORDINATORS]`; `child_count` is in
   `[0, MAX_CHILDREN]`; `failure_count` is in `[0, MAX_COORDINATORS]`.
4. Status must have exact type `str` and be in the existing result allowlist. Category/ref must be exact `str` or
   null before membership, regex, or allowlisting. A valid result error pair is exactly `(None, None)`, an allowlisted
   category plus `None`, or a category-specific canonical ref returned unchanged. Unknown categories, a ref without
   category, a forbidden/malformed/oversize ref, and non-exact/unhashable values invalidate the whole result.
   Separately, `AidtRoutingFailure` maps every malformed/unhashable category to `internal_error` and every malformed/
   non-exact-string identifier to `None` without raising; exact valid category/ref pairs remain unchanged.
5. If any field is malformed, atomically normalize the whole frozen instance to: `enabled=True`, dispatch false,
   empty blocked set, route/review/child zero, failure one, status `failure`, category `internal_error`, ref null.
6. Valid instances are unchanged. Consumers remain unchanged; tests pass both valid boundaries and malformed values
   through repr, routing failure logging, `_apply_aidt_routing_result`, health, and candidate gate.
7. The existing runtime-test `_routing_result` helper must emit a non-negative explicit child count for review
   fixtures. Tests may not weaken product validation to preserve an invalid synthetic result.

## TDD and Verification

1. Reproduce the verifier payload leak in contract/runtime tests before the fix.
2. Add valid boundary/canonical-ID cases and malformed bool/int/subclass/negative/overflow/set/ID/category/ref cases,
   plus `AidtRoutingFailure`, frozen assignment, hash/repr, and `dataclasses.replace` revalidation.
3. Implement cohesive helpers plus fail-closed `__post_init__`, each <=50 lines/nesting <=4.
4. Run isolated contract/runtime/storage/import permutations, all routing/affected/full suites, static/structure/diff,
   and doctor; a fresh verifier repeats all final gates.

The runtime regression returns one combined malformed result through a real `_on_tick`: repr/log/health expose only
the canonical failure, fetch and legacy normalization do not run, and all four fresh import orders remain green.

## Rejected alternatives

- Consumer-by-consumer sanitization: duplicates policy and leaves future surfaces exposed.
- Coercion/clamping or dropping only malformed IDs: can turn corrupt results into partial success or dispatch.
- Logging filters only: health/repr/dispatch remain unsafe.
