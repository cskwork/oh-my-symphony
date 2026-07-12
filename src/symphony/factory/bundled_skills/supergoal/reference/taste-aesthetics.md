<!--
COMPRESSED DERIVATIVE - taste-skill specialized aesthetic families.
Selectable overlays for supergoal's Expressive UI tier, loaded on demand by reference/ui-ux.md.

Source:  https://github.com/leonxlnx/taste-skill  (paths: skills/minimalist-skill, skills/soft-skill, skills/brutalist-skill)
Commit:  3206dd44fc  (upstream 2026-03-20)
Pulled:  2026-06-07   License: MIT (upstream LICENSE)
Compressed: 2026-06-07 for agent context cost.

To refresh: pull upstream, replace the affected profile body, keep the names + contract below.
-->

> Not installable. Reference material for supergoal's Expressive UI tier.

# Aesthetic families - selectable Expressive overlays

Pick at most ONE family when the Design Read names its vibe. A family OVERLAYS base
`reference/taste-skill-v2.md`; it does not replace it.

- Commit to one family per surface; never mix two families.
- Base universal rules still hold: em-dash ban, real/generated images (no div fake screenshots),
  hero fits viewport, WCAG AA contrast, `prefers-reduced-motion`, GPU-safe motion
  (`transform`/`opacity` only), no manual scroll listeners, no AI-placeholder names/cliches.
- Where a family's type/color/geometry rule conflicts with a base aesthetic default, the FAMILY
  wins (e.g. base discourages serif, but minimalist/editorial families use it deliberately).
- No family signal -> use base taste-skill-v2 alone (the mainstream landing default).

Selection map (Design Read vibe -> family):

| Vibe words in the Design Read | Family |
|---|---|
| minimalist, clean, calm, Linear, Notion, document, editorial, warm monochrome | `minimalist-ui` |
| premium, luxury, expensive, Apple-y, Awwwards, agency, "$150k", cinematic, glassy | `high-end-visual-design` |
| brutalist, industrial, Swiss, terminal, telemetry, blueprint, declassified, raw/technical | `industrial-brutalist-ui` |

---

## minimalist-ui - premium utilitarian / editorial document UI

When: clean editorial interfaces (Notion/Linear vibe), warm monochrome, typographic contrast,
flat bento, muted pastel spot accents.

Dials (taste-skill-v2 section 1): `DESIGN_VARIANCE 5-6`, `MOTION_INTENSITY 3-4`, `VISUAL_DENSITY 2-3`.

- Type: editorial serif headings (Lyon Text / Newsreader / Playfair / Instrument Serif), tracking
  `-0.02..-0.04em`, line-height `1.1`; geometric/system sans body (SF Pro / Geist / Switzer);
  mono for meta (`<kbd>`, code). Body never `#000` - off-black `#111` / `#2F3437`, line-height `1.6`,
  secondary `#787774`.
- Color: warm-mono canvas `#FFFFFF` / `#F7F6F3`; borders ultra-light `#EAEAEA` (`rgba(0,0,0,.06)`).
  Color is scarce; accents only as washed pastels (pale red/blue/green/yellow with matched text).
- Layout: bento cards `border:1px solid #EAEAEA`, radius `8-12px` max, generous padding `24-40px`;
  macro-whitespace `py-24/py-32`; content width `max-w-4xl/5xl`. CTA solid `#111` text `#fff`,
  radius `4-6px`, no shadow. FAQ = `border-bottom` dividers, no boxes.
- Motion: invisible. Scroll entry `translateY(12px)+opacity` over `600ms cubic-bezier(.16,1,.3,1)`
  via IntersectionObserver; staggered `--index*80ms`; hover lift to `0 2px 8px rgba(0,0,0,.04)`.
- Icons: Phosphor (Bold/Fill) or Radix, one stroke weight. Imagery desaturated + warm grain `0.04`.
- Pre-Flight delta (fail if present): Inter/Roboto/Open Sans; Lucide/Feather/Heroicons; gradients,
  neon, glassmorphism (beyond subtle navbar blur); `shadow-md/lg/xl` or shadow opacity `>=0.05`;
  `rounded-full` on large containers/cards/primary buttons; primary-colored hero/section blocks.

## high-end-visual-design - Awwwards-tier agency premium

When: "make it feel expensive / $150k agency / Apple-esque / Linear-tier" premium marketing.

Dials: `DESIGN_VARIANCE 7-9`, `MOTION_INTENSITY 7-9`, `VISUAL_DENSITY 3-4`. Vary archetypes; never
repeat the same layout/aesthetic twice in a row.

- Pick 1 vibe archetype: Ethereal Glass (OLED `#050505`, subtle radial-mesh orbs, vantablack cards
  `backdrop-blur-2xl`, white/10 hairlines); Editorial Luxury (warm cream `#FDFBF7`, variable serif
  display, film-grain `opacity-.03`); Soft Structuralism (silver/white, bold grotesk, diffused
  ambient shadows). Pick 1 layout archetype: Asymmetrical Bento, Z-Axis Cascade, or Editorial Split.
- Type: Geist / Clash Display / PP Editorial New / Plus Jakarta Sans. Ultra-light icon lines
  (Phosphor Light / Remix Line).
- Cards: Double-Bezel (Doppelrand) - outer shell (`ring-1 ring-black/5`, `p-1.5/2`, `rounded-[2rem]`)
  + inner core (own bg, `shadow-[inset_0_1px_1px_rgba(255,255,255,.15)]`, concentric
  `rounded-[calc(2rem-.375rem)]`). CTA = pill `rounded-full px-6 py-3` with button-in-button trailing
  icon in its own `rounded-full` circle. Eyebrow = micro pill `text-[10px] uppercase tracking-[.2em]`.
- Spacing: macro-whitespace `py-24..py-40`.
- Motion: spring physics, custom `cubic-bezier(.32,.72,0,1)` ~700ms+; fluid-island nav + hamburger
  morph to X; staggered mask reveals; magnetic hover (`active:scale-[.98]`, inner icon translate);
  scroll entry fade-up+blur via IntersectionObserver/`whileInView`. `backdrop-blur` only on
  fixed/sticky; grain on fixed `pointer-events-none` layer.
- Mobile: asymmetric layouts collapse to `w-full px-4 py-8` below 768px; remove rotations/overlaps;
  `min-h-[100dvh]` not `h-screen`.
- Pre-Flight delta (fail if present): Inter/Roboto/Arial/Open Sans/Helvetica; thick Lucide/FontAwesome/
  Material icons; generic 1px solid gray borders; harsh dark shadows (`shadow-md`, `rgba(0,0,0,.3)`);
  edge-to-edge sticky navbar; symmetric Bootstrap 3-col grid w/o whitespace; `linear`/`ease-in-out`
  or instant state changes; any element appearing statically on load; flat (non-nested) premium cards.

## industrial-brutalist-ui - Swiss print / tactical telemetry

When: data-heavy dashboards, portfolios, or editorial that should read like declassified blueprints
or a mainframe terminal. Raw, mechanical, high-density.

Dials: `DESIGN_VARIANCE 7-9`, `MOTION_INTENSITY 2-4` (analog texture, not choreography),
`VISUAL_DENSITY` bimodal - telemetry `7-9`, Swiss-print `3-4`.

- Pick ONE substrate, never mix: Swiss Industrial Print (light) OR Tactical Telemetry (dark CRT).
  Swiss light: bg `#F4F4F0`/`#EAE8E3`, ink `#050505..#111`. Tactical dark: bg `#0A0A0A`/`#121212`
  (avoid pure black), phosphor `#EAEAEA`. Accent in BOTH = hazard red `#E61919`/`#FF2A2A`, the ONLY
  accent. Optional terminal green `#4AF626` for a single status element only, else omit.
- Type: macro = heavy neo-grotesque (Neue Haas Black, Archivo Black, Monument Extended, Inter Black),
  `clamp(4rem,10vw,15rem)`, tracking `-0.03..-0.06em`, line-height `0.85-0.95`, UPPERCASE. micro =
  monospace (JetBrains/IBM Plex/Space Mono), `10-14px`, tracking `0.05-0.1em`, UPPERCASE for all
  metadata/nav/IDs. Serif (Playfair/EB Garamond) only as halftone/dithered textural disruption.
- Layout: strict CSS Grid; razor hairlines via `display:grid; gap:1px` over contrasting bg; visible
  `1-2px solid` compartment borders + full-width `<hr>`; bimodal density; ZERO `border-radius`
  (all 90 deg). Semantic tags `<data> <samp> <kbd> <output> <dl>`.
- Symbology: ASCII framing `[ ... ]` `>>>` `///`; `® © ™` and crosshairs `+` as structural marks;
  barcodes, warning stripes, faux telemetry strings (`REV 2.6`, `UNIT / D-01`).
- Texture: halftone / 1-bit dither (`mix-blend-mode:multiply` + SVG dot pattern); CRT scanlines
  (`repeating-linear-gradient`) for terminal; global low-opacity SVG noise on the root.
- Pre-Flight delta (fail if present): any `border-radius` > 0; gradients (except scanline/halftone
  patterns), soft drop shadows, translucency/glassmorphism; more than one accent color; mixing both
  substrates; macro headings not uppercase.
