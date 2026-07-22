# Runtime final re-verification

## Verdict

**APPROVE** — R1–R4 remain repaired and the final removal ownership regression is closed.

## Re-verification result

| Requirement | Result | Evidence |
|---|---|---|
| R1 DTO totality and semantic timestamp validation | PASS | Final DTO validators type-check before field access and strictly parse the timestamp. The corrected hostile-input controls reported in the gap-repair evidence pass. |
| R2 truthful provision/resume counts after post-prepare clock failure | PASS | `_complete_prepare` increments the physical action before the clock is read; both provision and resume controls retain count `1`. Malformed prepared output is bounded as `internal_error`, increments the completed prepare count, and issues no guard. |
| R3 exact runtime-issued capabilities | PASS | Admissions require exact object identity and are consumed once under the lock. Forgery cannot consume the genuine admission. Guards require exact issued identity, remain reusable, rotate on newer same-child issuance, and capability maps clear on material publication, rejection, and fatal failure. |
| R4 constructor purity and stable metadata | PASS | Constructor metadata derivation is lexical: no `Path.resolve` or filesystem observation. Its metadata equals the public stable derivation for canonical workflow input. Lazy/static/function-size controls remain green in the supplied repair evidence. |
| Removal durable-path precedence and bounded cleanup | PASS | The final reverse-ownership repair closes both recognized-other and loader-none identifier/path mismatches as `OWNED_ERROR/path_invalid`, without cleanup. |

## Superseded MUST finding

### M1 — Explicit unknown identifier bypasses durable path ownership

Decisive reproduction:

- Persisted aligned manifest and ownership records for child A: `A20-1188--viewer-api`.
- Called `remove(A_path, identifier="A20-1999--viewer-api")`, where the supplied identifier is canonical but unknown.
- Actual: `UNMANAGED`, category `None`, cleanup calls `0`.
- Control with a recognized different child, `A20-1998--viewer-api`: `OWNED_ERROR/path_invalid`, cleanup calls still `0`.

The zero-cleanup property is correct, but `UNMANAGED` is not. It permits generic fallback to operate on a path the AIDT registry durably owns.

Cause: `runtime.py:_recognize_removal` returns the result of `_recognize_for_delegate` immediately for an explicit identifier. An unknown identifier therefore returns before reverse ownership of the supplied path is checked. Durable-record validation only runs after identifier recognition succeeds.

Required invariant: when the supplied path is durably owned by any child, an unknown or different explicit identifier must return a bounded owned error (`path_invalid` or `registry_invalid`) and must never return `UNMANAGED`; cleanup remains zero.

Smallest repair: check durable reverse path ownership before accepting an explicit-identifier `UNMANAGED` result. If the path is owned by another child, fail closed; only a path with no AIDT ownership evidence may remain unmanaged.

Smallest RED control: persist aligned child-A manifest/ownership, then call removal with (1) an unknown canonical child B and (2) a recognized child B. Assert both are owned errors and cleanup is never called. The current implementation fails case (1).

## SHOULD

No new blocking SHOULD finding. The previously noted mutable raw-config retention remains a non-blocking, out-of-slice hardening recommendation.

## Evidence boundary

This pass read the final verification, RED, and repair reports; inspected final `runtime.py` and the corrected focused tests; and ran only the decisive ownership-boundary reproduction. The repair report's focused runtime, compatibility, Ruff, Pyright, and AST results support the R1–R4 controls. No product or test files were changed.

## Final repair confirmation

**Final verdict: APPROVE. No required finding remains.**

The repair routes only the explicit-identifier `UNMANAGED` branch through `_reverse_unmanaged_removal`. That helper reverse-checks the supplied path against durable ownership: an owned path fails closed as `path_invalid`; a truly unowned path preserves `UNMANAGED`. Recognized identifiers still use exact identifier/path validation.

Independent three-row replay:

| Case | Result | Cleanup |
|---|---|---|
| Durable child-A path + recognized child B | `OWNED_ERROR/path_invalid` | `0` |
| Durable child-A path + loader-none canonical child B | `OWNED_ERROR/path_invalid` | `0` |
| Unowned path + loader-none canonical identifier | `UNMANAGED` | `0` |

Focused verification:

- Exact ownership-containing runtime test: `1 passed in 1.09s`.
- Complete focused runtime file: `8 passed in 0.69s`.
- The eight controls retain the R1 DTO, R2 action accounting, R3 capability lifecycle, R4 constructor/lazy/static, removal-precedence, and bounded-error assertions.
- The repair report additionally records the compatibility controls as `125 passed`, Ruff clean, Pyright clean, and AST limits clean. The previously established provisioner `65` and routing `204` matrices were not repeated for this recognition-only repair.

No network, product/test edit, live system access, Git mutation, or commit was used in this confirmation.
