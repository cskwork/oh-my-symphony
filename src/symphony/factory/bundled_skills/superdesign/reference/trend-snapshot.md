<!--
DATED OFFLINE FALLBACK. Used only when WebSearch is unavailable in the Trend pulse step.
Refresh: re-run live search, replace the lanes below, bump the banner date.
A stale snapshot is a fallback, not the source of truth - live search wins when available.
-->

# trend-snapshot - dated fallback

**Snapshot date: 2026-06.** If today is much later, warn the user this may be stale and prefer live search.

Pick the ONE lane that fits the brief. These are implementable lanes, not a checklist to combine.

| Lane | Read it for | Build it with | Watch out |
|---|---|---|---|
| Liquid Glass / refined glassmorphism | premium consumer, Apple-adjacent, media | layered frosted panels, inner border + soft shadow, specular highlight, content-aware blur | reduced-transparency fallback; not generic glass everywhere; web is an approximation of Apple's native material |
| Bento grid | feature overview, product surfaces, dashboards-lite | exact cell count, varied cell content (image/pattern/tint/stat), one radius | no empty cells, no repeated one-sided rows |
| Kinetic / expressive typography | agency, editorial, launch, brand | variable fonts, scroll-linked weight/size, text mask, gradient stroke | keep readable; one hero treatment, not every line |
| Editorial minimalism | calm, trust-first, Linear-tier, content-led | macro whitespace, restrained palette, strong type scale, hairline dividers | scarce color; motion near-invisible |
| Spatial / depth | immersive product, 3D, hardware, games | parallax layers, subtle 3D tilt, depth shadows, Three.js below fold | lazy-load heavy canvas; GPU-safe motion |
| Material 3 Expressive (Android/cross) | Google-aligned, mobile-first | M3 expressive tokens, dynamic color, spring motion, large shape families | use the system, do not hand-recreate |
| Tactile neo-brutalism | playful, indie, dev-tool, anti-corporate | hard borders, flat blocks, raw type, offset shadows, high contrast | one accent; keep a11y contrast |
| Dark + vivid accent | dev-tool, fintech, AI product | off-black canvas, one vivid accent, mono numerals, glow used sparingly | not AI-purple; off-black not pure black |
| Data-dense business app / console | admin, analytics, internal tool, back-office, ops | app shell (icon-rail sidebar + breadcrumb topbar) + KPI cards + headless data table + chart restraint + density modes + command palette + dark-first; see `dashboard.md` | no marketing hero; tabular numerals; colorblind-safe status; per-widget loading/empty/error states |

## Cross-cutting (apply within the chosen lane, not as a lane)

- Real or generated imagery over div-mockups. Accessibility and reduced-motion are non-negotiable in every lane.
- Variable fonts and `clamp()` fluid type are mainstream-safe. Avoid Inter-as-default per `taste-core.md`.
- AI-native surfaces (chat, agents): streaming states, source citations, undo/stop affordances are part of the design, not afterthoughts.
- Engagement mechanics (for SaaS/consumer/commerce/marketing, see `engagement.md`): skeleton + optimistic UI over spinners, native scroll-driven CSS + View Transitions, single-stat/named-claim hero, outcome-led CTA + friction-reducer line, real-data social proof beside the CTA, thumb-zone + haptics on mobile, WCAG 2.2. These are conversion mechanisms, not aesthetic lanes - apply within the chosen lane.
