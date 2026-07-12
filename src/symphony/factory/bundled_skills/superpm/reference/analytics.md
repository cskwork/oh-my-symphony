# ANALYTICS - data analytics

Frameworks for turning a question into a defensible read of the data. The hard rule: state the
assumptions behind every number, and never report significance you did not actually compute.

## sql-queries (NL -> SQL)

Translate a plain-language question into a correct query for the target dialect (BigQuery,
PostgreSQL, MySQL). Steps:

1. **Pin the question** - the exact metric, grain, time window, and filters.
2. **Confirm the schema** - read the real table/column names from the user's schema; never guess
   column names. If the schema is unknown, ask for it or for a sample.
3. **Write the query** - dialect-correct (date functions, window functions, `QUALIFY` vs
   subquery differ by engine). Prefer explicit `JOIN`s and CTEs for readability.
4. **State assumptions** - dedup logic, timezone, how nulls and bots are handled.

Output: the query + a one-line statement of what it returns and the assumptions baked in. Flag
anything that needs validation against the real schema.

## cohort-analysis

Group users by a shared start event (signup week/month) and track a metric over subsequent
periods.

- **Retention curve** - % of each cohort active in period N; read the flattening point (the
  "smile" or its absence).
- **Engagement / adoption** - feature adoption or usage depth by cohort.
- **Read it:** compare cohorts to see if product changes moved retention; distinguish a real lift
  from seasonality. Output: the cohort table/curve + what changed between cohorts and the
  likely cause, with confounders named.

## ab-test-analysis

Read an experiment to a ship / extend / stop decision.

- **Pre-checks:** was the sample size adequate (powered for the MDE)? did it run full business
  cycles? is the split balanced (sample-ratio mismatch)?
- **Significance:** report the metric delta, the p-value or confidence interval, and the
  practical effect size - not just "stat-sig". Beware peeking and multiple-comparison inflation.
- **Decision:** ship (significant + meaningful + no guardrail regressions), extend (trending but
  underpowered), or stop (flat or negative). State which.

Output: the result table + the decision + the risks (novelty effect, guardrail metrics, segment
heterogeneity). Do not declare a winner on an underpowered or peeked test.

## Critic completeness anchors

SQL is dialect-correct against the real schema with assumptions stated; cohort reads name
confounders; A/B reads include a power/sample check and end in an explicit ship/extend/stop call,
never significance that was not computed.
