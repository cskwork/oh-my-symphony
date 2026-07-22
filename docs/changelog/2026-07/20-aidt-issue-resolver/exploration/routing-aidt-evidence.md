# Frontier 002 — AIDT routing evidence

Date: 2026-07-20
Mode: read-only AIDT inspection; no build, database, network, Git mutation, or product-code edit.

## Decision

Route the A20-1188 backend subtask to canonical service `viewer-api`, checkout
`/Users/chaeseong-gug/Documents/PARA/Project/Git/AIDT/aidt-viewer-api`, branch family
`feat/A20-1188` (base `origin/aidt-prd`). Confidence: **95%**.

Do not create `lms-api` or `lms-web` children. The named endpoint is implemented by
`viewer-api`; its actual browser consumer is `viewer-web`. If the local card also owns the
parent's popup/first-incomplete-module navigation, create a separate `viewer-web` child after
validating that frontend scope. The local parent evidence explicitly assigns that behavior to a
separate frontend owner and calls it a backend non-goal, so it is not part of the A20-1188 backend
route.

The A20-1188 body is empty and there is no `A20-1188` reference in the inspected AIDT source.
Therefore its parent context is required. The current local implementation/evidence is filed under
A20-1186; this issue-number mismatch must be retained in `route_evidence`, not hidden.

## Exact A20 ownership chain

All AIDT paths below are relative to
`/Users/chaeseong-gug/Documents/PARA/Project/Git/AIDT/`.

| Evidence | Exact anchor | Routing effect |
|---|---|---|
| Runtime context | `aidt-viewer-api/src/main/resources/application.yml:24` sets `context-path: /v-api` | The `../v-api/...` prefix maps uniquely to `viewer-api`. |
| Controller route | `aidt-viewer-api/src/main/java/ai/aidt/viewer/api/controller/MathAILearningCenterController.java:34` has `@RequestMapping("/ailearning/")`; line 41 has `@GetMapping("/{aiLrnNo}")`; lines 43-51 define and dispatch `getMathAILearningCenter` | Exact owner of `GET /v-api/ailearning/{learning-space-no}`; the request's “learning-space-no” is the code's `aiLrnNo`. |
| Service symbol | `aidt-viewer-api/src/main/java/ai/aidt/viewer/api/service/MathAILearningCenterService.java:15` and `serviceImpl/MathAILearningCenterServiceImpl.java:73-79` | `getMathAILearningCenter` parses the path value and calls `MathAILearningCenterDao.selectCourseWare`. |
| DAO symbol | `aidt-viewer-api/src/main/java/ai/aidt/viewer/api/dao/MathAILearningCenterDao.java:18` | `selectCourseWare(lctrCd, userId, aiLrnNo)` owns the response data query. |
| Domain ownership | `aidt-viewer-api/CONTEXT.md:60-71` | Defines AI Learning Center/courseware and names `MathAILearningCenterController` / `MathAILearningCenterService` as viewer concepts. |
| Parent contract and role split | `worktrees/A20-1186-viewer-api/docs/changelog/2026-07/20-A20-1186-ailrn-mdul-prgrs/GOAL.md:18-31` | Names the same endpoint, assigns API work to backend, assigns popup/navigation to frontend, and declares frontend behavior a non-goal of the backend run. |
| Existing backend change evidence | `worktrees/A20-1186-viewer-api/src/main/resources/mybatis/mapper/MathAILearningCenterDao.xml:60`; `.../enty/mathailearningcenter/ECrsWare.java:21`; `.../dto/mathailearningcenter/response/CoursewareModuleDto.java:33-35`; `.../serviceImpl/MathAILearningCenterServiceImpl.java:980` | Projects `COALESCE(TVSL.LRN_PRGRS_STTS, 'P100')`, carries `mdulLrnPrgrsStts`, exposes `lrnPrgrsStts`, and maps it into each module response. These files are evidence of ownership, not a canonical checkout path. |
| Regression evidence | `worktrees/A20-1186-viewer-api/src/test/java/ai/aidt/viewer/api/serviceImpl/MathAILearningCenterDaoXmlRegressionTest.java:20-26`; `MathAILearningCenterModulePrgrsSttsTest.java:30-86` | Tests the SQL projection, DAO-to-DTO mapping, and conditional JSON field. No test/build was run in this exploration. |

The canonical `aidt-viewer-api` working copy contains the endpoint and call chain but does not contain
the A20-1186 response-field additions above. The local A20-1186 completion note says they were promoted
to `aidt-dev`, but that report is not a substitute for freezing the source revision used by a future
route. Frontier 002 must record a route-time revision for the canonical checkout.

### Dependencies versus change ownership

The backend call chain is controller -> `MathAILearningCenterService` ->
`MathAILearningCenterServiceImpl` -> `MathAILearningCenterDao.selectCourseWare` -> MyBatis mapper.
The query reads viewer/LMS/LCMS-named tables, but table/schema names are data dependencies, not evidence
that `aidt-lms-api` or `aidt-lcms-api` needs a code change. No `ailearning` controller or
`getAiLearningMath` call was found under `aidt-lms-api/src` or `aidt-lms-web/src`.

The actual frontend consumer is:

- `aidt-viewer-web/src/api/index.ts:166` creates the `/ailearning` API client.
- `aidt-viewer-web/src/api/index.ts:1099-1102` calls `GET /{aiLrnNo}`.
- `aidt-viewer-web/src/stores/useStoreAiLearning.ts:349-449` loads that response and chooses the initial
  page through `getInitialMathCrsPageNo`; the inspected function uses `progressPageNo`/query page data,
  not module `lrnPrgrsStts`.

Consequently:

- response-field backend subtask: `viewer-api` only;
- popup/first-incomplete navigation, if separately in scope: `viewer-web` child;
- `lms-web`/`lms-api`: supporting ecosystem only, no change anchor for this contract.

## Bounded initial service catalog

The initial resolver catalog should cover checked-out product services with a build/package marker.
Tooling and emerging experiments remain out of the first catalog. `checkout_path` is canonical and must
be joined under the configured AIDT root; cards must never supply arbitrary paths.

| Canonical ID | Checkout directory | Kind | Verified runtime/base clue | Ownership file(s) found at service root | Branch family |
|---|---|---|---|---|---|
| `lms-api` | `aidt-lms-api` | backend | `/lms-api` in `src/main/resources/application.yml:24` | `CONTEXT.md`; no root `AGENTS.md`/`CLAUDE.md` found | `{feat|fix}/A20-*` |
| `viewer-api` | `aidt-viewer-api` | backend | `/v-api` in `src/main/resources/application.yml:24` | `CONTEXT.md`; no root `AGENTS.md`/`CLAUDE.md` found | `{feat|fix}/A20-*` |
| `lcms-api` | `aidt-lcms-api` | backend | `/lcms-api` in `src/main/resources/application.yml:23` | `CLAUDE.md`, `CONTEXT.md` | `{feat|fix}/A20-*` |
| `datactl-api` | `aidt-datactl-api` | backend | `/` in `src/main/resources/application.yml:24` | `CLAUDE.md` | `{feat|fix}/A20-*` |
| `lcms-was` | `aidt-lcms-was` | backend | `/lcms-was` in `src/main/resources/application.yml:24` | `CLAUDE.md` | `{feat|fix}/A20-*` |
| `lms-websocket-api` | `aidt-lms-websocket-api` | backend | `/lms-websocket-api` in `src/main/resources/application.yml:24` | no root ownership file found | `{feat|fix}/A20-*` |
| `demo` | `aidt-demo` | backend | `/aidt-demo` in `src/main/resources/application.yml:24` | `CLAUDE.md` | `{feat|fix}/A20-*` |
| `batch` | `aidt-batch` | backend, multi-module | Gradle root; module must also be recorded | `CLAUDE.md` | `{feat|fix}/A20-*` |
| `lms-web` | `aidt-lms-web` | frontend | Vite base from `VITE_BASE_URL`; dev proxy includes `/lms-api` and `/v-api` | `CLAUDE.md` | `csk-{feat|fix}/A20-*` |
| `viewer-web` | `aidt-viewer-web` | frontend | Vite base from `VITE_WEB_CONTEXT_PATH`; clients use `/v-api` and `/lms-api` | `CLAUDE.md` | `csk-{feat|fix}/A20-*` |
| `lcms-web` | `aidt-lcms-web` | frontend | Vite base `./` | `CLAUDE.md` | `csk-{feat|fix}/A20-*` |
| `bo-web` | `aidt-bo-web` | frontend | Vite base `./` | `CLAUDE.md` | `csk-{feat|fix}/A20-*` |
| `admin` | `aidt-admin` | frontend/Electron | Electron package marker | `CLAUDE.md` | `csk-{feat|fix}/A20-*` |

The project map/docs name `bo-api -> aidt-bo-api`, `bo-was -> aidt-bo-was`,
`lms-sse-api -> aidt-lms-sse-api`, and `lms-chatbot -> lms-chatbot`, but no files were found at those
top-level paths in this checkout. Keep these as disabled known IDs with
`checkout_present: false`; any route to them is Blocked/Human Review. Do not redirect them to a
similarly named present service. `aidt-lms-renewed` and non-product tooling such as
`aidt-business-intelligence` are present but deliberately deferred from this bounded first catalog.

`CLAUDE.md:86` and `.agents/skills/aidt-project-map/SKILL.md:97` define base `aidt-prd`, backend
`{feat|fix}/{KEY}`, and frontend `csk-{feat|fix}/{KEY}`. Store `kind` explicitly in the catalog; do not
infer frontend only from a `-web` suffix because `admin` is also frontend. Jira Bug maps to `fix`;
other work types, including this additive backend subtask, map to `feat` unless an explicit reviewed
rule says otherwise.

## Deterministic routing and confidence

### Mandatory validation

Before scoring a candidate:

1. Normalize an alias to one canonical service ID from the catalog; exact match only after alias lookup.
2. Resolve `checkout_path` under the configured AIDT root and require the expected directory plus its
   declared build/package marker.
3. Record service kind, issue type, derived branch prefix, and a route-time source revision.
4. Reject conflicting hard evidence, absent checkout, path escape/symlink escape, unknown service, or
   missing revision. These are blockers, not score deductions.

### Evidence weights

Score each service independently, deduplicate repeated evidence from the same source, and cap at 100.

| Evidence level | Weight | Rule |
|---|---:|---|
| Explicit Jira component or exact service metadata | 45 | Must normalize to a catalog ID and agree with current ownership. |
| Unique checked-in HTTP context/base path | 30 | Must come from current service configuration, not prose. |
| Exact current code symbol owning the requested contract | 35 | Controller/route, job, component, or callable symbol in that checkout. |
| Service-owned `CONTEXT.md`/`CLAUDE.md` domain statement | 15 | Corroboration; stale root prose cannot override code. |
| Parent exact endpoint/contract | 10 | Parent supplies scope but cannot route alone. |
| Issue type agrees with backend/frontend kind | 5 | Type mismatch is a conflict when explicit, not negative points. |
| Summary/body/label keyword or dependency/consumer text | 5 total | Supporting only; never a primary owner signal. |

Freeze a single-service route only at `confidence >= 90`, with at least two independent authoritative
categories among component, checked-in context path, code symbol, and service-owned domain docs; one
must be component or code symbol. Parent/supporting text alone can never pass.

A20-1188 scores `30 context path + 35 exact code symbol + 15 viewer domain + 10 parent contract + 5
backend type = 95`. A later explicit `viewer-api` component would cap it at 100. The score does not use
the words “AI”, “learning”, “module”, or “backend” as ownership guesses.

### Ambiguous and multi-service behavior

- Any hard conflict blocks even when a candidate scores 90+.
- If no candidate reaches 90, move the coordinator to Human Review/Blocked with every candidate score,
  missing evidence, and exact recheck needed.
- If more than one candidate reaches 90 for independently required changes, keep one coordinator and
  create one child per repository. Each child receives its own service ID/path, branch prefix, revision,
  acceptance slice, tests, and evidence score.
- A runtime dependency or API consumer does not create a child. Require a change anchor in that
  repository (explicit component/scope or exact affected symbol).
- If two candidates are plausible but only one reaches 90, do not silently discard the other: record it
  as `supporting_service` with its non-owning evidence. If conflicting direct anchors remain, block.
- Parent-only, ambiguous alias, absent-service, and multi-service cases must be first-class routing
  fixtures. There is no default to `lms-api`.

## Gaps Frontier 002 must preserve

1. No live Jira component/comment snapshot was available here; A20-1188's own body remains empty.
2. No route-time Git revision was captured under the read-only `rg/find` constraint; the implementation
   must capture and persist it before freezing a card.
3. The root docs claim broader per-service `AGENTS.md`/`CLAUDE.md` coverage than the current checkout
   contains. Ownership docs are enrichment, not a catalog existence check.
4. Four documented services have absent checkout paths; they must remain disabled, never guessed.
5. The current canonical `aidt-viewer-api` tree and the local A20-1186 worktree differ. Transient
   `worktrees/**` paths may support historical ownership but must never become catalog checkout paths.
6. The frontend navigation requirement is evidenced in `viewer-web` consumer code and parent text, but
   it is explicitly outside the backend run. A future combined parent card needs a separate validated
   frontend child rather than expanding A20-1188 automatically.
