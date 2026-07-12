# STORYBOARD - screen design documents (화면설계서)

Frameworks for turning a validated direction into a **storyboard-style screen design document**
(화면설계서): each screen drawn or replicated with numbered callouts on the left, and a per-element
table on the right. That right-hand table (action / data / exception / state) **is** the functional
spec (기능명세) - one artifact serves planners and API/frontend devs at once. Runs after
discovery/research (and usually a PRD) has settled *what* to build; this settles *how each screen
behaves*.

The bias: one screen (or one state) per page, every interactive element accounted for, no orphan
callouts, and every data/exception named or marked an assumption.

## Upstream contract - where the screen list comes from

Do not invent screens. Derive them from the **FLOW graph** (`flow.md`; R3 in `model.md`): every
`page` node yields >=1 Screen, plus one per non-happy state (empty / error / loading /
permission). A `page` node with no screen is `UNMAPPED_SCREEN` (critic blocker).

If no flow exists (storyboard used standalone), fall back to the prior loop's output:

- **Customer journey map** (`research.md`) -> one screen per stage / moment of truth.
- **PRD section 6 UX/flows + user stories** (`execute.md`) -> one screen per step in the key flow;
  one extra page per non-happy state (empty / error / loading / permission).
- **OST solutions** (`discover.md`) -> the screens the chosen solution requires.

Output of this step: a flat screen list, each row `SCREEN-NN | title | state | prev -> next`,
each screen tied to its source `page` node. A screen with no entry/exit is incomplete - every
screen is reachable and leads somewhere.

## Mode - B (기획) vs A (문서화)

Branch on whether the UI exists yet:

- **Mode B (기획 / plan-first)** - no UI yet. Draw a low-fi **wireframe** from the requirements
  (boxes, labels, inputs), then number and annotate it. The design-up-front path; most pre-build
  work is here.
- **Mode A (문서화 / document)** - a built screen or Figma frame exists. Replicate it (paste the
  real markup, or embed a screenshot / Figma export) and annotate that. The left side matches the
  real screen; the right table describes it.

State the mode per screen.

## Element-extraction checklist - this IS the 기능명세

For each screen, walk the UI and list every element a user touches or that carries data. Each one
becomes a callout row:

1. **No** - callout number; matches the marker on the left 1:1.
2. **구역 / area** - which region of the screen (header, form, list, footer...).
3. **요소 / ID** - the element name + its DOM id or selector (so devs can find it).
4. **설명 / behavior**, tagged by kind:
   - **동작 (event)** - what firing it does (click -> validate -> POST /sessions).
   - **데이터 (data)** - the data contract in/out (field, type, source API, validation rule).
   - **예외 (exception)** - failure / empty / permission behavior and the message shown.
   - **상태 (state)** - enabled / disabled / loading / selected variants, if any.

Cover every interactive element (buttons, inputs, links, toggles) and every data-bound region
(lists, counters, status). A static label with no data and no event needs no row. An on-screen
element with no row is a gap; a row with no left-side marker is an orphan - both fail the critic.

Each callout row back-links to its source SPEC (`linkedSpecId`; `model.md` R4) - its 동작 / 데이터
/ 예외 restate that SPEC's `businessRule` / `dataContract` / `exception`, so a developer reads the
same contract from either side. A callout with no SPEC is `ORPHAN_CALLOUT`; callout numbers must
run `1..N` with no gaps (`MISSING_CALLOUT`); a SPEC exception with no screen state is
`MISSING_EXCEPTION`.

## Storyboard structure

- **화면 목차 (TOC, left rail)** - a sticky sidebar on every screen page listing **all** screens,
  grouped, so a reader jumps screen-to-screen without returning to the board. The current screen
  is highlighted (`a.t.current`); planned-but-undrawn screens are shown dimmed and unlinked
  (`span.t.plan`). This is on by default in the bundled template. The block is **identical on every
  page** - only which entry is `current` changes.
- **Left pane** - the screen: Mode B wireframe or Mode A replica/image. Each annotated element
  carries a numbered marker (red circle, white border, centered number).
- **Right pane** - the 5-column table above (No / 구역 / 요소·ID / 설명·동작·데이터·예외), one row
  per marker, in callout order.
- **Board (index)** - a thumbnail grid linking every screen page, so a reader sees the whole set
  and clicks into any screen. (The board is the visual overview; the TOC is the per-page text jump.)
- **Viewer controls (on by default)** - each page ships a top bar + screen caption with: 글자 크기
  (font-size A-/A/A+, persisted), 콜아웃 번호 진하기 (callout opacity slider), 화면 확대/축소
  (wheel + ＋/－/맞춤 + drag-pan + double-click reset on the `.sb-zoom`/`.sb-stage`), 원본 보기
  (full-screen lightbox that clones the screen stage), and 이전/다음 pager. These work in both
  modes (the zoom/lightbox operate on the `.sb-stage`, whether it holds a wireframe or an `<img>`).

## Production - default to workspace.html

The **default deliverable is `templates/workspace.html`** - superpm's own self-contained,
interactive 3-tab canvas (기능명세 cards / 유저플로우 SVG / live 와이어프레임), driven by one
embedded SSOT JSON (`model.md`). **Always produce it** unless the user explicitly asks only for
the per-screen image-replica set. To fill it: swap `{{PROJECT_NAME}}` / `{{SNAPSHOT_ID}}`, replace
the example `ssot` script JSON with the real `features / specs / flow / screens` graph, and the
embedded live validation (R2/R3/R4 + `MISSING_*`) checks back-link integrity in the browser. Each
`screen.preview` is **real working HTML** carrying `data-callout="N"` (the callout overlay) and
`data-nav="SCREEN-NN"` (so the wireframe is a clickable prototype following the flow). Zero
external CSS/JS - double-click opens it in any browser, offline.

For a real-app feel, set `brand` + `nav` (the app's side menu) on the SSOT and `device`
("web" | "mobile") + `nav` (active menu id) per screen: web+nav screens render inside a
persistent left-menu app shell, while mobile / popup / no-nav screens render full-screen. The
flow auto-lays-out left-to-right (cycle-safe BFS from the start node; failure / back-edges curve
back) and fits-to-view on open, so no node overlaps or is hidden.

Build it coherently so validation goes green: every `page`/`action`/`decision` flow node links
to >=1 real SPEC (R2); every `page` node has a `screen` whose `flowNodeId` matches it (R3); every
screen callout's `linkedSpecId` resolves and its `exception` restates that SPEC's exception (R4 /
`MISSING_EXCEPTION`); callout numbers run `1..N` (`MISSING_CALLOUT`). For a large screen set, fan
the screen-`preview` authoring out to subagents over a pre-assigned SPEC/SCREEN id range, then
merge and re-validate.

### 대규모: 모듈 분리 모드 (도메인 기능별 분리)

When the screen set spans many domains (e.g. 7 modules / 40+ screens), keep `workspace.html` a
self-contained **shell** but split the SSOT into **one data file per module** so each domain is
owned, authored, and verified independently:

- `data/_common.js` (`templates/workspace-data-common.js`) - `window.SSOT` root + `registerModule()`; loaded first.
- `data/NN-<module>.js` (`templates/workspace-data-module.js`) - one `registerModule({id,label,order,features,specs,screens,flow})` per domain (one 기능명세 / module).
- `workspace.html` loads them before the app via `<script src="data/_common.js">` ... `<script src="data/01-<module>.js">` ..., then merges `window.SSOT`: sorts by module `order`, and auto-stitches hub->entry edges from the `hub:true` node to each module's `entry:true` node. A module filter (`#modSel`) scopes all three tabs and auto-hides for single-module / inline-`#ssot` storyboards (fully backward compatible).
- Classic `<script src>` is CORS-exempt, so double-click-offline (`file://`) still holds.
- Fan each module file out to a fresh subagent against the shared contract + a golden example module; each self-validates its own file in isolation (the 5 `model.md` rules), then assemble and re-run whole-SSOT validation (no duplicate ids across modules, every `data-nav` target resolves).

Use inline `#ssot` (single file) for small storyboards; switch to module files once the set is large or multi-domain.

The per-screen side-by-side pages (`sb-NN-*.html` + board) are an **optional supplement** - reach
for them when the source is a built/Figma screen to be documented as an **image replica** (Mode A),
or when a reader wants one printable page per screen:

1. **Detect.** Check for `~/.claude/skills/storyboard-spec/SKILL.md`.
2. **If present -> delegate** the per-screen image-replica rendering (Figma REST auto-extraction,
   headless-Chrome thumbnails, themeable CSS). Do not re-implement its work.
3. **If absent -> bundle** from `templates/storyboard-page.html` (one per screen; `{{...}}` +
   `sb-mark`/`sb-cue` markers; right = the `sb-notes` table) + `templates/storyboard-board.html`
   (thumbnail board).

workspace.html and the per-screen set share the same SSOT shape, so a document can ship both - but
workspace.html is the default and the per-screen pages are added only on request or for Mode A.

## Critic completeness anchors

Every screen has a prev/next (no dead ends); every interactive element has a callout row with at
least an event and an exception; left markers and right rows are 1:1 (no orphans either way); every
data contract is sourced or labeled an assumption; non-happy states (empty / error / loading /
permission) each get a page or a row; the mode (A/B) is stated per screen; every page carries the
same 화면 목차 (TOC) with the current screen marked. A storyboard that documents only the happy
path is incomplete.
