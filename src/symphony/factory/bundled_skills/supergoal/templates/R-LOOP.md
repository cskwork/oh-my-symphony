# R-LOOP - verifier -> implementer loop channel

The verifier APPENDS one timestamped section per failed verification pass; the relaunched implementer
reads `PLAN.md` plus ONLY the latest section here. Never edit older sections; never delete this file.
A regressed previously-green criterion is unticked in `GOAL.md` and listed first.

## <YYYY-MM-DDTHH:MM> iteration <n>

- [ ] <missing/broken item: GOAL.md criterion #, expected vs actual, evidence path>
Regression: <previously-green criterion now red, or "none">
Next: <smallest fix the implementer should attempt>
