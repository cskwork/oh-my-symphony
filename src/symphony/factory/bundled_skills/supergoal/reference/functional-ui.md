# Functional UI - density + states overlay for dense/admin surfaces

Load when the surface is a dashboard, data table, admin panel, internal tool, settings, wizard, or
CRUD form. Layered on top of the Expressive baseline (`reference/taste-skill-v2.md` stays the polish
authority); never an alternative tier, never lowers polish. Mixed product: Expressive everywhere,
this overlay only on the dense surfaces.

Relative to taste-skill-v2 it suppresses landing-only rules (hero, marquee, scroll-choreography,
anti-slop landing) and adds density discipline plus complete UI states. Phases, gates, vault, topology
unchanged. One Designer driver per surface.

## Non-negotiable baseline

- **Design system.** Pick exactly one and do not hand-roll or mix. Map the brief:
  enterprise SaaS -> Fluent; analytics/B2B -> Carbon; Material product -> Material 3;
  Shopify admin -> Polaris; Atlassian/Jira -> Atlaskit; accessible React base -> Radix Themes;
  owned modern SaaS -> shadcn/ui. (Same table as taste-skill-v2 section 2.)
- **Contrast computed, not eyeballed.** Body AAA (>=7:1), all other text AA (>=4.5 normal, >=3 large)
  on its actual background. Enumerate pairs to `<vault>/qa/contrast-pairs.json`; gate computes them.
- **One accent, one type scale, one spacing scale, one radius system.** Consistent across every view.
- **All UI states implemented.** loading (skeleton), empty, error, focus, hover, active, disabled.
  Tables also need: empty state, loading state, error state, pagination/sort affordance.
- **Density first.** `VISUAL_DENSITY` high (7-10): tight rows, mono/tabular numbers, lines over cards.
  Cards only when elevation conveys hierarchy.
- **Motion minimal.** `MOTION_INTENSITY` low (1-3): hover/active/feedback only. No choreography,
  parallax, or perpetual loops. Any motion above 3 honors `prefers-reduced-motion`.
- **Theme declared.** Set `color-scheme`; ship both light/dark tokens or a justified single-mode lock.
- **No empty decoration.** Every element carries data or a labeled control. No placeholder panels.

## Data-app techniques (dashboards, analytics, consoles, admin at scale)

Density-first depth for a real data app. No marketing hero: the landing heuristics (centered hero,
hero-fits-viewport, logo wall, 1-3-word CTA, eyebrow/zigzag) do not apply here - do not fire them. Keep
the baseline's density-first dials (high `VISUAL_DENSITY`, low `MOTION_INTENSITY`, low `DESIGN_VARIANCE`).
Open with an app shell + a KPI summary row, north-star metric top-left. Every universal ban still holds.

- **App shell.** Collapsible icon-rail sidebar + topbar (breadcrumbs, global search, account,
  theme/density) + scrollable content; persist collapse across routes. Command palette (Cmd/Ctrl+K,
  `cmdk`) covering nav + actions + record search. Roving tabindex inside tables; visible focus ring; no
  pointer-only actions.
- **Data tables.** Headless-first TanStack Table (own the markup). MUI X DataGrid only inside an MUI app;
  AG Grid for enterprise grids (pivot/group/aggregate; server-side row model past ~100k rows). Virtualize
  past ~50 unpaginated rows (overscan 5-10, not 25). Left-align text, right-align numbers,
  `font-variant-numeric: tabular-nums` on every numeric column. Sticky header + first column; sort,
  faceted filters, column pin/resize/show-hide, row-select + bulk actions. Keep sort/filter/pagination in
  URL params (shareable, back-safe). Never ship the default Alpine/MUI look.
- **Charts (pick by data scale, then retheme).** Recharts/Tremor for small-medium KPI cards (SVG, drops
  past a few thousand points) -> Nivo/visx for many-type or bespoke D3 -> ECharts/uPlot (Canvas/WebGL) for
  big-data, real-time, or streaming (lazy-load, heavy). Every chart needs a data-table fallback + ARIA
  (WCAG 1.4.1). High data-ink ratio, direct labels over legends, no chartjunk (no 3D/gauges/rainbow); pie
  max 3 slices; bars ordered by value.
- **KPI cards.** label + big tabular value + delta-vs-baseline (or target) + sparkline. A number with no
  delta/target is "just a number". 5-7 primary KPIs max; gauges are deprecated.
- **Status color, colorblind-safe.** Color carries status/severity meaning only; everything else neutral.
  Okabe-Ito / Wong palette; prefer blue-orange over red-green; ALWAYS a redundant non-color cue
  (icon/shape/label); 6 categorical max. Color-only status fails ~8% of men and breaks in grayscale.
- **Per-widget states.** Skeleton sized to the final content (no full-page spinner for one slow widget);
  empty = one purpose line + the first action (distinguish zero-data / no-permission / error); error =
  plain cause + inline retry. Live surfaces show a last-updated / freshness tick; animated counters use
  `tabular-nums`. Never show mock data as if it were real - label any mock.
- **Dark-first elevation.** Off-black canvas (e.g. `#0B0D0F`, never `#000`); depth by lightness (tinted
  surface elevations + hairline borders + soft glow), not drop shadows. Ship a tested light mode via token
  swap, never a late CSS invert.
- **Enterprise systems (lean on one, never mix).** Carbon (most explicit density + data-viz spec), Ant
  Design Pro (broadest tables), Fluent 2 (Microsoft/Office), Cloudscape (formal comfortable/compact). Use
  one whole; do not mix Carbon tables with AntD inputs and an MUI grid so spacing/tokens fight.
- **Ban:** marketing-hero thinking on a data app; KPI overload (>12, no dominant metric, equal-weight
  grid); chartjunk; wrong renderer for scale (SVG for millions of points, 10k+ rows with no
  virtualization); color-only status; proportional or center-aligned number columns; tables >50 rows with
  no virtualization/pagination; missing or conflated loading/empty/error states; two design systems mixed;
  pure `#000`/`#fff` dark mode.

## Frame overlay

Add a one-line **Design Read** (surface kind + chosen design system) and the three dials
(`DESIGN_VARIANCE` low/symmetric, `MOTION_INTENSITY` 1-3, `VISUAL_DENSITY` 7-10) to `PLAN.md`.

## Build overlay

Dispatch **Designer** with `PLAN.md` + this file as the authority. Enforce the baseline above.
No self-approval.

## QA overlay

Run beside normal QA: a11y (keyboard focus, labels), reduced motion, all UI states present, density
matches dials, `color-scheme` declared. Record `UI-tier: Functional` in `QA.md` `## QA` and
enumerate every text/bg pair to `<vault>/qa/contrast-pairs.json`; `qa-gate.sh` runs
`node templates/contrast-gate.mjs` on it (completeness critic audits the pair list). Any fail rewinds
to Build and blocks delivery.
