# PM Workspace

PM Workspace adds a local Artifacts screen for planning a change before implementation. It is disabled unless the corresponding `pm` switches are enabled in `WORKFLOW.md`.

Since v0.29.0 PM Workspace is the optional `oh-my-symphony-pm` distribution. It is not on PyPI; install it from a checkout with `python -m pip install ./packages/pm` (see [packages/pm](../packages/pm/README.md)). Without it, `symphony pm` prints the install hint and exits 2, and enabling a `pm` switch is a config error.

The workflow is explicit:

1. Select an isolated Git worktree and create a planning change.
2. Draft and review Idea, Requirements, Design, Plan, and Code Files. Design contains UX, storyboard, architecture, ERD, API, and integration subviews.
3. Validate the managed WBS and the pinned OpenSpec 1.14.2 profile.
4. Approve one exact revision. Approval creates a local planning commit; it does not dispatch, merge, push, or deploy.
5. Preview and confirm handoff. Handoff creates ordinary file-board tickets with immutable PM provenance and native dependencies.
6. Record implementation evidence before an explicit archive operation.

Storyboard frames are ordered functional cards. Selecting one opens structured screen, actor, UI, action, response, transition, requirement, and design fields while the server preserves the stable frame identity and unrelated source bytes.

The planning assistant offers seven explicit proposal-only actions: Draft artifact, Refine selected section, Find gaps, Review consistency, Explain dependency, Propose WBS, and Update impacted artifacts. The server owns section and target scope; every proposed document diff is reviewed before apply. When a proposal needs new managed identities, the host reserves permanent IDs and shows them in the review. Apply activates those IDs. Discard or cancellation retires unused reservations.

Each planning screen has a **What do these buttons do?** panel that explains every button in one sentence. It is folded by default in ordinary and demo workspaces. To choose the page language, add `?lang=en` or `?lang=ko` before the `#` in the address, for example `http://127.0.0.1:<port>/?lang=ko#/artifacts`. The choice is remembered like the language switch.

Document editors begin in readable view. **Edit source** reveals the editor and focuses its textarea; **Review document** returns to the preview. In Korean these controls read **소스 편집** and **문서 검토** through repeated toggles. Opening the source from Design → Storyboard, UX, or API preserves the selected language for the nested editor and its Add controls. Toggling does not save or discard a draft.

Recovered local source drafts can be saved immediately. The editor compares them with the saved document; a recovered draft is not already saved. When leaving a tab, Design subview, planning change, or project with unsaved edits, Cancel keeps the draft and selection. Confirming discard clears that recovery draft. Save explicitly to keep the changes in the planning files.

PM drawers and dialogs use one active overlay with an opaque readable surface. While it is open, the background is unavailable and Tab/Shift+Tab stay inside it; Escape or Close returns focus to its trigger. Storyboard Escape, Cancel, or background close asks before discarding changed fields. Cancelling keeps those fields available; a failed save keeps the editor and its error visible. Task autosave keeps its current drawer open.


## Demo

Try PM Workspace on sample content without touching a real project:

```bash
symphony pm demo             # English
symphony pm demo --lang ko   # Korean
```

The command creates a throwaway folder with its own Git project and one sample planning change, `team-leave-request`, in Draft. It validates the sample, then serves it on a free port at `127.0.0.1`, which only this computer can reach. Open the `Open:` link it prints. In the demo you can validate, edit, approve, and hand off to tickets; the tickets land on the demo's own board, no AI worker starts, and the planning assistant stays off.

- `--lang en|ko` picks the sample content and the page language. Without it, the demo follows `SYMPHONY_LANG`, then English.
- `--path DIR` puts the demo in `DIR`, which must be empty or not exist yet. Without it, the demo uses a new temporary folder.
- It needs `git` and Node.js 20.19.0 or newer on PATH.
- Press Ctrl-C to stop. The folder stays; delete it when you are done.
- `symphony pm demo` is not verified on Windows.

`symphony pm` always runs this command, even when the current folder contains a `pm` directory. To run a workflow from a directory named `pm`, give the path: `symphony ./pm`.

## Verification and coverage

An approved task may require native host commands, manual observation procedures, or both. **Run verification** executes an approved native command at the selected tested commit and records the real result without inventing a worker run. **Submit completion evidence** records a configured manual procedure with an immutable report from that commit. Evidence submission never completes a task by itself; the complete procedure set for one commit must pass, then a person reviews and explicitly accepts it.

Requirement and scenario coverage displays specified, linked, implemented, tested, accepted, and waived independently. Partial task counts remain visible. A waiver records its reason and reviewer but grants no execution, acceptance, ancestry, completion, or archive authority.

Every mutation carries the registered project identity, permanent change ID, expected revision, and an idempotency key where the operation can be retried. Direct file editing is supported through External edit mode; application writes stay suspended until re-index and validation finish.

## Configuration

```yaml
pm:
  authoring: false
  exchange: false
  assistance: false
  handoff: false
  planning_workspace_root: /absolute/path/outside/the/protected/checkout
  # openspec_executable: /path/to/openspec   # optional: your own OpenSpec 1.14.2
```

Symphony ships a trimmed OpenSpec 1.14.2 CLI that keeps only the six commands PM Workspace calls: version, schema validation, status, apply instructions, strict non-interactive validation, and archive. It is the default: when `openspec_executable` is absent, PM Workspace runs it with the `node` on your PATH, which must be Node.js 20.19.0 or newer. Set `openspec_executable` only to use your own OpenSpec install, which must be exactly 1.14.2. A config copied from an older example with `openspec_executable: openspec` keeps calling that external program; remove the line to use the bundle.

Strict validation treats upstream warnings as invalid. Each applicable main-spec requirement description and each new `ADDED` delta requirement description must be 500 characters or fewer; `MODIFIED` requirements skip that `ADDED` check but keep the other normal checks. Spec text that archive does not carry, such as an uncopied preamble or content outside the delta, requirement, and scenario blocks, raises a warning and therefore fails strict validation. Recognized artifact-output and `apply.tracks` globs nested more than 16 levels are refused. PM Workspace's containment, ownership, path refusals, and recovery are unchanged even though upstream accepts more managed symlink leaves. The Symphony PM schema (`symphony-pm`) and owner or project custom schemas are not edited or migrated; only upstream's bundled `spec-driven` instruction text is refreshed by the normal build.

Buttons that need a missing tool are disabled from the start and show the reason. Without a suitable Node.js, Approve revision and Preview archive are disabled; Validate still runs and lists the problems it can find without OpenSpec. With `assistance: true`, the planning assistant is disabled until the installed Codex CLI supports the restricted app-server controls and is logged in.

The planning client uses the installed Codex CLI after a bounded capability check and pins its model and reasoning effort. `pm.planning_codex_version`, `pm.planning_model`, and `pm.planning_reasoning_effort` are rejected at workflow load; remove them rather than expecting an override. Other unknown `pm.*` settings are currently ignored.

XLSX exchange requires the `pm-exchange` optional dependencies. Browser assets are local and lazy; no CDN is contacted. Automatic PM assistance uses a dedicated compatible installed Codex app-server process with GPT-6.1 Sol at medium reasoning, an isolated configuration home, no tools/connectors/network execution, read-only sandboxing, and a strict proposal-only output contract.

## Managed WBS

`tasks.md` is the sole task authority. Permanent `T-…` and `G-…` IDs survive title and order edits. Groups are non-executable. Executable tasks carry scope, acceptance, verification, integration impact, references, and typed dependencies. Unsupported markers, arbitrary checkboxes, duplicate YAML keys, cycles, missing references, and ambiguous nesting switch the document to a blocking source-repair state.

Authoritative verification selection uses versioned task metadata:

```yaml
verification_policy:
  version: 1
  procedures:
    - procedure_id: focused
      kind: native
    - procedure_id: manual-ux
      kind: manual
```

Human-readable Verification bullets remain descriptive. Only the structured policy selects procedures; an absent policy retains legacy native-command behavior.

Workbook imports accept a plain `.xlsx` filename and base64 workbook content. The preview uses a three-way comparison. Missing sheets or rows retain content; deletion requires an explicit `Action=Delete`. Formulas, duplicate IDs, external relationships, macros, embedded objects, hostile XML, oversized ZIPs, and oversized worksheet dimensions are rejected before mutation.

Workbook exports include a UTC export time and a leaf-task status summary in Read Me. Execution statuses are snapshots of matching published native tickets; missing or stale states are marked **Not supplied**, independently of the change's planning state. The summary lists up to 48 distinct supplied states and counts any additional states together; full values remain in WBS. Display Number follows parent/order hierarchy, WBS summarizes dependencies, and Dependencies shows adjacent task titles. Read-only runtime links point to matching native tickets on existing Traceability rows. Identifying columns stay frozen, task titles are indented by parent depth, and gray cells distinguish existing identities and derived/runtime/provenance fields. Dropdowns provide hints for Action, dependency Kind, and Reference Type. Type, Priority, and Agent Override still accept custom values.

## Final human review

Human acceptance is pending until the owner performs this review on the delivered commit and installed wheel. Automated checks do not grant merge approval. Use new disposable demo folders for each language and keep the reported commit, wheel hash, and installed package binding with your observations.

1. Run `symphony pm demo --lang en`, then repeat with `--lang ko`. Open the printed loopback link. The sample, five tabs, and button guides should use the chosen language; the planning assistant should be disabled with its demo reason. No agent run should start.
2. In Idea, toggle Edit source → Review document twice. Repeat in Design → Storyboard, UX, and API, including the outer Edit source action. Expect translated nested controls, a focused textarea when editing, unchanged source after toggling, and the appropriate Add storyboard or Add contract control. Type a disposable draft and toggle again: the draft and unsaved state should remain until Save draft or an explicit discard.
3. Validate and approve the unchanged sample. Expect combined validation, then Approved with a current approval ID. Edit and save a planning document: the earlier approval should no longer authorize that revision. Undo your disposable edit as needed, validate, approve, and preview handoff. Confirm only in the demo; expect ordinary demo-board tickets and no worker run.
4. Use the separately prepared installed-workflow fixture for XLSX review/import, proposal-only assistance, native/manual verification, explicit task acceptance, and archive/history. Check the expected observations in its evidence and [operations rules](pm-workspace-operations.md); the demo does not supply real worker or provider proof. Archived history should remain readable and refuse mutation.
5. Read the [accepted findings and platform limits](pm-workspace-operations.md#accepted-findings-and-current-handling). Record any failure with language, tab/subview, action, observed label/state, screenshot, and the exact delivered commit. Human acceptance and any later merge decision remain separate recorded decisions.
