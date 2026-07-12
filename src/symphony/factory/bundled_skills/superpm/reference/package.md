# PACKAGE - the integrated implementation bundle

The chain's terminal node: fold PRD + 기능명세(SSOT) + 유저플로우 + 와이어프레임 into **one
bundle** a developer or an agent can implement from without re-interpreting. Runs after
STORYBOARD (and its critic) is green. Identifiers and R6 live in `reference/model.md`.

This is the **기획서 산출물** the chain produces - the implementation package itself, not a
meta-plan about it.

## What it bundles

One snapshot of every chain artifact, under a single id (R6):

- `prd.md` - the PRD (`execute.md`).
- the 기능명세 (SPEC table) - the SSOT.
- the flow - text step list + (optional) svg / HTML (`flow.md`).
- the wireframes - the storyboard screens (`sb-NN-*.html`) or `workspace.html`'s wire tab.

## snapshotId & manifest (R6)

- **`snapshotId`** = `<slug>-<YYYYMMDD-HHMM>` - one string, stamped on every artifact in the
  bundle. Use a timestamp the user/environment provides; do not fabricate one.
- **`_manifest`** - a JSON block (also embedded in `workspace.html`):

  ```json
  { "snapshotId": "referral-20260622-0900",
    "artifacts": [
      { "path": "prd.md",        "kind": "prd",  "specRefs": ["SPEC-01", "SPEC-02"] },
      { "path": "workspace.html","kind": "html", "specRefs": ["SPEC-01", "SPEC-02", "SPEC-03"] }
    ] }
  ```

- Integrity = "everything shares one `snapshotId`". A mismatched id means the bundle mixes
  versions - a blocker.

## Cross-artifact integrity check

Before export, every back-link must resolve (`model.md` R2 / R4):

- every `FlowNode.linkedSpecIds` points to a SPEC that exists;
- every `ScreenElement.linkedSpecId` points to a SPEC that exists;
- every `page` node has a screen (R3); every SPEC `exception` surfaces somewhere (R3 / R4).

A broken back-link blocks the bundle - the package is only as trustworthy as its links.

## Export forms

Pick the forms the consumer needs; bundling is a **file-set convention, not a server job**
(there is no backend - zip creation / download is the browser's or the user's step).

| Form | Contents | For |
|---|---|---|
| `markdown` | PRD + SPEC table + flow text list | reading, code review, agent input |
| `json` | the whole SSOT graph (REQ / FEAT / SPEC / flow / screens) + `_manifest` | downstream tooling, re-import to `workspace.html` |
| `svg` | the flow diagram | slides, docs |
| `html` | `workspace.html` (3-tab) and / or the `sb-NN-*.html` set | single-file review |

Default: `json` (the re-importable SSOT) + `html` (the human view), under the same `snapshotId`.

## Critic completeness anchors

One `snapshotId` across all artifacts (no version mix); every back-link resolves (no
`ORPHAN_NODE` / `ORPHAN_CALLOUT` / `UNMAPPED_SCREEN`); the `_manifest` lists every artifact
actually in the bundle; the chosen forms cover the consumer's need (an agent needs json /
markdown, a stakeholder needs html). A package that ships with an unresolved back-link is
incomplete.
