# GOAL - Frontier 004 Delivery Stage Enforcement

Make the durable AIDT delivery journal, rather than worker prompts or editable card state, the sole authority for
advancing an issue through the full delivery sequence.

## Success Criteria

- [ ] The enabled profile exposes exactly Intake, Route, Plan, Plan Approval, Worktree, Build, Review, Local QA,
  Commit, Merge, Deploy, Dev QA, Learn, plus Done and explicit failure/review terminals.
- [ ] One deep `AidtDeliveryController` interface (`publish`, `apply`, `snapshot`) rejects every skipped or undeclared
  transition and returns durable sanitized blocking reasons.
- [ ] Evidence is append-only and binds issue revision, plan hash, stage epoch, relevant SHA chain, proof
  command/result/time, and side-effect review.
- [ ] Every issue waits for explicit human plan approval bound to its exact issue revision and canonical plan hash;
  infrastructure approval cannot satisfy it.
- [ ] Low-confidence planning receives at most three durable fresh-context attempts per issue revision, then enters
  Human Review without a fourth dispatch.
- [ ] One durable `(service, environment)` fence serializes Merge through successful Dev QA and survives restart.
- [ ] Restart, concurrent reconciliation, projection drift, malformed records, and ambiguous external outcomes fail
  closed; generic auto-merge remains disabled.
- [ ] Board/detail/history projections show approval, freshness, binding, and blocking reasons without exposing
  secrets or turning the HTTP surface into a generic evidence-authoring endpoint.

## Scope

This frontier owns the closed AIDT v1 stage graph, typed records, durable journal, transition decisions, projection
repair, low-confidence attempts, plan approval, promotion fence, minimal API/history projection, and orchestrator
authorization seam. It does not execute QA, Git promotion, Jenkins, Jira writes, or dev-environment actions.
