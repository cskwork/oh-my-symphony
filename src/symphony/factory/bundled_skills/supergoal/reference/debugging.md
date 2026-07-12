# Debugging - DEBUG mode

DEBUG is deep-and-narrow. Use one driving `debugger` (`agents/debugger.md`); spawn helpers only for independent probes
and save summaries in the vault.

Reproduce exit gate: deterministic repro, cheap to re-run after every probe. No trusted loop, no fix. If
none can be built, STOP and report. For web/UI bugs, drive through `qa-tester` and `reference/qa.md`.

## Read-only until the cause is confirmed

Through Reproduce and Diagnose, analyze only. First source write waits until one root cause has direct
evidence and a written fix plan.

## Observe-first triage - live evidence before code

For a RUNNING-system bug (UI/API/data/auth/perf), do NOT start with code reading or git archaeology.
History narrows WHEN; live observation narrows WHERE. Do WHERE first.

1. **Observe the failing flow at the symptom's boundary.** Reproduce it in the real environment
   and capture actual artifacts: devtools Network tab / Playwright HAR (call ORDER + payloads),
   API responses, logs, DB/queue records. Diff actual vs expected at that boundary - a missing
   field, an out-of-order dependency call, or an unexpected status often ends the search in
   minutes. For a web/UI symptom, drive this through `qa-tester` (`reference/qa.md`): load the
   repo's `.domain-agent/qa/nav-map.md` to reach the exact screen (its entry/auth flow, popups,
   new tabs), then capture the screen's real calls with `playwright-cli requests` (grep by path;
   `request <index>` for one) - the method + path + status it actually fires. That pins screen -> exact
   endpoint, so you open only the backend code that owns it instead of guessing where the symptom
   lives. If no nav-map exists yet, build one first (`reference/qa.md` "Navigation map"); if a saved
   entry no longer matches the live site (selector miss, route 404, popup target moved, API path
   changed), correct that row as you go, and promote a confirmed `screen -> API` row back into it.
2. **Bisect by boundaries, not by code.** Walk the chain the data/behavior crosses (UI -> API ->
   queue -> store -> batch -> external) checking the actual artifact at each hop; for
   "stopped accumulating" symptoms compare each hop's last-seen timestamp against the incident
   start. The first boundary where actual != expected is the locus - only then open the code that
   owns it.
3. **Early report checkpoint.** Once broken boundary + direct evidence are in hand, REPORT before deep
   root-causing. Continue deeper only if the user asks or the fix requires it.

## Single-driver default, escalate on breadth

Default: one driver and Reproduce -> Localize -> Fix -> Verify. Escalate only when the fix spans many
files/services (roughly >5 files or cross-service path): one agent localizes, another fixes.

## Distributed triage (cross-boundary bugs)

For DB/API/network/queue failures, map before digging:

1. **Golden signals first.** Frame the symptom in latency, traffic, errors, saturation (RED =
   rate/errors/duration is the request-side subset; USE is the resource side). State *what* is broken
   before guessing *why*; chase only causes that are definite and imminent.
2. **Correlation ID is a precondition.** Cross-service RCA needs a trace/request/correlation ID
   propagated across every boundary. If none exists, say so and make establishing it the first task.
3. **Known-good vs known-bad.** Compare a passing request/trace against a failing one. Find the
   tag/value *unusually* correlated with failures, not ones always present. Recent clustering matters;
   always-clustered is baseline noise.
4. **Failure-pattern checklist.** Before blaming app logic, rule out common distributed traps:
   cascading overload (grows by positive feedback; secondary symptoms mimic the cause), retry storms
   (retries multiply load across layers — needs jittered backoff + per-request limit + retry budget),
   missing deadline propagation (work spent on already-failed requests), partial failure / bimodal
   latency (a few slow requests exhaust upstream pools). Read latency distributions, not averages.

## Loop

1. **Reproduce red (fail-to-pass).** Create a deterministic failing test or scripted repro in a clean
   sandbox (fresh `git worktree` at HEAD). The repro must FAIL on current code and PASS after the fix
   with no new failures (F->P). Scaffold repro explicitly. Flaky/timing/concurrency repros must fail
   consistently over N repeats before trust. Record `run-to-prove`.
2. **Localize.** Narrow the smallest region. Use `git bisect`, input/state binary search, distributed
   triage, and focused instrumentation. Read structure first; load full code only for surviving suspects.
3. **Compete hypotheses (symptom vs cause).** Put 2-3 root causes in `PLAN.md` using a hypothesis
   ledger format: symptom, candidate cause, evidence-for, evidence-against,
   and "definite & imminent?". Phrase each candidate as a falsifiable prediction (if cause C, then
   probe P flips the result) so the most discriminating probe is obvious. Pick the next probe that
   best separates them. Resist fixating on the first plausible cause; advance only causes backed by
   direct evidence.
4. **Confirm.** Before locking the cause, present the 3-5 ranked hypotheses to the user for re-ranking
   (`reference/interview.md` DEBUG variant owns the mechanics: non-blocking, AFK-proceed, never abandon
   evidence for preference). Then back one hypothesis with direct evidence at the boundary. Write the
   fix plan in `PLAN.md`; the plan approval gate (`reference/role-loop.md`) clears before the first
   fix edit.
   If fix blast radius reaches past the cause site, present it with the re-ranking and apply
   `reference/interview.md` before the first edit. Ask only on SKILL.md hard stops or genuine ambiguity.
5. **Fix root cause (minimal diff, checkpoint per step).** Smallest change that addresses cause, not
   symptom. No silencing, fake success, broad refactor, or unrelated cleanup. Checkpoint after each
   plan step so every change traces to one observed outcome; do not free-form edit until green.
6. **Verify regression.** The red repro now passes in a clean sandbox and the full suite stays green.
   This failing-before/passing-after proof is DEBUG's literal delivery evidence.

After Fix, the shared mandatory core still applies: Mandatory Adversarial Review then Exact Verify
(`reference/role-loop.md`).

## Circuit breaker

Same error signature 3 times: stop. Record attempts + leading hypothesis in `PLAN.md`, then escalate
with evidence.

## Vault

Every probe result goes to the `PLAN.md` hypothesis ledger. The vault prevents fresh contexts from
re-investigating solved ground.
