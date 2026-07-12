# dashboard - data-dense business app overlay

Load at Build when the read is a data-dense business application: admin panel, analytics dashboard, internal tool, console, SaaS back-office, data table at scale. An APPLICATION-TYPE overlay (like `engagement.md`), not an aesthetic family: it overrides dials + layout assumptions for the medium already chosen (`web.md`/`mobile.md`) and pairs with `taste-core.md` (every universal ban still holds). Gate-compatible by construction: off-black not pure `#000`, neutral + one non-purple accent, tabular numerals, restrained motion, ASCII only.

## When to load + dials

Load it when the brief is about operating on data, not converting a visitor. Signals: "dashboard / admin / analytics / console / internal tool / back-office / ops / data table / KPI / metrics".

Dials override the landing baseline (`8/6/4`): set `DESIGN_VARIANCE 3-5 / MOTION_INTENSITY 2-3 / VISUAL_DENSITY 6-8`. Low variance (predictable chrome, the work area is the star), low motion (data must not jitter or distract), high density (trusted power users want more per screen, not macro whitespace). This is the inverse of the marketing families.

Reframe: there is NO hero. The landing rules (centered hero, hero-fits-viewport, logo wall, 1-3-word CTA, eyebrow/zigzag heuristics) do not apply here; do not fire them. The overview opens with an app shell + a KPI summary row, top-left = the single north-star metric the user checks daily.

## App shell

- Layout: collapsible icon-rail sidebar (primary nav) + topbar (breadcrumbs, global search, account, theme/density) + scrollable content region. shadcn `SidebarProvider`/`SidebarInset` is the canonical composition (sidebar-07 icon-collapse, dashboard-01); put the provider in the root layout so collapse state persists across routes.
- Nav recedes: dim/desaturate chrome so content leads (Linear). Breadcrumbs in the topbar for deep trees; never bury actions in mouse-only menu trees.
- Responsive collapse: rail collapses to icons, then to an off-canvas drawer below the breakpoint. Mobile = essential KPIs + collapsible nav, not a shrunk desktop grid; tables become cards or horizontal-scroll with a frozen first column.
- Command palette (Cmd/Ctrl+K): `cmdk` (powers Linear/Vercel; shadcn `Command` wraps it; Mantine Spotlight is the batteries version). Covers nav AND actions AND record search. Instant focus, arrow/Enter/Escape + a visible close button, recents by default, fuzzy + typo-tolerant, virtualize long result lists. Expected primitive in any power-user tool.
- Keyboard nav: focus order follows the visual grid, roving tabindex inside tables/grids, visible focus ring (WCAG 2.2), shortcuts discoverable (palette lists them). No pointer-only actions.

## Information hierarchy + density

- Hierarchy via type scale + area + contrast, not color (Stripe: ~6 type sizes, color reserved for status). Largest cell/number = most important (Carbon contrast-and-area).
- Overview vs detail: the overview answers "is anything wrong" at a glance (5-7 primary KPIs, north-star top-left); detail/drill-down lives one click away (progressive disclosure). Separate the "doing" dashboard from the "analyzing" one.
- Density as a setting: comfortable/compact toggle on a 4px base unit, subtract in 4px steps (AWS Cloudscape model). Drive row height + padding from spacing tokens, never hardcoded px; persist the user's choice; default to comfortable. Carbon row ladder: compact 24 / short 32 / default 48 / tall 64 px (tall only for 2-line content).
- Bento overview grid: exact cell count, varied cells, one radius; largest cell = the metric that most often reveals an anomaly.

## Data tables

- Headless first: TanStack Table (you own the markup, pair it with the alignment/density rules). MUI X DataGrid only inside an MUI app; AG Grid for enterprise grids (pivot/group/aggregate) - client-side ~100k rows in memory, beyond that use its Server-Side Row Model with server aggregation. Theme any of them; never ship the default Alpine/blue/MUI look.
- Virtualize once a list exceeds ~50 unpaginated rows (TanStack Virtual): render ~20-40 visible, overscan 5-10 (NOT 25 - cost, no gain), prefer uniform row height.
- Column rules (Pencil & Paper): left-align text, right-align numbers/currency, align decimals; `font-variant-numeric: tabular-nums` on every numeric column so digits stay equal-width. Header carries the unit; do not repeat the same word in every cell. Hairline row dividers, no thick dark vertical separators; if zebra striping, do not let it fight hover/selected/focus row states.
- Behaviors: sticky header + sticky first column, sort, faceted filters, column pin/resize/show-hide, density toggle (ProTable bundles search + column config + density as one unit), row-select + bulk actions, a reset-to-default and state preservation.
- URL-as-state: keep sort/filter/pagination in URL search params so a view is shareable and back-button-safe.

## Charts / data-viz (pick by data scale, then retheme)

- Recharts - React/SVG default for small-to-medium KPI cards; drop past a few thousand points.
- Tremor - fastest scaffolding in a Tailwind stack (built on Recharts, so same SVG ceiling); cleanest open vocabulary for KPI-card anatomy. Retheme it, never ship stock.
- Nivo / visx - polished many-type SSR (Nivo, SVG->Canvas as data grows) / bespoke D3 control (visx) when off-the-shelf cannot express the viz.
- Apache ECharts - Canvas/WebGL for big-data, real-time, drill-down, composites; lazy-load (heavy).
- uPlot - fastest time-series (millions of points, streaming/observability); lower-level, manual a11y.
- Victory - built-in ARIA + keyboard nav; pick when chart a11y is a hard requirement or shared with React Native.
- Observable Plot - layered grammar-of-graphics for analyst exploration; wrap its output (not React-native).
- Restraint: high data-ink ratio, direct labeling over legends, no chartjunk (no 3D/gauges/speedometers, no heavy gridlines, no per-card drop shadows, no rainbow series). Pie max 3 slices; bars ordered by value not alphabetically. Every chart needs a data-table fallback + ARIA (WCAG 1.4.1).

## KPI cards, filters, status color

- KPI/stat card anatomy: label + big tabular value + delta-vs-baseline (or target) + sparkline. A number with no delta/target/benchmark is "just a number". 5-7 primary KPIs max (>12 drops engagement); gauges are deprecated.
- Filters/facets/saved views: one GLOBAL filter set re-scopes every panel (Datadog); offer saved/named views and a clear-all.
- Status semantics, colorblind-safe: color carries meaning ONLY (status/severity), everything else neutral, so an error color genuinely means something (Sentry). Use Okabe-Ito / Wong / ColorBrewer; prefer blue-orange for up/down over red-green; 6 categorical max (8 cap). ALWAYS a redundant non-color cue (icon/shape/label). Status color fails ~8% of men and breaks in grayscale if it is the only signal.

## States for data (per-widget, not full-page)

- Loading: content-shaped skeleton sized to the final content, no layout shift; not a full-page spinner for one slow widget.
- Empty = onboarding: one purpose line + the single first action (a clickable starter, not a blank panel). Distinguish zero-data from no-permission from error - three different messages.
- Error = recovery: plain-language cause + inline retry, never a raw stack trace, never a full-page error for one failed panel.
- Real-time vs snapshot: live surfaces need a last-updated / live-tick freshness indicator; animated counters use `tabular-nums`. Never show demo/mock data as if it were real - label any mock.

## Embedded AI panel (when the brief asks for one)

Side panel/drawer keeping data context visible. Mandatory states: token streaming + stop/cancel, reasoning/tool-call disclosure, source citations linking back to the rows/charts used, undo on any data action, optimistic actions reconciled with rollback. Suggestion pills seed the next query. (Cross-ref `taste-core.md` AI-tell bans; Vercel AI Elements is the build kit.)

## Enterprise design systems (lean on one; never mix)

- IBM Carbon - the most explicit density + data-viz spec (row-height ladder, 4px mini-unit grid).
- Ant Design (+ Pro) - broadest component surface + deepest Table; retheme to escape the default blue.
- Microsoft Fluent 2 - Microsoft/Office-aligned products.
- AWS Cloudscape - formal comfortable/compact density foundation.
- Atlassian / Base Web / Geist - token scales + dark-first numeric styling.
When the brief maps to one, use it whole; do not mix Carbon tables with AntD inputs and an MUI grid so spacing/tokens fight.

## Dashboard anti-patterns to ban

- Marketing-hero thinking on a data app (centered hero, hero-fits-viewport, logo wall, 1-3-word outcome CTA, a single marketing stat forced onto an ops console).
- KPI overload (>12, no dominant metric, equal-weight grid with nowhere for the eye to land, cards-in-cards).
- Chartjunk: 3D bars/pies, gauges/speedometers, decorative gradients, drop shadows on every card, rainbow series, pie >3 slices, alphabetical bar order.
- Wrong renderer for scale (SVG for millions of points; 10k+ rows with no virtualization; overscan 25+).
- Color as the only signal (red-green-only status, no redundant icon/shape/label, charts with no ARIA / no data-table fallback).
- Proportional (non-tabular) numerals + center/left-aligned numbers (columns jitter, magnitude unscannable).
- Bad dark mode: pure `#000` + pure `#fff`, single flat surface, drop-shadow elevation that does not read (also trips the gate's pure-black ban).
- Missing/conflated data states; stock-template look (default colors/blue/Inter/spacing); mixing two design systems; hidden nav with no palette or breadcrumbs.

## Dark-first elevation

Off-black canvas (tint, e.g. `#0B0D0F` / `#101114`, never `#000`). Depth by lightness: 4+ tinted surface elevations + hairline borders + soft glow - drop shadows do not read on dark. One non-purple accent, status color rationed. Ship a tested light mode too; dark via token swap, never a late CSS invert. (Datadog, Vercel Geist, Sentry, Linear.)

## Pre-flight delta (dashboard-specific fails)

Fail: any marketing hero on a data app; >12 KPIs or no north-star top-left; a KPI value with no delta/target; chartjunk (gauges/3D/rainbow); a chart with no ARIA or data-table fallback; non-tabular numerals or center-aligned number columns; status conveyed by color alone; tables >50 rows with no virtualization/pagination; missing/conflated loading-empty-error states; stock template look or two design systems mixed; pure `#000`/`#fff` dark mode (also a hard gate fail).
