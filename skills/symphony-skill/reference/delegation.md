# Delegating sub-tasks to Symphony

When the user gives a large task that decomposes into N independent
sub-tasks, you (the main conversational agent) can offload each sub-task
to Symphony rather than doing them inline. Each spawned worker runs in a
**fresh LLM session** with its own context window — so the calling agent's
context only carries the orchestration overhead, not all sub-implementations.

## Recipe

1. **Frame the objective** before creating files: one goal, expected user or
   operator outcome, constraints, out of scope, and proof commands. If the
   request is really one small change, create one ticket instead of a board.

2. **Decompose** the request into independent tickets, each with a
   self-contained spec. Independence is critical: Symphony runs eligible
   tickets concurrently up to `agent.max_concurrent_agents`. Use `blocked_by`
   for real dependencies, and use ticket IDs to express FIFO order among
   otherwise eligible tickets.

   A good ticket slice has all of these:

   - independently testable without relying on unfinished work;
   - one contract owner: a route, data model, CLI behavior, UI flow, or
     integration boundary;
   - small implementation surface: roughly <=5 files and <=500 net lines for a
     Build ticket;
   - explicit acceptance criteria, including edge/error cases;
   - exact verification commands the worker can run.

   Bad slices: `misc cleanup`, `implement app`, `frontend polish`, or two
   tickets that must edit the same behavior without an ordered dependency.

3. **Register** each as a Symphony ticket with a rich description (this
   description is the only context the worker gets, plus the WORKFLOW.md
   prompt template):

   ```bash
   symphony board new TASK-001 "<title>" \
     --priority 2 \
     --description "<full spec + acceptance criteria + file pointers>"
   ```

   Number tickets in the same order you created the task list. Do not let a
   later task receive a lower suffix, because the dispatcher treats
   `TASK-001` as earlier work than `TASK-002` regardless of priority.

4. **Launch headless** (TUI requires a TTY you don't have):

   ```bash
   symphony ./WORKFLOW.md --port 9999 2>> log/symphony.log &
   ```

5. **Poll for completion** at sensible intervals (don't tight-loop):

   ```bash
   curl -s http://127.0.0.1:9999/api/v1/state \
     | jq '.counts, .running[].issue_identifier'
   symphony board ls --state Done
   ```

6. **Collect results** by reading the `## Resolution` section of each
   completed ticket file:

   ```bash
   symphony board show TASK-A
   ```

## Ticket prompt template

Use this shape for `--description`. Keep it compact but complete; the worker
should not need the original chat.

```text
Goal:
- <one outcome in user/operator terms>

Scope:
- In: <files, flows, APIs, or components this ticket owns>
- Out: <nearby work this ticket must not touch>

Dependencies:
- blocked_by: <ticket ids or "none">
- Contracts to preserve: <public API, schema, prompt, CLI, UI behavior>

Acceptance criteria:
- WHEN <event> THEN <observable behavior>
- IF <edge/error case> THEN <observable behavior>

Implementation notes:
- Prefer existing helpers/patterns: <paths>
- Avoid: <risky shortcut or unrelated refactor>

Verification:
- <exact unit/integration command>
- <lint/type/build command if relevant>

Done evidence:
- Append a Resolution with changed files, tests run, and residual risk.
```

If a dependency is real, add it to the ticket frontmatter after creation:

```yaml
blocked_by:
  - TASK-001
```

Symphony workers should still self-check blockers with
`symphony board show <ID>` because frontmatter is advisory metadata, not a
distributed lock.

## Application-development slice patterns

For a typical app change, separate by behavior and proof surface:

```text
TASK-001  Data model / migration / fixtures       (no deps)
TASK-002  Core service or API behavior + tests    (deps: TASK-001)
TASK-003  UI flow or CLI surface + tests          (deps: TASK-002)
TASK-004  Integration verification + full suite   (deps: TASK-001..003)
TASK-005  Delivery docs / changelog / commit prep (deps: TASK-004)
```

For a bug:

```text
TASK-001  Reproduce failure with a failing test/log
TASK-002  Minimal fix + focused tests             (deps: TASK-001)
TASK-003  Regression verification + full suite    (deps: TASK-002)
```

For browser UI:

```text
TASK-001  Data/API contract
TASK-002  UI implementation
TASK-003  Responsive/accessibility/browser QA
TASK-004  Delivery proof
```

One Verify ticket per release is usually better than one Verify ticket per
Build ticket; it catches integration failures and keeps the evidence ledger in
one place.

## When this pattern wins

- Large, parallelizable work (N independent features / fixes / migrations).
- Each sub-task fits in a worker's context — the orchestrator can't help
  if a sub-task itself is too big.
- The user is happy to wait for a polling cycle rather than streaming
  output.

## When this pattern *doesn't* win

- Sub-tasks have ordering dependencies you cannot encode with `blocked_by`.
- The user expects real-time visibility into each sub-agent's reasoning
  (Symphony exposes only event-level logs, not the agent's stream).
- There is no callback / push notification — the calling agent must poll.

## Distinction from in-session TodoWrite

| Aspect       | Claude Code TodoWrite                  | Symphony delegation                       |
|--------------|----------------------------------------|-------------------------------------------|
| Execution    | same agent, same session               | N separate subprocesses, fresh sessions   |
| Context      | within calling agent's context         | each worker has its own context           |
| Sync         | synchronous, inline                    | asynchronous, polling required            |
| Best for     | steps within one conversation          | independent work units, large fan-out     |

The two compose: one Symphony worker can use TodoWrite internally to
track the sub-steps of *its* sub-task.

## Quality of decomposition matters more than mechanism

Symphony will faithfully run whatever you put in front of it. The hard
part is human-side decomposition:

- Each ticket's `description` should read like a self-contained spec:
  acceptance criteria, file pointers, test commands, dependencies, and proof.
  The worker has no conversation history.
- Avoid sub-tasks that require shared in-memory state. If two tickets
  must agree on a schema, write the schema down in a third ticket (or in
  a file the workspace will pick up via `after_create`) before launching
  them.
- Time-box experiments: start with `agent.max_turns: 5` and `agent.max_concurrent_agents: 1`
  while validating the decomposition. Crank up only after seeing a clean
  run.
