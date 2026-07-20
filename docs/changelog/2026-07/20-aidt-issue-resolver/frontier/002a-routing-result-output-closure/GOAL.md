# GOAL - Frontier 002a Routing Result Output Closure

Close the public `AidtRoutingResult` value boundary so downstream repr, structured logs, health, and dispatch consume
only exact bounded types and canonical identifiers.

## Success Criteria

- [x] Exact valid boolean, count, status, category/ref, and blocked-ID values remain unchanged.
- [x] Any malformed field normalizes the complete result to enabled failure, dispatch false, empty blocked IDs, zero
  route/review/child counts, failure count one, status failure, `internal_error`, and no ref.
- [x] Canonical coordinator and `<coordinator>--<service-id>` blocked IDs are accepted only within the frozen combined
  coordinator/child cap and exact 256-byte full/48-byte service limits; malformed type/case/path/traversal/oversize/
  card/service values fail closed.
- [x] Exact-string status/category/ref and `AidtRoutingFailure` inputs are total for unhashable/non-string values;
  invalid result error pairs fail the whole result while invalid exception inputs become `internal_error`/no ref.
- [x] Repr, captured structured log, core health, and candidate-dispatch decision never expose injected text/object
  repr and remain fail closed.
- [x] Four fresh import permutations, five isolated routing suites, affected/full parity, Ruff, Pyright, structure,
  diff, and doctor meet Frontier 002's accepted baselines.

## Scope

Product/test paths: `src/symphony/aidt_routing/contract.py`, `tests/test_aidt_routing_contract.py`, and
`tests/test_aidt_routing_runtime.py`. Run-vault evidence may change. No other path may change.
