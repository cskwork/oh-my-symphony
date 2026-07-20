# Frontier 002 iteration 2 - routing cohesion review

Date: 2026-07-20
Mode: architecture-only, read-only product/test review. No product, test, Frontier plan/state, Git, network, or AIDT
repository mutation was performed.

## Decision

Replace the untracked flat `src/symphony/aidt_routing.py` prototype with a feature package whose
`src/symphony/aidt_routing/__init__.py` preserves the public import `symphony.aidt_routing`. Split by the five
independent reasons the code changes:

1. closed routing contract/configuration;
2. immutable Git-object observation;
3. pure source-evidence scoring and route decisions;
4. AIDT-owned file-board batch persistence;
5. one-pass runtime orchestration.

The AIDT batch persistence belongs in a sibling tracker adapter,
`src/symphony/trackers/aidt_routes.py`, not in the generic `FileBoardTracker` implementation. Keep the Jira DTO,
Jira source snapshot, and orchestrator hook in their existing modules; those changes already match each file's
purpose.

This is the smallest sustainable split. It adds boundaries, not behavior or generic abstractions. It also isolates
the iteration-2 immutable-object rewrite from config/scoring/card ownership and lets reviewers verify each trust
boundary independently.

The approved plan currently freezes exactly six product/test files and requires renewed Plan Approval for any other
product/test path (`frontier/002-aidt-routing-contract/PLAN.md:194-205`). Therefore this layout is a plan amendment,
not permission to implement outside the frozen scope.

## Theory and current evidence

The real activity has three trust transitions:

```text
workflow catalog -> immutable repository observation -> structured Jira evidence
                                                       |
                                                       v
                                      semantic route decision (pure)
                                                       |
                                                       v
                                  owned file-board batch (side effect)
                                                       |
                                                       v
                                      orchestrator dispatch barrier
```

These transitions should not share one implementation file because they have different inputs, failure modes, and
verification methods:

- The prototype stores config limits/types and closed-schema parsing in one contiguous region
  (`src/symphony/aidt_routing.py:32-350`).
- It then changes to filesystem/Git process and repository observation concerns
  (`src/symphony/aidt_routing.py:361-548`). That code is explicitly obsolete: it reads working-tree anchors,
  resolves `HEAD`, requires a clean checkout, and checks `ls-files`
  (`src/symphony/aidt_routing.py:389-485`). The iteration-2 trust decision requires fixed
  `refs/remotes/origin/aidt-prd` tree/blob reads instead
  (`exploration/routing-git-object-trust.md:44-48,121-138`).
- The same file then validates structured source and computes evidence/scores
  (`src/symphony/aidt_routing.py:551-702`), projects coordinator/child metadata
  (`src/symphony/aidt_routing.py:705-815`), scans/applies cards, and converts errors into runtime results
  (`src/symphony/aidt_routing.py:818-894`).
- The flat module imports Jira intake, generic issues, file persistence, and workflow configuration directly
  (`src/symphony/aidt_routing.py:18-29`), confirming that it currently spans transport input, domain decision,
  persistence, and runtime layers.
- `FileBoardTracker` now embeds AIDT marker ownership, coordinator/child validation, planning, and multi-file commit
  policy (`src/symphony/trackers/file.py:774-980`). Those approximately 207 method lines, plus AIDT constants/types
  near `src/symphony/trackers/file.py:103-202`, are a feature-specific concern inside a generic tracker.
- The test prototype has shared builders at `tests/test_aidt_routing.py:45-235`, decision cases at
  `tests/test_aidt_routing.py:238-397,457-557`, persistence cases at
  `tests/test_aidt_routing.py:560-599`, and orchestrator/health cases at
  `tests/test_aidt_routing.py:398-435,602-633,672-680`. One 693-line file cannot make a failing boundary obvious.
- Ruff currently reports 109 `E701`/`E702` errors, primarily from compressed semicolon/colon statements in that
  test file. This is a readability failure, not an exception to the repository gate
  (`pyproject.toml:45-56`).
- `orchestrator/core.py` is still unmodified in the current partial worktree. The earlier trust review observed the
  same five materialized implementation files and absent core integration
  (`exploration/routing-git-object-trust.md:44-46`).

Package facades are an established compatibility pattern here. `symphony.orchestrator` documents that its package
re-exports preserve the former flat-module dotted surface (`src/symphony/orchestrator/__init__.py:1-18,29-55`), and
`symphony.workflow` does the same (`src/symphony/workflow/__init__.py:1-14,62-98`). A routing package is therefore a
style match, not a new repository convention.

## Layout options

| Option | Shape | Advantages | Costs / decision |
|---|---|---|---|
| A. Keep the flat module and large test | One `aidt_routing.py`, AIDT methods remain in `trackers/file.py`, one test file | Honors the current six-path freeze; least file movement | Reject. The 894-line module has four layer directions already, the Git trust rewrite will enlarge its most security-sensitive region, `file.py` remains mixed, and tests remain an unreadable cross-layer suite. |
| B. Keep a flat facade plus private top-level siblings | `aidt_routing.py` re-exports `_aidt_config.py`, `_aidt_git.py`, `_aidt_decision.py`, etc. | Preserves the exact module file; migration can be mechanical | Acceptable fallback, not recommended. It pollutes the top-level namespace, hides the feature boundary, and does not follow the repository's package-facade precedent. |
| C. Feature package plus tracker adapter | `aidt_routing/{__init__,contract,git_objects,decision,runtime}.py` plus `trackers/aidt_routes.py` | Clear dependency direction; preserves public import; isolates Git and persistence trust; package-local internals; smallest split with one reason to change per file | Recommend. Requires an explicit Frontier scope amendment and test-path updates. |

Do not create both `src/symphony/aidt_routing.py` and `src/symphony/aidt_routing/`; the flat prototype should be
replaced by the package in one implementation patch. A compatibility shim is unnecessary because the import name is
the same, and coexisting module/package paths make import ownership ambiguous to readers and tools.

## Recommended product boundaries

```text
src/symphony/aidt_routing/
  __init__.py       stable public facade only
  contract.py       closed catalog contract and shared routing outcomes
  git_objects.py    fixed-ref repository/blob trust
  decision.py       pure structured-source evaluation and route projection
  runtime.py        one routing pass and fail-closed result mapping
src/symphony/trackers/
  aidt_routes.py    AIDT-owned file-board batch adapter
  file.py           generic file tracker + Jira source refresh only
```

### `aidt_routing/__init__.py` - public compatibility surface

Re-export only the existing supported names:

- `AidtRoutingFailure`, `AidtRoutingResult`;
- `MAX_SERVICES`, `MAX_ALIASES_PER_SERVICE`, `MAX_ANCHORS_PER_CATEGORY`, `MAX_EVIDENCE_RECORDS`,
  `MAX_VALUE_BYTES`;
- `canonical_fingerprint`, `load_routing_settings`;
- `filter_routing_candidates`, `run_aidt_routing`.

Declare these in `__all__`. Do not re-export Git parsers, repository observations, candidates, persistence plans, or
test hooks. `import symphony.aidt_routing as routing_module` and
`from symphony.aidt_routing import run_aidt_routing` must remain valid. Internal tests should import the owning
submodule when they intentionally test a private trust parser.

### `aidt_routing/contract.py` - closed contract/configuration

Own:

- limits and lexical patterns;
- sanitized `AidtRoutingFailure` and immutable `AidtRoutingResult`;
- anchor/service/settings value objects;
- canonical JSON hashing;
- recursive closed-schema parsing, catalog collision checks, and catalog revision calculation.

Primary API:

```python
def load_routing_settings(config: ServiceConfig) -> RoutingSettings | None: ...
def canonical_fingerprint(schema: str, value: object) -> str: ...
```

No filesystem access, subprocess, card parsing, scoring, or tracker imports. Disabled/absent configuration must
return before constructing downstream collaborators, preserving the current early return at
`src/symphony/aidt_routing.py:320-351,873-878`.

### `aidt_routing/git_objects.py` - immutable repository observation

Own:

- the non-configurable `_AIDT_BASE_REF = "refs/remotes/origin/aidt-prd"` and trust-schema tag;
- binary-safe, fixed-argv Git execution and sanitized environment;
- root/checkout/`.git`/Git-dir/common-dir identity capture;
- exact scalar decoding, `ls-tree -z` record parsing, regular-blob mode/type/path/OID validation, and bounded strict
  UTF-8 blob decoding;
- catalog observation and identity/ref/object recheck.

Primary internal API:

```python
GitRunner = Callable[[tuple[str, ...], Mapping[str, str], float], bytes]

def observe_catalog(
    settings: RoutingSettings,
    *,
    git_runner: GitRunner,
    identity_probe: IdentityProbe,
) -> CatalogObservation: ...

def recheck_catalog(
    observation: CatalogObservation,
    *,
    git_runner: GitRunner,
    identity_probe: IdentityProbe,
) -> None: ...
```

`CatalogObservation` exposes only canonical service metadata, fixed ref/commit, trusted decoded scoring contents,
and opaque repository identity tokens. It never exposes raw stderr, paths in health data, or a working-tree file
handle. Marker/anchor working-tree paths are never opened. Each function stays below 50 lines by separating scalar
decode, tree-entry parse, blob read, repository identity capture, and recheck.

### `aidt_routing/decision.py` - pure decision engine

Own:

- exact structured Jira source validation;
- component/context/code/domain/parent/support evidence extraction;
- per-category deduplication, score, conflict, tie, threshold, and explicit multi-route rules;
- semantic/fingerprint calculation, stable decision time, coordinator/child/stale route projections;
- immutable `RouteResolution` output, including desired and retained child intent.

Primary API:

```python
def resolve_card(
    frontmatter: Mapping[str, object],
    settings: RoutingSettings,
    catalog: CatalogObservation,
    *,
    now: Callable[[], datetime],
) -> RouteResolution: ...
```

No `Path`, `subprocess`, board glob, lock, rename, or orchestrator import. This makes A20-1188, hostile-text,
deduplication, conflict/tie, and fingerprint tests deterministic without constructing Git repositories or file
boards. The output describes desired semantics; it does not write cards.

### `trackers/aidt_routes.py` - feature-specific file persistence adapter

Move the AIDT constants, marker parser, route mutation/plan/result dataclasses, board/ownership validation, child
creation, CAS preflight, sorted locks, child-first atomic renames, partial-apply classification, and repair-safe
commit loop out of `trackers/file.py`.

Primary API:

```python
def apply_route_resolution(
    board: FileBoardTracker,
    resolution: RouteResolution,
    *,
    precommit_hook: Callable[[], None] | None = None,
    rename_fault_hook: Callable[[str, int], None] | None = None,
) -> AidtRouteBatchResult: ...
```

Dependency direction is `aidt_routes -> trackers.file`; `trackers.file` must not import the AIDT adapter. Keeping
the free function avoids a pass-through method on `FileBoardTracker` and leaves the generic tracker unaware of the
feature. Local use of sibling tracker primitives (ticket parsing, atomic write, lock path/CAS helpers) is explicit
and contained in this adapter; do not invent a generic transaction framework for one feature.

After extraction, `trackers/file.py` retains only the source-related additions needed by Jira intake:

- canonical ordering for the opaque `routing` frontmatter key;
- `ExternalSourceUpdate.source` and source-owned refresh logic.

### `aidt_routing/runtime.py` - application orchestration

Own only the pass sequence:

1. load/early-return settings;
2. construct the board only when enabled;
3. validate source-mode/intake coupling;
4. scan managed Jira cards;
5. observe catalog once;
6. resolve each card;
7. recheck catalog inside storage precommit and apply the resolution;
8. accumulate blocked IDs/counts and map exceptions to allowlisted results.

Primary public API remains:

```python
def run_aidt_routing(
    config: ServiceConfig,
    *,
    intake_result: JiraIntakeResult | None = None,
    board_factory: BoardFactory | None = None,
    git_runner: GitRunner | None = None,
    identity_probe: IdentityProbe | None = None,
    now: Callable[[], datetime] | None = None,
    precommit_hook: Callable[[], None] | None = None,
    rename_fault_hook: Callable[[str, int], None] | None = None,
) -> AidtRoutingResult: ...
```

`filter_routing_candidates` stays here because it is the runtime handoff to the orchestrator. The clock injection is
required by the approved plan; the current `_decision_time` directly calls wall time
(`src/symphony/aidt_routing.py:711-716`) and should not cross the new boundary unchanged.

### Existing modules that should not be split in this frontier

- `trackers/jira.py`: keep the bounded `JiraInboxIssue` fields and Jira payload normalization together. The new
  components/named-field/timestamp helpers remain part of the Jira intake adapter
  (`src/symphony/trackers/jira.py:73-89,180-252`).
- `jira_intake.py`: keep source snapshot hashing/render/update creation together; it is the source-owned sync boundary
  (`src/symphony/jira_intake.py:146-185,188-238`). Do not make it depend on routing internals merely to reuse a
  one-function hash helper.
- `orchestrator/core.py`: add only the planned default-off poll/health/dispatch-barrier seam. The route hook position
  is already identified after intake and before candidate fetch (`exploration/routing-symphony.md:23-24`).

## Dependency rules

The package must remain acyclic:

```text
contract
  ^       ^
  |       |
git_objects   decision
      ^         ^
      |         |
      +-- runtime --+--> trackers.aidt_routes --> trackers.file
                    |
                    +--> jira_intake (result contract only)

__init__ --> contract + runtime (re-export only)
orchestrator.core --> aidt_routing public facade
```

Binding rules:

- `contract.py` imports only standard library plus `ServiceConfig`.
- `git_objects.py` imports `contract.py`; it never imports decision/storage/runtime.
- `decision.py` imports contract types and read-only catalog observation types; it never imports tracker or Jira
  transport clients.
- `trackers/aidt_routes.py` imports generic file-tracker primitives and the pure `RouteResolution`; it never imports
  runtime/orchestrator.
- `runtime.py` is the only composition root for the four collaborators.
- `jira_intake.py` owns the source revision schema. Routing validates that schema but must not own or regenerate it.
- `orchestrator/core.py` consumes only facade APIs/results. It must not reach into `git_objects`, `decision`, or
  tracker adapter internals.

## Migration from the current partial diff

Perform this as one behavior-preserving architecture step followed by the iteration-2 trust correction. Do not copy
the obsolete `HEAD`/working-tree reader and then polish it.

1. **Return to Plan Approval.** Replace the exact-six-file clause and verification paths with the exact product/test
   list in this report. The plan itself says another product/test file requires re-approval
   (`frontier/002-aidt-routing-contract/PLAN.md:196-205`). Keep the default-off, no-network, no-worktree scope
   unchanged.
2. **Create the facade package.** Add `aidt_routing/__init__.py` and `contract.py`; move the current limits,
   dataclasses, fingerprint, parser, and candidate-filter result surface. Add an import-compatibility test before
   moving behavior.
3. **Implement `git_objects.py` directly against the revised trust contract.** Reuse only the injectable runner and
   identity-probe concepts. Do not migrate `_read_anchor`, clean-status checks, `HEAD`, or `ls-files` from
   `src/symphony/aidt_routing.py:389-485`. The trusted iteration-2 command reads fixed-ref tree entries/blobs
   (`frontier/002-aidt-routing-contract/R-LOOP.md:7-12`).
4. **Move pure resolution.** Extract source validation, evidence/scoring, conflict/passing rules, canonical route
   payloads, and child/retained intent into `decision.py`. Replace direct wall-clock access with injected `now`.
   Prove decision tests without filesystem/Git fixtures.
5. **Extract persistence before editing it.** Move AIDT-specific additions from `trackers/file.py:103-202,774-980`
   to `trackers/aidt_routes.py`. Keep the existing child-first/coordinator-last order and fault hooks. Run storage
   tests before and after the move to distinguish relocation defects from trust-model defects.
6. **Compose in `runtime.py`.** Move managed-card scanning, blocked-ID accumulation, one-pass iteration, sanitized
   exception mapping, and candidate filtering. Its precommit closure must call `recheck_catalog`, then the tracker
   adapter must repeat board/source ownership preflight before the first rename.
7. **Replace the prototype atomically.** Remove the untracked flat `src/symphony/aidt_routing.py` when the package
   is ready. Never leave the file and directory with the same import basename together.
8. **Keep existing narrow diffs.** Retain the already cohesive Jira normalization (`trackers/jira.py`), structured
   source snapshot (`jira_intake.py`), and external-source refresh (`trackers/file.py`). Do not reformat unrelated
   code in those files.
9. **Add core integration last.** `orchestrator/core.py` is not yet modified; wire the facade only after contract,
   Git-object, decision, and storage tests are green. This minimizes the interval in which a half-built route hook
   can affect tick behavior.
10. **Ruff-normalize tests while splitting.** Expand every semicolon/inline suite into ordinary statements. Treat
    this as required readability work inside the moved test lines, not repository-wide formatting.

Migration must preserve public behavior, not private symbol locations. Current public imports at
`tests/test_aidt_routing.py:15-27` remain unchanged. Tests for binary parser internals should target
`symphony.aidt_routing.git_objects`; tests should not force private Git helpers into the facade.

## Test organization

Replace the single `tests/test_aidt_routing.py` with one support module and five collected modules:

```text
tests/
  aidt_routing_support.py
  test_aidt_routing_contract.py
  test_aidt_routing_git_objects.py
  test_aidt_routing_decision.py
  test_aidt_routing_storage.py
  test_aidt_routing_runtime.py
```

### `tests/aidt_routing_support.py`

Own only reusable builders: `ServiceConfig`/catalog/source factories, temporary Git repository/base-ref fixture,
file-board card builder, and fake workflow state. Move the current helpers from
`tests/test_aidt_routing.py:45-235,683-693`, but change `_repo` so it freezes a local
`refs/remotes/origin/aidt-prd` commit. No assertions and no `test_` functions.

### `tests/test_aidt_routing_contract.py`

Own disabled/absent behavior, closed keys/types, Unicode/case/checkout collisions, catalog ordering/fingerprint,
branch-prefix contract, and every named boundary/boundary+1 cap. Migrate the current cases around
`tests/test_aidt_routing.py:398-407,438-456,549-557,636-669`.

### `tests/test_aidt_routing_git_objects.py`

Own only repository/object trust:

- fixed ref versus unrelated `HEAD`;
- binary runner argv/environment/output bounds;
- exact scalar and `ls-tree -z` parsing;
- regular blob mode/type/path/OID and strict UTF-8;
- dirty staged/unstaged/untracked/ignored working-tree neutrality;
- root/checkout/Git metadata symlinks and identity drift;
- committed non-blob anchors;
- base-ref/object drift and precommit recheck.

Replace, rather than carry forward, the old tests at `tests/test_aidt_routing.py:468-513`. Their current names and
fake runner encode the rejected clean-`HEAD` contract.

### `tests/test_aidt_routing_decision.py`

Own A20-1188 at 95, authoritative-category deduplication, keyword/parent-only review, component-code conflict,
multi-route explicit anchors, hostile body-marker isolation, supporting-only consumers, source/fingerprint changes,
and stable semantic decisions. Migrate the pure behavior currently spread across
`tests/test_aidt_routing.py:238-295,315-324,428-465,515-557` and replace filesystem setup with a trusted catalog
observation fixture/value where the repository mechanics are not under test.

### `tests/test_aidt_routing_storage.py`

Own coordinator/child ownership, unmanaged/case/path/reparent collisions, source drift, sorted locks, late collision,
child-first/coordinator-last ordering, zero-write cooperative failures, partial apply, next-poll repair, retained
stale children, local-state/body/frontmatter preservation, and byte/mtime stability. Migrate
`tests/test_aidt_routing.py:326-397,560-599`.

### `tests/test_aidt_routing_runtime.py`

Own facade imports, disabled collaborator construction, source-mode/intake coupling, sanitized global failure,
blocked-candidate ordering, orchestrator health, same-tick Jira failure, reload failure, and candidate-fetch barrier.
Migrate `tests/test_aidt_routing.py:398-435,602-633,672-680`. Mock collaborators at the consumer reference in
`aidt_routing.runtime` or `orchestrator.core`, matching the repository's documented monkeypatch rule
(`src/symphony/orchestrator/__init__.py:12-18`).

This split is behavioral, not one-test-per-file. Each collected module answers one failure question: contract, Git
trust, decision, storage, or tick integration.

## Verification impact

### Changed gates

- The plan's focused pytest command must list the five collected test files (or use a stable
  `tests/test_aidt_routing_*.py` glob through the shell used by CI). Explicit paths are safer in the recorded
  Frontier command.
- Ruff must include `src/symphony/aidt_routing/`, `src/symphony/trackers/aidt_routes.py`, and all five collected test
  files plus the support module. The present baseline is not green: read-only Ruff returned 109 errors.
- Pyright should target the package directory and tracker adapter as well as the four existing changed modules.
  Tests remain outside the configured Pyright include (`pyproject.toml:58-63`) unless this Frontier deliberately
  changes that repository policy; do not widen that policy here.
- Add a focused import-surface assertion proving all names listed under `__all__` resolve from
  `symphony.aidt_routing`. The repository's setuptools package discovery already finds packages below `src`
  (`pyproject.toml:39-40`), so no packaging configuration change should be required.
- `git diff --check`, affected Jira/file/orchestrator regressions, repository-wide pytest parity, and `symphony
  doctor` remain unchanged. The approved command list lives at
  `frontier/002-aidt-routing-contract/PLAN.md:268-281` and must be path-amended, not weakened.

### Recommended verification order

1. `test_aidt_routing_contract.py` and public import-surface test.
2. `test_aidt_routing_git_objects.py` (all hostile binary/ref/path cases).
3. `test_aidt_routing_decision.py` (pure, no I/O).
4. `test_aidt_routing_storage.py` (fault injection and idempotence).
5. `test_aidt_routing_runtime.py` plus Jira/file/orchestrator affected regressions.
6. Ruff over every changed product/test/support path; require zero findings.
7. Pyright over `src/symphony/aidt_routing`, tracker adapter, and existing touched product files.
8. Full pytest parity, `git diff --check`, and doctor.

This order localizes failures. A malformed `ls-tree -z` record should fail the Git-object suite, not appear first as
an orchestrator health assertion; a late child collision should fail storage, not look like a score defect.

## Surgical scope and risks

- **Scope amendment required:** recommended product/test paths exceed the current six-file freeze. Implementing the
  split without updating Plan Approval would violate the plan even though runtime behavior is unchanged.
- **No configuration migration:** facade/package movement does not change `ServiceConfig.raw["aidt_routing"]`.
- **No persisted-route migration beyond the already required trust-schema bump:** the package split is internal;
  the fixed-ref object trust change already requires stale recomputation.
- **No generic tracker framework:** extracting one AIDT adapter is sufficient. A reusable transaction engine would
  be speculative and increase blast radius.
- **No Jira module decomposition:** their new responsibilities remain transport/source cohesive; splitting them
  would be file-count churn without an independent change driver.
- **No private facade compatibility:** only the named public surface is stable. Preserving `_read_anchor`,
  `_git_revision`, or old monkeypatch locations would preserve the rejected trust design.
- **Cycle risk:** prevent `decision -> storage -> runtime -> decision` by keeping `RouteResolution` in `decision.py`
  and making storage consume it. Runtime alone composes both.
- **Test-helper risk:** support helpers must not hide assertions or silently create a clean current branch. The base
  ref and dirty working-tree state must be explicit fixture operations.

## Exploration record

- Classification: feature/refactor fit; no database/schema or live runtime mutation applies.
- Fresh-context architecture agent: read repository `AGENTS.md`, Frontier 002 `GOAL.md`, `PLAN.md`, `R-LOOP.md`,
  `routing-git-object-trust.md`, `routing-symphony.md`, the current planned six paths, package facade precedents,
  Pytest/Ruff/Pyright configuration, and the AIDT-specific file-tracker region.
- Hypothesis A, "the six-file monolith is acceptable because functions are short": rejected. File purpose and
  dependency direction still mix four layers (`aidt_routing.py:18-29,32-350,361-548,551-815,818-894`).
- Hypothesis B, "flat private siblings are the least risky compatibility route": retained only as fallback. The
  repository already proves package re-export compatibility, so the package is clearer at the same runtime surface.
- Hypothesis C, "extract a generic tracker transaction abstraction": rejected. Only AIDT needs the specialized
  coordinator/child ownership and partial-apply semantics in this Frontier.
- Final: package facade + four cohesive routing modules + one tracker adapter. This is the minimum layout that
  separates configuration, immutable Git trust, pure decision, persistence, and runtime composition.

Read-only checks performed: current six-path status/line counts, tracked diff, symbol/caller searches, Ruff, and
`git diff --check`. Ruff failed with 109 test style errors; `git diff --check` passed. A nested exploration-agent slot
was unavailable, but this report itself was produced as the main thread's delegated fresh-context architecture
review.
