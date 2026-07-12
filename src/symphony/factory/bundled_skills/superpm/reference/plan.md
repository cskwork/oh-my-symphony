# PLAN - the build-planning session (기획 세션)

When the ask is "help me plan something to build" - 기획하자 / 이거 개발하고 싶어 / 기획 도와줘 /
plan this feature end to end - rather than one named artifact, run the chain as a **stepped
session**: `EXECUTE -> FLOW -> STORYBOARD -> PACKAGE`, sharing the 기능명세 (SPEC) as the SSOT
(`reference/model.md`). This is an **orchestrator over the per-domain loop**, not a new framework:
each stage still runs its own reference and its own critic.

## When PLAN fires (vs single-artifact intent)

`intent.md` classifies one artifact for a named request ("write a PRD" -> EXECUTE). PLAN fires
**instead** when the request asks to design a buildable thing without naming a single deliverable:

- "이거 기획해줘 / 개발하고 싶어 / 기획 도와줘", "plan this app / feature", "PRD부터 화면까지",
  "기획서 만들어줘" - the user wants the developer-handoff package, not one isolated doc.
- If only one stage is named ("just the user flow", "PRD만"), stay in single-artifact mode - do
  not start a session.

## The stepped chain - a gate after each stage

Each stage is **one full loop turn** (Frame -> Draft -> Critic) on the shared SSOT, then a
**gate**: present the stage's artifact + its critic risks, then **stop** for the user to edit or
approve before the next stage. Never auto-run the whole chain; the user owns each transition.

| # | Stage | Produces | Adds to SSOT | Gate |
|---|---|---|---|---|
| 1 | **EXECUTE** (`execute.md`) | PRD + the SPEC table | `REQ` / `FEAT` / `SPEC` | "명세 확정 - 유저플로우로 진행할까요?" |
| 2 | **FLOW** (`flow.md`) | user-flow graph from SPECs (R2) | `FlowNode` / `FlowEdge` | "흐름 맞나요 - 화면으로?" |
| 3 | **STORYBOARD** (`storyboard.md`) | a screen per `page` node (R3) + callouts back-linked (R4) | `Screen` / `ScreenElement` (+`preview`) | "화면 맞나요 - 패키지로?" |
| 4 | **PACKAGE** (`package.md`) | one `snapshotId` bundle (R6) + `workspace.html` | `snapshotId` / manifest | "전달 패키지 확정?" |

A **blocker in stage N blocks advancing to N+1** (a flow node with no SPEC cannot become a
screen). Re-run the critic only on what changed at each gate, not the whole session.

## Shared state - the running SSOT JSON

Carry one growing JSON across stages - the same shape `templates/workspace.html` embeds:

```json
{ "snapshotId": "...", "features": [], "specs": [], "flow": { "nodes": [], "edges": [] }, "screens": [] }
```

Each stage **appends its slice**; later stages **read** earlier ones (FLOW reads `specs`,
STORYBOARD reads `flow`, PACKAGE reads all). Keep the running SSOT visible: after each stage emit
one line - "SSOT now: N specs, M nodes, K screens" - so the user watches it accrue. At PACKAGE,
write the final JSON into `workspace.html`'s `ssot` block and emit the bundle.

## Resuming / partial entry

- If a PRD or SPECs already exist (repo, attachment, prior turn), **start at the first unfilled
  stage** - do not redo settled ones.
- If the user jumps ("그냥 화면부터"), honor it but warn which upstream SSOT is missing, citing the
  model code (`FLOW_GENERATION_BLOCKED` / `UNMAPPED_SCREEN`).
- A session can be paused and resumed: the SSOT JSON (or the exported `workspace.html`) is the
  whole state.

## Critic completeness anchors

Every stage gate ran its critic; no stage advanced past an open blocker; the SSOT is internally
consistent at each step (`model.md` R2 / R3 / R4); the final PACKAGE shares one `snapshotId`; the
running-SSOT line was shown at each gate. A session that jumped a stage without producing its SSOT
slice is incomplete.
