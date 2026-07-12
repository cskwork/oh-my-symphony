# MODEL - the shared data contract (SSOT)

The chain `EXECUTE -> FLOW -> STORYBOARD -> PACKAGE` shares one source of truth: the
**Specification (기능명세)**. Every flow node, every screen, every callout traces back to a
SPEC. This file is the single place that names the **identifiers**, the **entities**, the
**synchronization rules**, and the **validation codes**; `flow.md` / `storyboard.md` /
`package.md` / `critic.md` reference it instead of re-defining their own.

This is a **cross-cutting contract** (like intent / critic / signal), not a routed domain - no
domain count change. Load it whenever an artifact in the chain is produced or verified.

## Identifiers

Stable, human-readable ids so a reader - or a diff - can trace any element to its source.

| Kind | Pattern | Example | Source |
|---|---|---|---|
| Requirement | `REQ-NN` | `REQ-01` | PRD section 5 (`execute.md`) |
| Feature | `FEAT-NN` | `FEAT-03` | groups requirements |
| **Specification (SSOT)** | `SPEC-NN` | `SPEC-07` | the implementable unit |
| Flow node | `FLOW-NN` | `FLOW-04` | `flow.md` |
| Screen | `SCREEN-NN` | `SCREEN-02` | `storyboard.md` |
| Callout | integer, per screen | `1`, `2`, `3` | 1:1 with a SPEC row |

Ids are assigned once and never renumbered; deleting an element retires its id - do not reuse
a retired number. Ids are the anchor targets for R5 deep-links (`workspace.html#SPEC-07`).

## Entities

Minimal fields - enough to **trace** and to **render**, nothing the web-app draft carried for
its own sake (no ACL, no timestamps, no `createdBy`, no server PKs).

- **Requirement (REQ)** - `title`, `description`, `priority` (P0/P1/P2), `acceptanceCriteria[]`.
- **Feature (FEAT)** - `title`, `requirementIds[]`, `priority`. A bucket over requirements.
- **Specification (SPEC)** - `featureId`, `title`, **`input` / `output` / `businessRule` /
  `exception`**. The atomic unit the whole chain stands on: the storyboard right-pane row and
  the flow node both point here. This is the SSOT - derive, never duplicate.
- **FlowNode** - `id` (`FLOW-NN`), `type` (start / section / page / action / decision / end),
  `label`, **`linkedSpecIds[]`** (required for page / action; empty for start / section / end).
  See `flow.md`.
- **FlowEdge** - `from`, `to`, `condition` (edge label; a `decision` node emits 2+).
- **Screen (SCREEN)** - `flowNodeId` (the `page` node it renders), `title`, `state`
  (normal / error / loading / empty), `prev`, `next`, and `preview` - **real rendered HTML** for
  the wireframe tab (actual styled, working UI an `iframe` runs, not low-fi boxes). The preview's
  interactive elements carry `data-callout="N"` so the workspace overlays callout N on them, and
  any element that navigates carries `data-nav="SCREEN-NN"` so a click jumps to that screen - the
  wireframe is a **clickable prototype** (following the flow's edges), not a static mock.
- **ScreenElement** - `calloutNo`, `area`, `selector`, **`behavior` / `dataContract` /
  `exception`**, **`linkedSpecId`** (the SPEC this callout realizes). See `storyboard.md`.

## Synchronization rules (R1-R6)

The chain stays coherent because each derived artifact is generated *from* the one above it,
never invented beside it. The drafter applies these; the critic checks them.

| # | Source -> Target | Rule |
|---|---|---|
| **R1** | PRD / Brief -> 기능명세 | Goal / constraint / persona changes propose updated REQ titles + acceptance criteria. Re-derivation is a **manual re-run step** - the skill is a procedure, not a live server. |
| **R2** | 기능명세 -> 유저플로우 | A `page` / `action` node MUST carry >=1 `linkedSpecId`. A node with none is `ORPHAN_NODE`. No SPEC -> no node. |
| **R3** | 유저플로우 -> 와이어프레임 | Every `page` node yields >=1 Screen (one per state: normal / error / loading / empty). A `page` node with no screen is `UNMAPPED_SCREEN`. |
| **R4** | 와이어프레임 -> 기능명세 | Each ScreenElement's behavior / dataContract / exception back-links 1:1 to its source SPEC (`linkedSpecId`). A callout with no SPEC is `ORPHAN_CALLOUT`. |
| **R5** | validation -> all tabs | A blocker deep-links to the offending entity. In the bundled HTML this is an **anchor link** (`#SPEC-07`), not a router. |
| **R6** | export -> all | One **`snapshotId`** stamps every artifact in a bundle, so PRD + spec + flow + wireframe are provably the same version. See `package.md`. |

R1 and R5 are the two the web-app draft assumed a live backend for; here they are a manual
re-run and an HTML anchor. **R2 / R3 / R4 / R6 carry over unchanged** and are the load-bearing
ones for chain integrity.

## Validation codes

The critic (`critic.md`) and each reference's completeness anchors cite these **by name**, so a
risk is machine-traceable to a rule. Same spelling everywhere.

| Code | Means | Rule | Severity |
|---|---|---|---|
| `ORPHAN_NODE` | flow `page`/`action` node links to no SPEC | R2 | Blocker |
| `FLOW_GENERATION_BLOCKED` | flow requested before a PRD outline + >=1 SPEC exists | R2 | Blocker |
| `UNMAPPED_SCREEN` | flow `page` node has no screen | R3 | Blocker |
| `ORPHAN_CALLOUT` | screen callout maps to no SPEC, or a SPEC row has no callout | R4 | Blocker |
| `MISSING_CALLOUT` | callout numbers are not `1..N` contiguous | R4 | Gap |
| `MISSING_EXCEPTION` | a SPEC `exception` never surfaces as a screen state / row | R3/R4 | Gap |

CRUD / permission / version-merge codes from the web-app draft are **out of scope** - the skill
has no store to violate. Keep the set at these six so the critic list stays short and every code
maps to a rule above.
