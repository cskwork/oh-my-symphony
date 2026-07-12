# trend-research - Trend pulse

Make the design current without chasing fads. Live by default, snapshot on fallback.

## Reuse before searching

A dated `trend-pulse.md` for the same kind+vibe, <=30 days old, may be reused: note `reused <date>` instead of re-searching. Different kind, different vibe, or older -> pulse fresh.

## Default: live search

1. `WebSearch` 2-3 queries scoped to the brief:
   - `"<kind> design trends <year>"` (e.g. "SaaS landing design trends 2026")
   - `"<vibe> UI <year>"` or `"<competitor/brand> redesign"`
   - medium-specific when native: `"iOS <year> design"`, `"Material 3 expressive"`.
2. Read 2-4 results. Extract concrete, implementable signals (a specific material, layout, type treatment, motion), not vague adjectives.
3. Keep only trends that serve the intent. Drop anything the brief does not want. A trend that fights the audience/constraint is slop.

## Fallback: snapshot

If search is unavailable/blocked/offline: use `reference/trend-snapshot.md` (dated). Tell the user the snapshot date and that it may be stale; offer to re-pulse when search returns.

## Record (always)

Write `trend-pulse.md` from `templates/trend-pulse.md`: date, queries run (or "snapshot fallback"), 2-4 chosen trends with a one-line reason each, the lane picked for Direction. Dating is mandatory so a later read knows the freshness.

## Evaluate

- Prefer trends with a real mechanism you can build (a CSS technique, a layout, a motion), not a screenshot vibe.
- Cross-check against `taste-core.md` bans - a trending pattern that is also a banned AI-tell loses.
- One lane drives the surface. Note runner-up lanes for EXPLORE, do not blend.
