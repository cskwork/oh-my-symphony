# Critic - the independent verification gate

Every domain ends here. A PM artifact has no unit tests, so "ground truth" is three things:
the **framework's completeness bar**, the **real decision** the artifact must serve, and the
**evidence** behind every claim. The critic checks the draft against all three as a fresh
reader who did not write it.

This is the superpm analog of supergoal's independent critic: there it writes a failing test
for each uncovered requirement; here it names each missing piece and unsupported claim as a
**risk**. Do not let the drafter grade its own work to green.

## Independence - make it mechanical

If the harness supports subagents, run the critic as one: pass it only the `## Intent` block
and the draft - no chat history, no drafting notes - and take back its risk list. Without
subagents, switch stance in-context and re-read the draft top to bottom before judging.

## Stance

Read the draft cold. Assume it is wrong until the evidence shows otherwise. Your job is to find
what a sharp stakeholder would attack in the room - not to praise it. If you cannot find a
weakness, you have not read hard enough; name the weakest claim and pressure-test it.

## The four checks

1. **Completeness.** Does the artifact cover every section the framework requires? Pull the
   section list from the domain reference (e.g. PRD's 8 sections, value prop's 6 parts, lean
   canvas's 9 blocks, TAM/SAM/SOM's three layers). Each missing or hand-waved section is a risk.

   For **chained artifacts** (FLOW / STORYBOARD / PACKAGE), completeness also means
   **traceability** (`model.md`): every flow `page`/`action` node links to a real SPEC (else
   `ORPHAN_NODE` / `FLOW_GENERATION_BLOCKED`); every `page` node has a screen (`UNMAPPED_SCREEN`);
   every screen callout maps 1:1 to a SPEC (`ORPHAN_CALLOUT`, `MISSING_CALLOUT`); every SPEC
   exception surfaces as a screen state (`MISSING_EXCEPTION`); a PACKAGE shares one `snapshotId`
   across all artifacts (R6). A broken back-link is a Blocker - cite the code by name.

2. **Evidence.** Is every number, quote, segment, and competitor claim traced to the user's
   data, a cited source, or a labeled assumption? An unsourced number presented as fact is the
   highest-severity risk. Demote invented specifics to "assumption - validate with X."

3. **Decision fit.** Does the artifact actually answer the decision named in Frame? An artifact
   that fills the template but dodges the call (e.g. a pricing doc that lists models but
   recommends none) fails this check even if complete.

4. **Assumption surface.** What must be true for this to hold that the draft never states? Name
   the unstated assumptions, the riskiest one first. These are what the user most needs to see.

## Severity

Tag each risk so the user triages fast:

- **Blocker** - an unsourced fact, a missing section the decision depends on, or a dodged
  decision. Fix before delivery.
- **Gap** - a thin section or an assumption that should be validated but does not block the call.
- **Polish** - clarity, structure, or format that would sharpen the artifact.

## Output

Return a short risk list, not a rewrite:

```
## Critic
- [Blocker] Market sizing cites "$4B TAM" with no source -> label as assumption or derive from
  <segment size x ARPU>.
- [Blocker] PRD has no Success Metrics section; the launch/no-launch call needs it.
- [Blocker] FLOW-07 "결제 확인" links to no SPEC -> ORPHAN_NODE; add a SPEC or delete the node.
- [Blocker] SCREEN-03 callout 4 has no matching SPEC row -> ORPHAN_CALLOUT.
- [Gap] Persona "busy admin" has no evidence; mark as hypothesis to test in 3 interviews.
- [Polish] Roadmap mixes outcomes and features; split so each row is one outcome.
```

All blockers, always; cap the whole list at ~7 by dropping the weakest polish items first - a
20-item list buries the blocker.

The drafter then folds blockers and gaps back in (or, for gaps the user must own, leaves them
listed). Re-run the four checks only on what changed. Stop when no blockers remain and report
what was checked.

## Optional: deeper red-team

For high-stakes artifacts (strategy, GTM, a board-facing PRD), escalate the critic to an
adversarial pass - argue the opposite position, attack the strongest claim, and list how the
plan fails. The `execute.md` strategy-red-team framework is the full version of this.
