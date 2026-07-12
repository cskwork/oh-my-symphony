# Domain rules - priority digest

For domain-specific work, carry <=10 abstract priority rules for the task's domain(s). They guide
Frame/Build/Verify quality only. They are advisory and never replace hard gates: the project's REAL
tests, the QA gates, and the read-only DB rule still decide.

## Produce at Frame

1. **Route.** Name the domain(s) the objective touches (e.g. web-design, payments, data pipeline).
   Multiple domains may stack.
2. **Distill.** Write <=10 one-line principle rules total - from the project's own docs/code first,
   then general domain knowledge. Keep them abstract, not task steps.
3. **Record once.** Add `## Priority Rules` to the run vault `PLAN.md`; keep them in the
   conductor's context.

## Apply per role

Inject only the role-relevant subset into each role prompt. Do not widen vault read scope.

- Build / Fixer: honor the rules while implementing.
- Critic: flag violations as findings; derive edge-case tests from them.
- Verify: derive a concrete coverage checklist from the same domain routing - UI means
  a11y/responsive/error states; data pipeline means idempotency/schema/PII; API means error paths and
  auth ordering; security-sensitive input means bypass families. Report each item as
  verified-with-evidence, or list it under `Not covered:` with justification.

## Conservative updates

Refine only when the objective or plan materially changes. Keep rules abstract and log the reason in
the run `PLAN.md` as `RULES-UPDATE:`.

## Format

```md
## Priority Rules
Domain(s): <e.g. web-design + ecommerce-retail>
1. <abstract one-line rule>
...
10. <abstract one-line rule>
```
