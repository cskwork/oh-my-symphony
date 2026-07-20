# Plan Attack - Frontier 002a Routing Result Output Closure

## Fresh attack findings

### MUST resolve before Build

1. **Bind the blocked-identifier byte boundary exactly.** Frozen Contract 2 says coordinator and child IDs must be
   within the “existing byte limits,” but the two existing limits mean different things: `MAX_ID_BYTES == 48` is
   currently enforced only for service IDs, while `MAX_VALUE_BYTES == 256` is the generic text/path/literal cap.
   `_CARD_KEY`, `_valid_card_ref`, and the current runtime coordinator/child predicates do not bound Jira-key bytes.
   State explicitly whether the coordinator segment, each segment, and/or the full child is capped, then name
   boundary and boundary-plus-one cases. Without that decision, two compliant implementations can accept different
   public IDs and “valid boundary values remain unchanged” is not testable.

2. **Make exact status and failure-constructor behavior explicit.** GOAL requires an exact valid status, but Frozen
   Contract 4 only says status is in the allowlist; bind `type(status) is str` so `str`/enum subclasses cannot cross
   the public boundary. Likewise replace “`AidtRoutingFailure` rejects ... safely” with the exact result: a malformed
   or unhashable category becomes `internal_error`, and every malformed/non-exact-string identifier becomes `None`.
   Exact type gates must precede frozenset membership, regex/string calls, and category membership so list/dict/set
   payloads cannot raise from either public constructor.

3. **Bind invalid category/ref handling to whole-result normalization.** “Before existing allowlisting” is not
   sufficient: the current implementation repairs status/category/ref independently. For `AidtRoutingResult`, an
   unknown category, a ref with no category, a ref forbidden for its category, or a malformed canonical ref must
   invalidate the complete instance; none may be silently dropped while the injected dispatch/count fields survive.
   A valid pair is exactly `(None, None)`, an allowlisted category plus `None`, or a category-specific canonical
   service/card ref which the allowlist returns unchanged. This is deliberately stricter than
   `AidtRoutingFailure`, whose exception-boundary contract sanitizes malformed inputs to `internal_error`/`None`.

4. **Repair the existing runtime-test result builder before green evidence.** `_routing_result(status="review")`
   currently computes `child_count == -1` when `blocked` is omitted. The new constructor must normalize that fixture
   to failure, so `test_health_transitions_are_exact_and_failure_retains_last_success` would stop testing the intended
   review transition. Change the test-only helper to produce a non-negative explicit child count (zero is sufficient)
   rather than weakening the constructor or treating the resulting failure as a product regression.

Recommended exact resolution for finding 1: cap the complete blocked string at `MAX_VALUE_BYTES == 256`; for a
child, additionally cap and validate the service segment with the existing `MAX_ID_BYTES == 48`. Apply the same
coordinator-key predicate/cap to the key segment of `card:` refs. This gives every public string a finite bound,
preserves the already-frozen 48-byte service grammar, and makes the exact-256/exact-257 blocked-value tests
unambiguous. If a different interpretation is intended, it must be written into `PLAN.md` before Build.

### Exact smallest test matrix

`tests/test_aidt_routing_contract.py`:

- Define one exact canonical failure assertion covering every field: enabled true, dispatch false, exact empty
  `frozenset`, route/review/child zero, failure one, status `failure`, category `internal_error`, ref `None`.
- Preserve valid boundaries: exact booleans; exact zero and named maximum counts; exact `frozenset`; all four statuses;
  empty, coordinator, and child blocked IDs; total blocked cardinality
  `MAX_COORDINATORS + MAX_CHILDREN`; exact string/card/service byte boundaries; every allowed category with `None`,
  plus one allowed canonical service ref and one allowed canonical card ref. Assert fields are unchanged, not merely
  equal after coercion.
- Parameterize malformed scalar fields independently: `1` for each boolean; `True`, a custom `int` subclass, `-1`,
  and named-cap-plus-one for each count; unknown status, list/dict status, and an unhashable `str` subclass. Every row
  must produce the complete canonical failure without raising.
- Parameterize blocked values: ordinary set/list, `frozenset` subclass, combined-cap-plus-one, non-string and `str`
  subclass elements, lowercase/zero-number/path/traversal/control/Unicode card IDs, missing/extra `--`, uppercase or
  empty service, coordinator/service/full-string boundary-plus-one. Every row must normalize the whole result.
- Parameterize category/ref values: unknown and `str` subclass category; list/dict/set category; ref without category;
  ref forbidden for its category; path/case/oversize card or service ref; `str` subclass and list/dict/set ref. Include
  one combined verifier payload so no earlier field repair can mask a later exception.
- Exercise `AidtRoutingFailure` separately with the same non-exact/unhashable category/ref inputs and assert its
  public `category`, `identifier`, `args`, and repr contain only `internal_error`/`None`; retain exact valid refs.
- Assert frozen safety (`FrozenInstanceError` on assignment), hash/repr of normalized hostile input do not invoke or
  expose hostile object repr, and `dataclasses.replace` re-enters the same whole-result invariant.

`tests/test_aidt_routing_runtime.py`:

- Fix `_routing_result` so every pre-existing synthetic result satisfies the new non-negative count invariant.
- Add one actual `_on_tick` regression returning a combined hostile result. In one assertion surface, cover result
  repr, captured `aidt_routing_failure` fields, health snapshot, and the dispatch barrier: candidate fetch and legacy
  normalization are not called; no injected text/path/object repr occurs; health/log counts are `0/0/0/1`, status and
  error are `failure`/`internal_error`, and dispatch is false.
- Add `core-first` to `_IMPORT_ORDER_PRELUDES` (or an equivalent fresh subprocess) so the required four permutations
  are storage-first, package-first, public-runtime-first, and core-first. Preserve facade/runtime callable identity
  and the lazy absence of `aidt_routing.runtime` before first public runtime access where applicable.

### Implementation and scope conclusion

The smallest product implementation remains in `contract.py`: exact-type predicates, one bounded blocked-ID
predicate, one whole-result validity predicate, and one canonical failure setter called by frozen `__post_init__`.
Validate every field first and only then either leave all fields untouched or set every canonical failure field with
`object.__setattr__`; do not instantiate recursively from `__post_init__`. Update `AidtRoutingFailure.__init__` in the
same file with type-before-membership checks.

No consumer product code should change. `orchestrator/core.py` correctly treats the result as trusted and its direct
health/log/dispatch copies become safe once construction is total; `runtime.py` already constructs through the same
boundary; and the lazy facade must remain untouched. The exact approved three-path scope therefore holds:
`contract.py`, `test_aidt_routing_contract.py`, and `test_aidt_routing_runtime.py`. A change to core, product runtime,
the facade, storage, GOAL/PLAN/state, or any unrelated test is not justified by this correction.

### SHOULD

- Keep one shared test helper for the canonical failure tuple so each hostile row proves atomic normalization rather
  than checking only the field it attacked.
- Keep identifier validators private to `contract.py`; exporting new constants/helpers would expand the frozen
  11-name facade without need.
- Retain field-only validity as frozen: do not invent cross-field equations between counts, status, blocked-set size,
  and dispatch in this correction. Runtime producers establish those semantics; this frontier closes exact type,
  grammar, bound, and output safety.

## Final gate

**FAIL before Build.** Findings 1-4 are binding plan/test corrections. Once `PLAN.md` freezes the exact blocked/card
byte cap, exact status type, exact exception sanitization, whole-result category/ref invalidation, and the negative
test-helper repair, the three-path build is sufficient and consumer product code remains unchanged.

## Amendment Recheck

**Verdict: PASS.** The amended `PLAN.md` resolves all four MUST findings and Build may proceed within the exact
three-path scope.

1. **Finding 1 — PASS.** Frozen Contract 2 now binds an exact `frozenset`, the combined coordinator/child cardinality
   cap, exact `_CARD_KEY`/`_SERVICE_ID` grammars, the 256-byte complete blocked-value and coordinator/key cap, the
   additional 48-byte child service cap, and binding 256/257 plus 48/49 cases. It also reuses the bounded key and
   service predicates for `card:` and `service:` refs. The current contract defines both named caps and leaves the
   Jira-key boundary open, so this is an implementable, non-conflicting closure rather than a scope expansion.
2. **Finding 2 — PASS.** Frozen Contract 4 requires exact `str` status/category/ref inputs before membership, regex,
   or allowlist operations. It separately fixes `AidtRoutingFailure`: malformed or unhashable category becomes
   `internal_error`, malformed or non-exact-string identifier becomes `None`, and exact valid pairs remain unchanged.
   The contract test matrix explicitly covers subclasses and unhashable list/dict/set payloads without raising.
3. **Finding 3 — PASS.** Frozen Contract 4 enumerates the only valid result error pairs and states that unknown,
   orphaned, forbidden, malformed, oversize, or non-exact values invalidate the complete result. Frozen Contract 5
   binds the atomic canonical failure value for every field, so independent repair cannot preserve injected dispatch,
   blocked-ID, or count data.
4. **Finding 4 — PASS.** Frozen Contract 7 explicitly requires the runtime-test `_routing_result` helper to emit a
   non-negative child count for review fixtures and forbids weakening product validation. The current helper computes
   `len(blocked) - routed - review`, confirming that `_routing_result(status="review")` with no blocked IDs is the
   exact test-only defect the plan names.

**Scope recheck — PASS.** `contract.py` owns both public constructors and all relevant constants/ref predicates;
`test_aidt_routing_contract.py` owns constructor boundaries; `test_aidt_routing_runtime.py` owns the invalid helper,
fourth import order, and real `_on_tick` repr/log/health/dispatch regression. Current runtime producers already create
results through `AidtRoutingResult`, while `orchestrator/core.py` only consumes its trusted fields. No change to core,
product runtime, facade, storage, GOAL, PLAN, QA, or state is needed. Approved paths remain exactly:
`src/symphony/aidt_routing/contract.py`, `tests/test_aidt_routing_contract.py`, and
`tests/test_aidt_routing_runtime.py`.

**Final gate: PASS for Build.** This is plan approval only; red-green implementation and the full fresh verification
matrix remain pending.
