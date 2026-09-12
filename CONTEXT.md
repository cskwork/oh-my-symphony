# Symphony Domain Glossary

## Project

A registered, durable binding between one Git repository, one workflow, one board, one service endpoint, and that project's worker runs. A Project's location does not change while it is registered.

## Project location

The canonical root directory of a Project's Git repository. All project-owned workflow and board paths are contained by this repository.

## Board

The issue collection owned by one Project. New issues are created in the selected Project's Board and cannot be reassigned to another Project by switching the user interface.

## Project adoption

Making an existing directory usable as a Project while preserving its contents. Adoption reuses existing Git metadata when present, initializes Git when absent, and adds only missing Symphony files.

## Project switch

A user-interface navigation from one independently running Project service to another. A switch can start the destination service, but it never retargets, redirects, or stops workers belonging to either Project.

## Ticket artifact

A host-owned deliverable file collected from a worker's workspace and shown on one ticket's card. A worker writes the file into the workspace artifact directory; the host copies it after each completed turn and keeps it after the workspace is removed. A Ticket artifact is operational data, not Git history, and it is never the same thing as the Git-tracked evidence under a ticket's evidence root.

## Artifact store

The per-Project directory that holds every Ticket artifact, one subdirectory per ticket, with a metadata index beside the files. The store is the only path the Board serves artifacts from. A file that is absent from the index is not served.

## Artifact sweep

The periodic removal of artifact subdirectories whose ticket left the Board or entered the archive state. A sweep removes a subdirectory only after the retention period passes with no change. A retention period of zero disables the sweep. A live ticket never loses its Ticket artifacts.

## Product Preview

An operator-started, loopback-only process launched from one Project's trusted workflow recipe in a detached checkout of the exact target commit. Symphony asks the operating system for a currently free managed port. Each Project service owns only the preview process group it launched; it never stops an unrelated listener or another Project's preview. A rare bind race fails the launch rather than killing the process that acquired the port.

## Configured Product Preview

A Product Preview with a non-empty launch command. A configured preview is enabled unless its workflow explicitly sets `enabled: false`; an omitted preview block remains valid and unconfigured.

## Preview health

The latest successful `2xx` or `3xx` response from the configured health path while the owned preview process is still running. Readiness requires both a live owned process and current health; startup success is not permanently latched.

## Application release contract

A versioned inventory of the launch path, viewports, visible behavior, and proof required before an application may be released.

## Release verifier

A ticket that independently proves an Application release contract against one exact Target commit. A failed cycle becomes historical evidence and never approves a later commit.

## Release evidence cycle

One Release verifier's contract-bound evidence for one Target commit, including native runner results and hashed artifacts. Each repaired target requires a fresh cycle.

## Release gate

The host-owned SQLite record that binds one Release finalizer to one Release verifier, expected contract hash, Cycle generation, and exact verifier/finalizer runs. Labels opt a ticket into the first cycle and remain useful diagnostics, but they are not approval authority.

## Cycle generation

A unique host-created identifier for one pending or approved Release gate. Replacing, restarting, or invalidating a cycle creates a new generation so an older worker run cannot inherit later approval.

## Release cycle item reservation

A host-owned SQLite binding from one release fingerprint, role, and repair key to an exact deterministic file-board ticket identifier. Symphony writes the reservation before creating the ticket, so a crash, concurrent service, or removed labels cannot allocate duplicate repair or verifier work.

## Finalizer completion token

A host-computed digest of the exact local finalizer ticket bytes and file-replacement generation observed in the authorized terminal run. Any later ticket rewrite invalidates completion and requires a fresh Release evidence cycle.

## Repair group

A stable product boundary that collects related failed release checks into one repair ticket. A failed check belongs to exactly one Repair group.

## Release finalizer

The delivery ticket named by an Application release contract. It can finish only in an exact host-bound run after the current Release verifier succeeds, its lease ends, and its approved Target commit, contract hash, and Cycle generation still match.

## Target commit

The full Git commit SHA at the local configured branch tip that a Release verifier proves. Symphony does not fetch or infer a remote/deployed commit; evidence for any other local commit is stale.

## Run attempt

One durable dispatch lease for one issue execution, identified only by `run_id`. A Run attempt has one resolved agent kind, one workspace, one start time, and one terminal outcome. Continuation turns, phase changes, and in-process backend retries remain events within that attempt; a later redispatch after terminal exit acquires a new `run_id`. The nullable numeric `attempt` field is metadata, not identity.

## Attempt event

A bounded, redacted lifecycle fact attached to one Run attempt. Attempt events describe Symphony-controlled milestones such as acquisition, session start, turn completion, failure, retry, and completion. They are diagnostic summaries, not raw agent transcripts or authoritative workflow state.

## Diagnostic bundle

A downloadable JSON projection of one Run attempt and its retained Attempt events. It is size-bounded and redacted before persistence. A Diagnostic bundle is operational evidence for debugging, not a secret store, complete audit log, or replay protocol.

## Continuation checkpoint

A private, host-owned recovery fact recorded only after a worker turn completes. It identifies the completed boundary, workflow state, and resumable agent conversation needed to continue safely. A turn that was merely started or interrupted never becomes a Continuation checkpoint.

## Durable continuation

A new Run attempt that succeeds an interrupted Run attempt from its latest eligible Continuation checkpoint. Symphony first confirms that the predecessor no longer owns a live worker, then gives exactly one successor the checkpoint. Durable continuation preserves completed progress but does not promise exactly-once execution of an interrupted turn.

## Request DAG

A dependency graph rooted at tickets carrying the same explicit, non-empty `request` value. Its read-only execution projection includes the transitive prerequisite closure even when a blocker belongs to another request; grouping metadata never hides a real dependency edge. A ticket without a request belongs to a standalone request group, and malformed cycles are reported as invalid rather than presented as an executable order.

## Scheduling policy

The configured ordering rule applied after eligibility and starvation recovery. `fifo` preserves ticket registration order. `dag` orders non-starved candidates by declared priority, remaining downstream Critical path, then registration order. A Scheduling policy never bypasses an unresolved dependency or another dispatch-safety gate.

## Queue forecast

A deterministic projection of current scheduler order, dispatch position, and dependency wave. It explains the present board snapshot and does not predict wall-clock completion time.

## Critical path

The longest remaining downstream dependency chain from a ticket through active work. In DAG scheduling it is a tie-breaker after declared priority, so work that unlocks a longer chain is preferred without overriding starvation recovery.

## Intent proposal

A server-owned chat action created only from the strict `<symphony-intent>` marker the chat agent emits after its human explanation. It holds a slug, a title, a Track, and the intent markdown in the sdlc-kit stage-1 shape (Problem, Evidence, Success criteria, Out of scope, Constraints, Open questions). It expires after thirty minutes, and any ordinary reply supersedes it so the agent can propose a revision. Prose that is not the exact marker never becomes a proposal. The server scans the intent with the sdlc-kit trip-wire heuristics; hits ride on the card and the filed ticket (`## Trip-wires`) and force the `full` Track.

## Intent approval

The operator's explicit confirmation of one Intent proposal, from the card or a bare `approve` reply carrying the session's browser-held confirmation capability. It is the only human gate of the chat-to-delivery cycle. Approval makes the server, never the agent, file the request ticket through the tracker API and record `.sdlc/work/<slug>/intent.md` with an `## Approval` section. Concurrent approvals of one proposal file exactly one ticket.

## Track

The delivery depth frozen by Intent approval. `full` walks every lane of the board. `micro` is allowed only when the exact files and symbols are known and success is checkable by an existing command; on the deep preset it lets Intake skip Research and move straight to Plan.
