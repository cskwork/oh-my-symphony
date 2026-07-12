# FLOW - user-flow graphs (유저플로우)

The chain's missing link between EXECUTE (*what* to build) and STORYBOARD (*how each screen
behaves*): the screen-to-screen flow, its branches, and its states. A flow is a **graph of nodes
derived from the 기능명세** - never drawn free-hand. Runs after a PRD (or at least a feature list)
has settled the requirements; hands the screen list to `storyboard.md`.

Identifiers, node fields, and the R-rules live in `reference/model.md` - this file is the
framework that applies them.

## Upstream contract - nodes come from SPECs only (R2)

Do not invent flow steps. Every `page` and `action` node must carry >=1 `linkedSpecId`
(`model.md`). A node with no SPEC is `ORPHAN_NODE` (critic blocker). If a PRD outline plus at
least one SPEC do not yet exist, the flow is `FLOW_GENERATION_BLOCKED` - go settle the 기능명세
first, then return here.

## Node taxonomy

| Type | Role | linkedSpecIds | Emits |
|---|---|---|---|
| `start` | entry point | - | one page / action |
| `section` | a lane / grouping (회원가입, 결제...) | - | child page / action nodes |
| `page` | a screen the user sees | **required** | -> >=1 Screen (R3) |
| `action` | a user action or system step | **required** | next node |
| `decision` | a branch on a businessRule | **required** (the rule's SPEC) | 2+ edges |
| `end` | terminal | - | - |

Each node is one row: `FLOW-NN | type | label | linkedSpecIds[]`.

## Edges & sections

- **FlowEdge** - `from -> to`, optional `condition` label. A `decision` node has one edge per
  branch, each with a condition (성공 / 실패 / 재시도...); every branch leads somewhere - a
  condition with no target is a dead end.
- **section** - a grouping node whose children are the pages / actions of one journey stage.
  Flatten sections on export if the consumer wants a linear list.

## Derivation - SPEC -> nodes (R2 in practice)

Walk each SPEC and expand it into the nodes its behavior implies:

1. A SPEC whose `output` is a view -> a `page` node.
2. A SPEC whose `businessRule` branches -> a `decision` node, one edge per outcome.
3. A SPEC that mutates or calls -> an `action` node.
4. Group nodes that belong to one journey stage under a `section`.

Set `linkedSpecIds` on every page / action / decision **as you create it** - that link is the
R2 / R4 back-reference the critic checks. One SPEC may spawn several nodes; one node may realize
several SPECs.

## Downstream contract - page nodes -> screens (R3)

This is the contract `storyboard.md` consumes: **every `page` node requires >=1 Screen**, plus
one extra screen per non-happy state (empty / error / loading / permission). A `page` node with
no screen is `UNMAPPED_SCREEN`. The flow's hand-off is exactly this list of `page` nodes.

## Visualization - zero-dependency, two forms always

Match the storyboard templates' rule: **no external CSS/JS, opens straight in a browser**. No
Mermaid / CDN diagram libraries - they break the self-containment the bundle promises.

1. **Text step list (always)** - nodes in order, branches indented, edges as `-> label`. It
   prints, diffs cleanly, and is accessible. This is the canonical form.
   ```
   start
   -> [page] FLOW-01 로그인 (SPEC-01, SPEC-02)
      -> click 로그인 [action] FLOW-02 인증 (SPEC-03)
         -> decision FLOW-03 자격 검증 (SPEC-03)
            -[성공]-> [page] FLOW-04 홈 (SPEC-05)
            -[실패]-> [page] FLOW-01 로그인 (error state)
   ```
2. **SVG node/edge canvas (`templates/workspace.html` FLOW tab)** - nodes auto-laid-out by
   topological depth, typed colors (page / action / decision / start / end), arrowed edges with
   condition labels, and zoom / pan / node-drag. Rendered live from the embedded SSOT JSON with no
   external libraries; clicking a node jumps to its SPEC card.

## Critic completeness anchors

Every `page` / `action` / `decision` node links to a real SPEC (else `ORPHAN_NODE` /
`FLOW_GENERATION_BLOCKED`); every `page` node has >=1 screen downstream (`UNMAPPED_SCREEN`); the
graph has a `start` and at least one `end` with a path between them (no dead ends); every
`decision` branch carries a condition and a target; non-happy states are represented, not just
the success path. A flow that draws only the happy path is incomplete.
