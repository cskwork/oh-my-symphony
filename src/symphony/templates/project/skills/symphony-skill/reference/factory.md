# Autonomous development factory

Use this as the default beginner file-board path. It keeps ownership narrow:
Supergoal plans and delivers one Wayfinder slice; Symphony schedules tickets,
creates isolated worktrees, retries failures, and runs an independent Verify
turn.

```bash
git init /path/to/project
symphony factory init /path/to/project --agent opencode
# Commit the generated files before starting workers:
git -C /path/to/project add .
git -C /path/to/project commit -m "Initialize Symphony factory"
# Edit /path/to/project/wayfinder/tickets/*.md
symphony factory sync /path/to/project/wayfinder
symphony factory start /path/to/project/wayfinder
```

Wayfinder tickets use YAML frontmatter with stable `id`, `title`, `route`,
`blocked_by`, and `skills` fields, plus `## Acceptance criteria`, `## Proof
commands` (or `## Proof`), and `## Non-goals`. Routes are `GREENFIELD`,
`DEBUG`, or `LEGACY`. Every synchronized ticket receives `supergoal`. Add only
the `superdesign`, `superpm`, or `superqa` overlays when needed. Optional
`kind` values are `customer-research`, `research`, `design`, `product-spec`,
`qa`, and `ui`; optional `browser` must be a YAML boolean. `factory init`
prints a complete copy-ready schema.

Sync validates the full dependency graph before writing, imports every ticket
and its blocker edges by default, uses source provenance for idempotency, and
never rewrites a ticket after it leaves `Ready`. Use `--frontier-only` when an
operator deliberately wants to import only the currently actionable frontier.

The package owns pinned runtime copies of `supergoal`, `superdesign`,
`superpm`, and `superqa`; init and sync copy them into the project before
falling back to home-directory search for a nonstandard custom skill. Project
copies remain operator-customizable. Bundling the skills does not install
their browser runtimes. Superdesign gates require Node.js 18+,
`@playwright/cli` (`npm install -g @playwright/cli@latest`),
`playwright-cli install --skills`, and system Chrome or
`npx playwright install chromium`. SuperQA browser runs require `textual`,
`playwright`, `pyyaml`, and `pillow`, followed by
`python -m playwright install chromium`. First-time dependency or browser
installation may require network access.

`Ready` is a machine-owned dependency gate, not an agent stage. Sync validates
scope, acceptance checks, proof, and route; Symphony leaves unresolved
dependencies visible in `Ready` and promotes actionable tickets to `Build`
without creating a workspace or consuming an agent slot.

In `Build`, the Wayfinder ticket is the already approved scope and plan and its
sections are the run ledger. The worker reads Supergoal once and executes its
Build, full-spec, edge-case, adversarial-review, and exact-local-QA passes, but
does not create a second GOAL/PLAN/QA run vault. The Build contract requires a
short nonempty section for every pass before an independent `Verify` worker
can add the acceptance-criterion evidence table and move the ticket to Done.

Verification must use the exact commands declared in the ticket's Proof section.
One criterion may cite several inline-code commands in its table cell. Every
declared command needs evidence. The fresh Verify turn rewrites its Verification
section with current UTC time and output; browser work also supplies a fresh,
nonempty in-worktree report and explicit pass, failure, and side-effect counts.
These checks validate the worker's evidence and prevent bypassing Verify; they
do not independently attest that a model executed each reported command.

`Blocked` preserves the workspace for missing authority or environment and does
not create automatic repair tickets. Fix the named blocker, then use
`symphony board update <ID> --state Verify --workflow /path/to/WORKFLOW.md`
to retry proof, or `--state Build` when product work remains. Existing turn and
token budgets still apply; inspect the ticket's budget diagnostics before retrying.

If a Git snapshot fails (for example, an index lock or permission error), Symphony
keeps the product worktree and pauses the ticket instead of merging or removing
it. Inspect the `## Blocker` diagnostic and fix the named Git problem without
deleting the retained worktree. Return the ticket to `Verify` with the command
above, then clear its pause with the TUI's `P` control or
`POST /api/v1/<ID>/resume` on the running service. Moving the card alone does not
clear a persisted pause. Verify runs again before delivery; remote publishing
remains opt-in.

If a factory verifier writes Done but is then cancelled for exceeding its token
budget, Symphony also retains the worktree and pauses the ticket in a safe
non-success lane, normally `Blocked`. Its Blocker records the exceeded phase
budget and recovery steps. Inspect the cause before changing limits or retrying;
`agent.max_total_tokens_by_state.Verify` overrides the fallback
`agent.max_total_tokens` for Verify. Return the card to `Verify` and clear the
pause with the same operator control above. Fresh verification must finish before
delivery; a worker-written Done state alone is not evidence of a local merge.

After successful target delivery, Symphony marks its own Git-snapshot and
token-cancellation diagnostics as `Resolved Blocker (history)`. The original
message remains below that heading for audit. An operator-authored or edited
Blocker is left intact. Ambiguous older diagnostics, such as an embedded second
Blocker heading or repeated recovery instructions, also remain unchanged.
Resuming a ticket, entering Done, or committing its worktree alone
does not resolve a delivery failure. For workflows that publish remotely, local
ancestry alone is insufficient to mark a previous delivery failure resolved.

### PowerShell recovery

Use these steps for the factory file board after inspecting its `Blocker`.
Set the project path and ticket below; copy the running service's base URI from
`factory start` when prompted. Keep the retained worktree and database intact.

```powershell
$projectPath = 'C:\Work\My Product'
$workflow = Join-Path $projectPath 'WORKFLOW.md'
$ticket = 'TASK-1'
$baseUri = (Read-Host 'Paste the running factory service base URI').TrimEnd('/')
$ticketSegment = [Uri]::EscapeDataString($ticket)
symphony board show "$ticket" --workflow "$workflow"
if ($LASTEXITCODE -ne 0) { throw 'Ticket inspection failed; stop here.' }
```

Fix the named cause before continuing. Use `Verify` when product work is ready
for fresh proof; use `Build` instead when product work remains. Inspect existing
budget diagnostics before changing limits. The next block explicitly changes
the card and resumes its persisted pause; moving the card alone cannot resume it.
For an authenticated service, supply its configured authorization header to the
HTTP command; keep the credential out of shared output.

```powershell
symphony board show "$ticket" --workflow "$workflow"
if ($LASTEXITCODE -ne 0) { throw 'Ticket inspection failed; resume was not requested.' }
symphony board update "$ticket" --state Verify --workflow "$workflow"
if ($LASTEXITCODE -ne 0) { throw 'State update failed; resume was not requested.' }
$resume = Invoke-RestMethod -Method Post -Uri "$baseUri/api/v1/$ticketSegment/resume" -ErrorAction Stop
if ($resume.changed -eq $true) {
    Write-Output 'Pause cleared; fresh verification and delivery still need inspection.'
} else {
    Write-Warning 'No pause was cleared (changed=false). Inspect the current state; delivery is not confirmed.'
}
```

HTTP errors stop the block. A resume `404` means no resumable entry was found;
a detail-API `404` also does not prove delivery. Inspect the board and service
log instead of treating either response as completion.

After fresh Verify succeeds, inspect its criterion/proof-command results.
The host may consolidate worker commits before delivery, so the worker's tested
commit need not be an ancestor of the target branch. Use the actual launch/target
branch below, then select the host delivery commit from its log:

```powershell
$targetBranch = Read-Host 'Target branch used by this factory run'
git -C "$projectPath" log -5 --oneline "$targetBranch"
if ($LASTEXITCODE -ne 0) { throw 'Target branch log inspection failed.' }
$deliveryCommit = Read-Host 'Host delivery commit from the target branch log'
git -C "$projectPath" show --stat --oneline "$deliveryCommit"
if ($LASTEXITCODE -ne 0) { throw 'Host delivery commit inspection failed.' }
git -C "$projectPath" status --short
if ($LASTEXITCODE -ne 0) { throw 'Target Git status inspection failed.' }
git -C "$projectPath" worktree list --porcelain
if ($LASTEXITCODE -ne 0) { throw 'Worktree inspection failed.' }
```

Match the intended product changes and verification evidence to that host
delivery commit, and rerun the ticket's recorded proof commands on the delivered
tree. Review any dirty files, and compare the retained worktree path from `Blocker`
with the worktree list and disk. Preserve unexpected remaining work for
inspection. Board `Done`, a cleared pause, and local commit ancestry each leave
other delivery checks open; remote publication needs its own configured proof.

### Worker timeouts and recovery

The local objective is bounded failure detection with retained work and a usable
recovery path. It is not an availability or latency guarantee for a hosted model.
These are the existing defaults; the selected backend and per-state workflow
settings determine the effective limits.

| Boundary | Default limit | What happens when it is exceeded |
| --- | --- | --- |
| Codex initialize or thread/start reply | `codex.read_timeout_ms: 20000`, per request | A response-timeout diagnostic is reported; the ticket is paused for operator recovery. |
| Codex turn/start reply | `codex.turn_timeout_ms: 3600000` | A turn-timeout diagnostic is reported. |
| Codex turn completion after its reply | A separate wait with the same turn limit | A completion timeout is reported. The two waits are not one combined deadline. |
| Worker without progress | `codex.stall_timeout_ms: 300000`, checked during reconciliation | The stalled worker is cancelled. Detection includes polling delay (factory default: 30000 ms) and process cleanup. |
| Recognized transient overload/network error | Retry after 10, 20, then 40 seconds by default | Retries use exponential backoff, capped by `agent.max_retry_backoff_ms` (300000 ms); `agent.max_retries` defaults to 3. |

Ordinary timeout, stall, and other non-transient failures pause the ticket;
queued retries stay held until explicit resume. Inspect the service log and
ticket diagnostic, repair the named dependency, then resume with the same
operator control described above. Exhausted retries produce an Escalation and
Blocked state. Other agent adapters have their own stream/turn timeout handling;
Codex's per-request read limit is not a universal HTTP timeout.

### Windows service interruption

New Windows backend processes run under a private supervisor and an owned job
object. The supervisor starts the backend only after the host records its
ownership. If the orchestrator exits unexpectedly, the supervisor terminates
that job's ordinary child processes. Restart confirms that the recorded job is
empty or gone before reusing the workspace; an operator pause still requires
explicit resume. This contains ordinary child creation, not work submitted to
external services, WMI, scheduled tasks, or preexisting daemons.

Older run records without this ownership identity remain protected when process
cleanup cannot be established. An absent numeric process ID alone does not prove
that all old descendants exited. Preserve the worktree and run database and seek
operator-reviewed recovery for those records; do not delete the database to
clear a hold. Permission or containment failures are reported rather than
launching an uncontained backend.

`factory init` prints the exact Supergoal WAYFINDER prompt to use next.
It merges runtime ignore rules into `.gitignore` without replacing user rules:
board cards, worktrees, logs, and `WORKFLOW-PROGRESS.md` stay local runtime
state. Commit the Wayfinder spec and product code, not `kanban/*.md`.
`factory start` runs sync, Doctor (including any `--port` override), then the
managed service. A Doctor failure stops before process launch. Verified Done
work merges locally into the branch that launched the factory. Remote publishing
is opt-in: configure an upstream and `agent.auto_merge_push_target: true`. Use
`examples/advanced/WORKFLOW.file.example.md` for the advanced production
pipeline.

Each worker's `kanban/` must link to the host board. The setup hook replaces
an empty checkout directory with that link and is safe to rerun. It stops with
a repair message if an older repository has tracked cards that produce a
nonempty real directory; continuing would let the worker edit a stale private
card that the scheduler cannot observe.

The shipped cleanup hooks detach board links and Windows directory junctions
before Git removes the worktree. They validate the worktree's repository,
location, registration, and every configured board target first. A cleanup
diagnostic is not a promise that a custom hook preserves the directory:
Symphony's existing workspace manager still proceeds with its own removal after
a hook failure.

Upgrading Symphony does not rewrite an existing project's copied hooks. Before
using an older project on Windows, preserve its customizations and compare its
`before_run` and `before_remove` blocks with the current
`examples/advanced/WORKFLOW.file.example.md` (or `WORKFLOW.example.md` for Linear).
Also refresh the copied `scripts/symphony-setup-worktree.sh`: advanced refresh
uses it to remove and reattach a retained worktree, so its cleanup must protect
the same shared links. For stale empty targets, setup's existing metadata prune
can remove other unlocked stale registrations; it preserves valid and locked
worktrees and uses ordinary branch checkout checks.
Factory projects should also compare `scripts/factory-remove-worktree.sh` with
the current factory template. Refresh those cleanup adapters together with the
installed package: they use its private `symphony._worktree_cleanup` helper and
the interpreter supplied by Symphony. Run `symphony doctor ./WORKFLOW.md` after
the reviewed update. Do not retain an older raw `git worktree remove --force`
hook on Windows, where Git can traverse a shared-board junction.

## Data and external services

The factory keeps its board, worktrees, progress, and run diagnostics in the local
project. Tickets and logs can contain task text, code, command output, paths, and
agent responses; review them before sharing a diagnostic bundle. Runtime ignore
rules keep these files out of ordinary product commits, but do not encrypt them.

The selected agent CLI receives the rendered ticket prompt and access to its
workspace. That agent and any tools it runs may send prompts, code, or files to
their configured external services. Their authentication, charges, retention,
and data-handling terms apply. Check those settings before dispatching sensitive
work; choosing a local file board does not make a hosted coding agent local.
Factory remote Git publication is separately opt-in as described above.

Symphony is provided under the repository's [Apache 2.0 license](../../../LICENSE).
Use the [project issue tracker](https://github.com/cskwork/oh-my-symphony/issues)
for support and feedback, after removing credentials and private project content.
The factory does not create a Symphony product account or subscription.
