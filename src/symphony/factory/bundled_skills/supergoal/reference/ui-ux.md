# UI/UX overlay - design authority

Load whenever the objective ships user-facing UI. The **Expressive/polished baseline is the default for
ALL user-facing UI** - `reference/taste-skill-v2.md` is always the authority. There is no separate
"Functional" tier you classify down into that ships a plainer result. Pure non-visual work (lib, API, CLI
without TUI) loads neither.

| Surface | Authority |
|---|---|
| ANY user-facing UI - landing, portfolio, app, tool, dashboard, form, "make X" | **Expressive baseline** - `reference/taste-skill-v2.md` (always) |
| ...AND the surface is dense admin/dashboard/data-table | + `reference/functional-ui.md` density add-on, layered on top |

`reference/functional-ui.md` is NOT an alternative to Expressive and never lowers polish - it only adds
density discipline (type/spacing/density scale) and complete UI states (loading/empty/error/disabled) on
top of the Expressive baseline for information-dense surfaces. Marketing aesthetic (hero imagery, heavy
motion) is dialed down for dense surfaces via the Plan dials below, not by skipping design authority. A
mixed product still applies the Expressive baseline everywhere and adds the density layer only to its
dense surfaces. The rest of this file describes the Expressive authority.

The chosen authority overlays the normal mode; phases, gates, vault, and topology stay unchanged. UI
scaffolding may fan out, but one surface's look-and-feel is deep-and-narrow: use one Designer driver
per surface.

## Localized UI copy

Visible copy is part of UI quality, not a translation afterthought. For non-English UI, rewrite the
message in the target language's natural product rhythm instead of line-by-line translating the English.
Check deliberate line breaks (`<br>`) as content: Korean should prefer complete, action-oriented
sentences over noun-fragment stacks such as "필수 서비스 없음. Board/TUI는 선택."

| Phase | Overlay |
|---|---|
| Frame | Add one-line **Design Read** and dials `DESIGN_VARIANCE`, `MOTION_INTENSITY`, `VISUAL_DENSITY` to `PLAN.md`. Pick official design system vs aesthetic. If the Design Read vibe names a specialized aesthetic, also pick at most ONE **aesthetic family** from `reference/taste-aesthetics.md` (selection map there) and record it on the Design Read line; no family signal means base taste-skill-v2 alone. If the Design Read names a primary user action (sign up/buy/book/subscribe/install), flag the **engagement** overlay (`reference/engagement.md`) on the Design Read line; editorial/portfolio/docs leave it off. For known brands, use existing brand color/type as accent source and record it. |
| Build | Dispatch **Designer** (`agents/designer.md`) with `PLAN.md` + `reference/taste-skill-v2.md` + (if a family was chosen) its profile from `reference/taste-aesthetics.md` + (if a primary action was flagged) `reference/engagement.md`. Enforce anti-default rules, real/generated images, reduced-motion fallback, hard visual bans, locked accent, no off-brand gradient/glow slop, and no self-approval. With a family: commit to that one, apply its bans; the family overrides base aesthetic defaults where they conflict, base universal rules still hold. |
| QA | Run taste Pre-Flight beside normal QA, plus the chosen family's Pre-Flight delta. Required: a11y, reduced motion, Color Consistency Lock, LILA rule, dark/light or justified single-mode lock. Record `UI-tier: Expressive` (and the family, if any) in `QA.md` `## QA` and enumerate every text/bg pair to `<vault>/qa/contrast-pairs.json`; `qa-gate.sh` runs `node templates/contrast-gate.mjs` on it (completeness critic audits the pair list). Any fail rewinds to Build and blocks delivery. |

Progressive disclosure:

- Conductor does not load `taste-skill-v2.md` into its own context.
- At Frame, load only the sections needed for Design Read/dials/system choice.
- The Build Designer loads the full file in its own context, plus the one selected family profile from `taste-aesthetics.md` if Frame chose one, plus `reference/engagement.md` if Frame flagged a primary action.
- Verify still gates delivery.
