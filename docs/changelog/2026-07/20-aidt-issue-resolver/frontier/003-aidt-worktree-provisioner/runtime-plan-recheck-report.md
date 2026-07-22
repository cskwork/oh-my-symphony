# Frontier 003 runtime plan final recheck

Date: 2026-07-21
Scope: Binding Amendment 4, runtime implementation map, current runtime RED contract, and landed public dependency APIs
Verdict: **PASS / APPROVE BUILD against the current intentional RED contract**

## Decision

All ten runtime-plan attack MUST findings (M1-M10) and both SHOULD findings (S1-S2) are now binding,
executable, and implementable through the landed public facade, contract, manifest, provisioner, and routing APIs.
No runtime requirement now depends on a private Git-state parser, copied provisioner helper, unsafe unlocked durable
transition, live repository, network, Core/workspace edit, or widened public constructor.

The focused suite remains intentionally RED because `src/symphony/aidt_worktree/runtime.py` and the three facade
exports do not exist yet. That is the expected Build entry state, not a plan/test defect: collection succeeds, the
first test fails on the absent facade exports, and the other seven fail through the bounded absent-runtime helper.

## Findings

### Required

None.

### Recommended

None before Build. The builder should implement only the authorized runtime module and three lazy facade exports;
workspace/Core integration and orphan registration-only observation remain deferred.

## M1-M10 and S1-S2 closure

| ID | Result | Binding and executable evidence |
|---|---|---|
| M1 lazy default | PASS | Amendment 4 fixes `provisioner_factory=None` and exact lazy resolution/call. The subprocess contract constructs and disabled-publishes through the facade while runtime dependencies and metadata remain absent (`PLAN.md:565-568`; `tests/test_aidt_worktree_runtime.py:333-379`). |
| M2 disabled durable path | PASS | `path_for` alone may return an exactly validated recorded path; missing/corrupt/partial evidence is owned error and mutating gates remain closed (`PLAN.md:569-574`; test at `:1142-1175`). |
| M3 managed/unmanaged distinction | PASS | `LOCAL-1` is the zero-loader non-child control; a canonical route-managed child stays owned after disable; a separate canonical child becomes `UNMANAGED` only after one loader call returns `None` (`PLAN.md:572-574`; test at `:1252-1258`). |
| M4 ready evidence | PASS | Missing manifest and mismatched owner persist manual `registry_invalid`, never call prepare, and exercise one explicit pre-lock optional-attempt observation followed by exact manifest-lock-bounded manifest/owner/attempt rereads and persistence (`PLAN.md:575-578`; lock probe/test at `:681-774`). |
| M5 atomic publication | PASS | Factory and activation failures leave the current DTO/provisioner usable, do not count inside `publish`, and count once only through bounded rejection (`PLAN.md:579-583`; test helpers at `:835-886`). |
| M6 fatal sources | PASS | Activation, initial write, consume, scope reset, provisioner `persistence_failed`, naive clock, and aware non-UTC `+09:00` clock each latch exactly once; changed/disabled publication cannot reopen the circuit (`PLAN.md:591-594`; tests at `:900-1015`, `:1289-1303`). |
| M7 counter ordering | PASS | A `provision` prepare with `created_now=False` increments create before the stale publication postcheck, and the resulting `scope_changed` separately increments failure (`PLAN.md:584-586`; test at `:814-824`). |
| M8 exception table | PASS | Actual `AidtRoutingFailure` maps to `card_invalid`; loader `None`, bounded worktree category/ref, generic prepare/attest/cleanup errors, and publication-scoped factory failure have executable assertions (`PLAN.md:587-590`; test at `:1245-1287`). |
| M9 health/structure | PASS | Health is called twice with clock, route, registry/manifest/filesystem, and all provisioner methods guarded; call lists are unchanged. Static gates exclude tracker/network dependencies and enforce product functions at <=50 lines/nesting <=4 (`PLAN.md:591-594`; tests at `:488-514`, `:1018-1039`). |
| M10 no private registration parser | PASS | Registration-only ownership is explicitly deferred. The AST gate rejects direct, relative, facade-export, constant, and dynamic Git-state/parser access, including public facade registration-parser exports (`PLAN.md:595-598`; tests at `:423-514`). |
| S1 immutable publication identity | PASS | Binding corrections require a private immutable key over consumed validated fields; mutating the published shallow `ServiceConfig.raw` cannot change equality with a fresh equivalent config (`PLAN.md:599-602`; map `:404-409`; test `:1112-1114`). |
| S2 concurrent initializer | PASS | The map now specifies a non-authorizing pre-lock optional read, exact manifest-lock expected-none CAS persist, no same-call retry for the loser, lock release before public `admit_attempt`, and a real two-thread barrier proving one revision-2 admission/one owned `cas_mismatch` (`PLAN.md:603-604`; map `:242-263`; test `:777-812`). |

## Public API feasibility audit

- Facade: the current closed lazy sets and `__getattr__` provide the established pattern; the three runtime exports are
  intentionally absent now (`src/symphony/aidt_worktree/__init__.py:213-225,336-347`).
- Contract: `AidtWorktreeFailure` sanitizes allowlisted category/ref, while sealed `DelegateResult` permits only exact
  `UNMANAGED`, `HANDLED`, `OWNED_PRESERVED`, or `OWNED_ERROR`
  (`src/symphony/aidt_worktree/contract.py:81-88,142-176`).
- Manifest: optional readers return `None` only for exact absence; `persist_attempt` exposes revision CAS; public
  `advisory_lock` and `admit_attempt` provide the required lock/consume protocol
  (`src/symphony/aidt_worktree/manifest.py:462-530,614-695`).
- Provisioner: the frozen admission/guard DTOs and public `prepare`, `attest_before_run`, and `cleanup` methods are the
  complete runtime delegation boundary; the constructor accepts the exact keyword-only clock call
  (`src/symphony/aidt_worktree/provisioner.py:98-160,218-308`).
- Routing: production dispatch raises the actual sanitized `AidtRoutingFailure`, and the public loader returns either
  a contract or exact `None` (`src/symphony/aidt_routing/contract.py:102-113`;
  `src/symphony/aidt_routing/dispatch.py:130-150`).

The corrected initializer protocol uses public operations only:

1. Observe `read_optional_attempt` before locking; this authorizes no work.
2. On absence, acquire the exact public manifest lock and call `persist_attempt(..., expected_revision=None)` without
   re-reading under that creation lock.
3. Return an expected-none CAS loser as owned `cas_mismatch`, with no same-call retry.
4. Release the creation lock before calling public `admit_attempt`, which acquires that lock itself.

Ready admission separately revalidates and persists its negative disposition while the same exact manifest lock is
active. No private CAS, Git registration parser, or provisioner validation helper is prescribed.

## Fresh verification evidence

All commands ran in the assigned isolated worktree with `PYTHONDONTWRITEBYTECODE=1` where applicable.

| Gate | Exit | Fresh result |
|---|---:|---|
| Runtime collection | 0 | `8 tests collected in 0.30s` |
| Focused expected RED | 1 | `8 failed in 0.71s`; one missing-facade failure and seven bounded absent-runtime failures |
| Unrelated accepted baseline | 0 | `124 passed in 2.12s` |
| Ruff | 0 | `All checks passed!` |
| Pyright | 0 | `0 errors, 0 warnings, 0 informations` |
| Test AST | 0 | `73` functions; max `47` lines; max nesting `2`; no violations |
| Import-boundary synthetic check | 0 | `runtime_import_boundary_ok` for direct Git-state, facade parser, unknown dynamic import, socket, and tracker rejection; allowed public contract/manifest/provisioner imports pass |
| No-index whitespace: runtime test | 1 | Expected content-difference exit; no output, therefore no whitespace error |
| No-index whitespace: RED report | 1 | Expected content-difference exit; no output, therefore no whitespace error |
| Tracked diff whitespace | 0 | `git diff --check` produced no findings |

Focused failure classification is exact and bounded:

- `test_never_enabled_unmanaged_runtime_is_inert`: facade `__all__` lacks the three not-yet-built runtime exports.
- Remaining seven tests: `No module named 'symphony.aidt_worktree.runtime'`, converted by the test-local helper to
  `runtime module is intentionally absent in the RED slice`.

## Safety and scope

This recheck did not modify product code, tests, facade, controlling plans, manifest/provisioner/routing code, Core,
workspace code, or Git state. It used no network, live repository, Jira, backend, AIDT checkout, commit, or other Git
mutation. The only recheck write is this report.

## Final verdict

**PASS / APPROVE BUILD.** The runtime builder can now turn the eight intentional RED tests GREEN using only the
authorized public seams and files. There are no required or recommended plan/test corrections remaining.
