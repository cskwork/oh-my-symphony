# design-brief - Read phase

Infer the brief before any design. Output one line, then proceed.

## Infer

- **Kind**: SaaS/consumer/agency/event landing, portfolio, dashboard, app screen, marketing, editorial.
- **Primary action**: the one thing the surface should make the user do - sign up, buy, book, subscribe, install, read, explore. Names it -> load `reference/engagement.md` in Build. Content/editorial/portfolio with no conversion goal -> skip it (no forced conversion patterns).
- **Medium**: web, mobile web, native (iOS/Android), cross-platform.
- **Audience**: procurement, consumer, recruiter, public-sector, regulated, developer, kids.
- **Vibe words**: minimalist, calm, Linear, Awwwards, brutalist, premium, playful, serious B2B, editorial, glassy, dark-tech.
- **References**: URLs, screenshots, products, competitors, brand names.
- **Brand assets**: logo, color, type, photography, existing patterns. For known brands, use brand color/type as accent source.
- **Quiet constraints**: a11y, public-sector, trust-first commerce, regulation. These OVERRIDE aesthetics.

## Output

`Reading this as: <kind> for <audience>, <vibe> language, leaning <design system or aesthetic family>.`

## Ask gate

- Ask exactly ONE question only when two plausible reads genuinely diverge (e.g. preserve brand vs start fresh; consumer vs enterprise).
- Resolve anything answerable from provided references/brand/code by reading, not asking.
- If context is enough, do not ask - proceed to Trend pulse.
- Non-interactive run (no user to answer): take the conservative read (preserve brand; the calmer dial row), record the assumption in the brief's open-question line, proceed.

## Anti-defaults (carry into Direction)

No automatic AI-purple gradient, centered dark mesh hero, three equal feature cards, generic glass everywhere, infinite micro-animation, or Inter + slate-900 unless the brief earns it.
