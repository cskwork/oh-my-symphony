<!--
COMPRESSED DERIVATIVE - taste-skill v2 (experimental).
supergoal's UI/UX design authority, loaded on demand by reference/ui-ux.md.

Source:  https://github.com/leonxlnx/taste-skill  (path: skills/taste-skill/SKILL.md)
Version: v2 (experimental) - install name `design-taste-frontend`
Commit:  3c7017d636c3a4aad378433ea6d0cfa6c921da4a  (upstream 2026-05-26)
Pulled:  2026-05-30   License: MIT (upstream LICENSE)
Compressed: 2026-06-02 for agent context cost.

To refresh: pull upstream, replace this body, then re-compress while preserving the contract below.
-->

> Not installable. Reference material for supergoal's UI/UX overlay.
> Sections (jump, don't rescan): 0 Brief inference | 1 Three dials | 2 Foundation choice |
> 3 Default architecture | 4 Design directives | 5 Motion | 6 Performance & a11y | 7 Dial
> definitions | 8 Dark mode | 9 AI tells (banned) | 10 Pattern vocabulary | 11 Redesign protocol |
> 12 Block library contract | 13 Out of scope | 14 Final pre-flight | Appendices.

---
name: design-taste-frontend
description: Anti-slop frontend guidance for landing pages, portfolios, and redesigns. Infer the brief,
choose the right design system or aesthetic, use real visuals, and pass strict pre-flight checks.
---

# tasteskill: Anti-Slop Frontend Skill

For landing pages, portfolios, marketing/about pages, and redesigns. Not for dashboards, data tables,
wizards, native mobile, dense admin UI, or realtime collaboration surfaces. Every rule is contextual:
read the brief first, then apply only what fits.

## 0. Brief inference

Before code, infer the design read:

- Page kind: SaaS/consumer/agency/event landing, portfolio, redesign, editorial/blog.
- User vibe words: minimalist, calm, Linear, Awwwards, brutalist, premium, playful, serious B2B,
  editorial, glassy, dark tech.
- References: URLs, screenshots, products, competitors, brand names.
- Audience: procurement, consumer, recruiter, public-sector, regulated, kids, etc.
- Existing brand assets: logo, color, type, photography, interaction patterns.
- Quiet constraints: a11y, public-sector, trust-first commerce, regulation. These override aesthetics.

Output one line before generating:

`Reading this as: <page kind> for <audience>, with a <vibe> language, leaning toward <design system or aesthetic family>.`

If two plausible reads diverge, ask exactly one clarifying question. If context is enough, do not ask.

Anti-defaults: no automatic AI-purple gradients, centered dark mesh hero, three equal feature cards,
generic glass everywhere, infinite micro-animations, or Inter + slate-900.

## 1. Three dials

Set and carry:

- `DESIGN_VARIANCE`: 1 perfect symmetry, 10 artsy chaos.
- `MOTION_INTENSITY`: 1 static, 10 cinematic/physics.
- `VISUAL_DENSITY`: 1 airy gallery, 10 packed cockpit.

Baseline: `8 / 6 / 4`, unless design read overrides.

| Signal | VARIANCE | MOTION | DENSITY |
|---|---:|---:|---:|
| minimalist / clean / calm / Linear / editorial | 5-6 | 3-4 | 2-3 |
| premium consumer / Apple-y / luxury / brand | 7-8 | 5-7 | 3-4 |
| playful / wild / Dribbble / Awwwards / agency | 9-10 | 8-10 | 3-4 |
| mainstream landing / portfolio / marketing | 7-9 | 6-8 | 3-5 |
| trust-first / public-sector / regulated / a11y-critical | 3-4 | 2-3 | 4-5 |
| redesign preserve | match existing | +1 | match |
| redesign overhaul | +2 | +2 | match |

Use exact variable names; do not invent aliases.

## 2. Foundation choice

Use an official design system when the brief maps to one. Do not recreate official CSS by hand, and do
not mix systems.

| Brief reads as | Use |
|---|---|
| Microsoft / enterprise SaaS | `@fluentui/react-components` or `@fluentui/web-components` |
| Google / Material product | `@material/web` + Material 3 tokens |
| IBM B2B / analytics | `@carbon/react` + `@carbon/styles` |
| Shopify admin | Polaris web components / Polaris React |
| Atlassian / Jira | `@atlaskit/*` + `@atlaskit/tokens` |
| GitHub devtool/community/marketing | Primer CSS / Primer Brand |
| UK public-sector | `govuk-frontend` |
| US public-sector | `uswds` |
| fast local-business / agency MVP | Bootstrap 5.3 |
| accessible React base | `@radix-ui/themes` |
| owned modern SaaS components | shadcn/ui, customized |
| Tailwind indie/AI marketing | Tailwind v4 utilities |

Aesthetic-only directions have no official package: glassmorphism, bento, brutalism, editorial,
dark-tech, aurora/mesh, kinetic typography, Apple Liquid Glass on web. Implement honestly with native
CSS/Tailwind/Motion/GSAP as needed. Apple Liquid Glass is official only on Apple platforms; web is a
frosted-glass approximation.

## 3. Default architecture

- React or Next.js. Prefer Server Components; isolate interactivity in leaf `"use client"` components.
- Tailwind v4 by default. For v4, use `@tailwindcss/postcss` or Vite plugin, not the old plugin.
- Motion imports from `motion/react`; `framer-motion` is legacy alias.
- Fonts: `next/font` or self-hosted `@font-face` with `font-display: swap`; no production Google
  Fonts `<link>`.
- State: local `useState`/`useReducer` for isolated UI; global only to avoid deep prop drilling.
  Continuous values (mouse, scroll, physics) use Motion values/hooks, never React state.
- Icons: prefer Phosphor, HugeIcons, Radix, Tabler. Lucide only if requested or already installed.
  Never hand-roll icon paths. One icon family per project.
- Emojis discouraged unless playful/social-native brief explicitly asks.
- Layout: standard breakpoints, `max-w-[1400px] mx-auto` or `max-w-7xl`, `min-h-[100dvh]` not
  `h-screen`, CSS Grid over flex percentage math.
- Before importing a third-party library, check `package.json`; if missing, give install command first.

## 4. Design directives

### Typography

- Display default: `text-4xl md:text-6xl tracking-tighter leading-none`.
- Body default: `text-base text-gray-600 leading-relaxed max-w-[65ch]`.
- Avoid Inter by default. Prefer Geist, Outfit, Cabinet Grotesk, Satoshi, or brand-appropriate type.
  Inter is fine for explicit neutral/Linear/public-sector reads.
- Useful pairings: Geist + Geist Mono; Satoshi + JetBrains Mono; Cabinet Grotesk + Inter Tight;
  GT America + IBM Plex Mono.
- Serif is very discouraged as default. Use only when brand names a serif or the read is genuinely
  editorial/luxury/publication/heritage and you can justify the specific face.
- Do not use Fraunces or Instrument Serif as defaults.
- For emphasis inside a headline, use italic/bold in the same family; do not inject a random serif word.
- Italic display words with descenders (`y g j p q`) need `leading-[1.1]` minimum and `pb-1`/`mb-1`.

### Color

- Max one accent color. Saturation under 80% by default.
- LILA rule: no default AI-purple/blue glow, random neon gradients, or purple CTA slop. If brand/brief
  asks for purple, use it deliberately and consistently.
- Lock one palette across the page. No warm/cool gray drift.
- Premium-consumer ban: do not default to beige/cream + brass/clay/oxblood/ochre + espresso. Rotate to
  cold luxury, forest, black/tan, cobalt/cream, terracotta/slate, olive/brick, or monochrome + one pop.
  Use the warm-craft palette only when brand explicitly owns it.

### Layout, material, UI states

- Avoid centered hero when `DESIGN_VARIANCE > 4` unless editorial/manifesto/launch.
- Use cards only when elevation communicates hierarchy; otherwise use spacing, borders, or dividers.
- Tint shadows to background; no pure black drop shadows on light backgrounds.
- Pick one radius system and follow it, or document the rule.
- Implement loading, empty, error, focus, hover, and active states.
- Button/form text must meet WCAG AA. No white-on-white, invisible ghost buttons, weak placeholders, or
  unreadable focus/error text.
- CTA labels must fit one desktop line. Primary CTAs should be 1-3 words.
- Use one label per intent: no "Get in touch" plus "Let's talk" plus "Contact" on the same page.
- Form labels go above inputs; placeholder is never the label.

### Hard layout rules

- Hero fits initial viewport: headline <=2 desktop lines, subtext <=20 words and <=4 lines, CTA visible.
- Hero top padding max `pt-24`; plan font scale with image size.
- Hero text stack max 4 elements: eyebrow/brand strip or neither, headline, subtext, CTAs. Move trust,
  pricing teasers, compatibility lines, bullets, and avatar rows below hero.
- Logo wall goes under hero, not inside it.
- Desktop nav one line, 64-72px default, 80px max.
- Bento grids need exact cell count and visual rhythm. No empty cells, no one-sided repeated rows.
- Section layout family appears at most once; no 3+ consecutive zigzag image/text splits.
- Eyebrow labels are rationed: max 1 per 3 sections, hero counts. Mechanical check: count small-caps
  `uppercase tracking` labels above headings.
- Split-header pattern (left giant headline + right tiny paragraph) is banned by default. Stack unless
  the right side carries real visual/interactive content.
- Bento/feature grids need 2-3 visually varied cells: image, pattern, tint, or appropriate gradient.
- Mobile collapse must be explicit per section below 768px.

### Images and logos

- Landing pages and portfolios need real visuals. Use image generation first when available, then real
  web images, then explicit TODO placeholders with final note to user.
- Even minimalist pages need at least 2-3 real images unless the brief truly forbids it.
- Logo walls use real SVG logos (Simple Icons/devicon) or generated marks for invented brands. Logo wall
  is logos only; no industry/category labels.
- Hand-rolled decorative SVGs are strongly discouraged. Icon-library SVGs are fine.
- Div-based fake screenshots are banned. Use real screenshot, generated image, real component preview,
  or no preview.
- Hero needs a real visual; text + gradient blob is a placeholder.

### Copy and content density

- Default section: headline <=8 words, body <=25 words, one visual or CTA.
- No data-dump marketing sections. Long lists (>5 items) need tabs, accordions, grouped cards,
  horizontal scroll, carousel, marquee, or a separate page.
- Spec sheets should not be long hairline tables. Use 2-col cards, scroll-snap pills, grouped chunks, or
  featured-vs-rest.
- Re-read every visible string. Rewrite broken grammar, unclear referents, AI-cute phrasing, forced
  metaphors, fake craft labels, and cute-but-wrong copy.
- Fake precise numbers are banned unless real data or clearly labeled mock/sample.
- One copy register per page.
- Quotes max 3 lines; attribution is name + role/company. No em-dash in quotes or attribution.
- Page theme locks to light, dark, or auto. Do not invert sections mid-page unless the brief explicitly
  calls for one deliberate theme-switch device.

## 5. Motion and proactivity

Use effects only when the design read calls for them. Every animation needs a reason: hierarchy,
storytelling, feedback, or state transition.

- Glassmorphism: only for suitable premium/media vibes; use inner border/shadow and reduced-transparency
  fallback.
- Magnetic physics: `MOTION_INTENSITY > 5` and premium/playful/agency read; Motion values only.
- Perpetual loops: only when the section benefits; not every card.
- If `MOTION_INTENSITY > 4`, the page must visibly move. If scope cannot support working motion, lower
  the dial.
- Marquee: max one per page.
- GSAP sticky-stack / horizontal-pan: use only for real pin/scrub work. Critical params:
  `start: "top top"`, `pin: true`, correct `end`, `scrub`, cleanup with `ctx.revert()`, reduced-motion
  static fallback.
- Simple viewport reveals use Motion `whileInView`; save GSAP for pin/scrub.

Forbidden:

- `window.addEventListener("scroll", ...)`
- React state from `window.scrollY`, pointer physics, or RAF loops
- Layout props on static content "for safety"
- GSAP/Three/Motion fighting in the same component tree

## 6. Performance and accessibility

- Animate only `transform` and `opacity`; use `will-change` sparingly.
- Any motion above `MOTION_INTENSITY > 3` honors `prefers-reduced-motion`.
- Consumer-facing pages support both light and dark unless user explicitly locks one.
- Core Web Vitals targets: LCP <2.5s, INP <200ms, CLS <0.1.
- Reserve image/font/embed space. Hero images use priority/preload.
- Grain/noise filters go on fixed pointer-events-none pseudo-elements, never scrolling containers.
- Lazy-load Three.js/heavy below-the-fold motion.
- Use a documented z-index scale; do not spam arbitrary `z-50`.

## 7. Dial definitions

- `DESIGN_VARIANCE` 1-3: symmetrical; 4-7: offset/overlap/varied aspect; 8-10: masonry, fractional
  grids, large empty zones. Mobile under 768px collapses to strict single column.
- `MOTION_INTENSITY` 1-3: static plus hover/active; 4-7: CSS/Motion transitions and reveals; 8-10:
  scroll choreography/parallax/GSAP/CSS scroll-driven animation. Never manual scroll listeners.
- `VISUAL_DENSITY` 1-3: airy gallery; 4-7: normal web spacing; 8-10: tight cockpit, mono numbers, lines
  instead of cards.

## 8. Dark mode

Pick one token strategy:

- Tailwind `dark:` pairs, or
- CSS variables/semantic tokens for shadcn, Radix, or design-system theming.

Brief and brand decide colors. Enforce contrast, hierarchy parity, brand fidelity, off-black/off-white
instead of pure black/white, and test both modes. Respect `prefers-color-scheme` unless brand insists.

## 9. AI tells - banned patterns

Visual/CSS: neon glows by default, pure black, oversaturated accents, excessive gradient text, custom
mouse cursors, generic glass, AI-purple.

Typography: Inter as default, oversized shouting H1, unjustified serif.

Layout: perfect equal-card rows, three identical feature cards, awkward floating gaps, repeated
zigzags, split headers, eye-brow everywhere.

Content/data: John Doe/Sarah/Acme/Nexus/SmartFlow, generic avatars, fake-perfect numbers, filler verbs
like elevate/seamless/unleash/next-gen/revolutionize.

Resources: hand-rolled icons, hand-rolled decorative SVGs by default, broken Unsplash links, shadcn
default state, div fake screenshots.

Production-test tells:

- Version labels, beta/invite eyebrows, `Brand · No. 01` micro-meta.
- Section-number eyebrows, image pagination labels, scroll cues.
- Overused middle-dot separators; decorative dots without semantic state.
- `<br>`-broken italic headlines, rotated vertical labels, crosshair lines.
- Fake version footers, stock counters, poetic labels like "Field notes" unless real.
- Weather/locale strips unless place/time-zone relevance is real.
- Pills over images, decorative photo credits, hero-bottom text strips.
- Border top+bottom on every long-list row, progress bars with filled background tracks.

### Em-dash ban

Visible page output contains zero em-dashes (`—`) and zero en-dashes (`–`) as separators. Use hyphen,
period, comma, colon, parentheses, line break, hairline, or column layout. Date/number ranges use
hyphen. A single visible em/en dash fails pre-flight.

## 10. Pattern vocabulary

Use these names when the design read calls for them:

- Hero: Asymmetric Split, Editorial Manifesto, Media Mask, Kinetic Type, Curtain Reveal, Scroll-Pinned.
- Navigation: Dock Magnification, Magnetic Button, Gooey Menu, Dynamic Island, Radial Menu, Speed Dial,
  Mega Menu Reveal.
- Layout: Bento Grid, Masonry, Chroma Grid, Split-Screen Scroll, Sticky Stack.
- Cards: Parallax Tilt, Spotlight Border, Glassmorphism, Holographic Foil, Swipe Stack, Morphing Modal.
- Scroll: Sticky Scroll Stack, Horizontal Scroll Hijack, Sequence Scroll, Zoom Parallax, Progress Path,
  Liquid Swipe.
- Media: Dome Gallery, Coverflow, Drag-to-Pan Grid, Accordion Image Slider, Hover Image Trail, Glitch.
- Type: Kinetic Marquee, Text Mask, Text Scramble, Circular Path, Gradient Stroke, Kinetic Grid.
- Micro: Particle Button, Pull-to-Refresh, Skeleton Shimmer, Directional Hover, Ripple, SVG Line Draw,
  Mesh Gradient, Lens Blur.

Library choice: Motion for UI/state motion; GSAP + ScrollTrigger for scrolltelling/hijacks; Three.js
for 3D/canvas. Isolate in leaf components and clean up effects.

## 11. Redesign protocol

Detect mode first:

- Greenfield: no existing site or overhaul approved.
- Redesign preserve: modernize without breaking brand. Audit first.
- Redesign overhaul: new visual language, preserve content and IA.

If ambiguous, ask once whether to preserve brand or start visually from scratch.

Audit before touching: brand tokens, IA, content blocks, preserve/retire patterns, existing dial values,
SEO baseline. SEO migration is the largest redesign risk.

Preserve silently: slugs, anchors, nav labels, copy voice, accessibility wins, analytics events, brand
logo/wordmark, legal/consent/cookie copy. Ask before changing them.

Modernize in order: typography, spacing/rhythm, color recalibration, motion layer, hero/key-section
recomposition, full block replacement only when unsalvageable.

Targeted evolution when IA/content/SEO are sound. Full redesign only when visual debt is structural.

## 12. Block library contract

Pattern vocabulary names patterns; block files implement them.

Location:

```text
skills/taste-skill/blocks/<category>/<name>.md
```

Required frontmatter: `name`, `category`, `dial_compatibility`, `when_to_use`, `not_for`, `stack`.

Required body: visual sketch, props API, code sketch, mobile fallback, motion variants for 1-3/4-7/8-10,
dark-mode notes, anti-patterns, references.

Discipline: one block per file, standalone render, passes pre-flight. Design-system variants include
system suffix, e.g. `feature/bento-grid--material.md`.

## 13. Out of scope

Use the right tool instead for dashboards/admin, data tables, wizards, code editors, native mobile, and
realtime collab UI. Apply this skill only to marketing/about/landing surfaces inside those projects.

## 14. Final pre-flight

Run every check. If any cannot be honestly ticked, the page is not done.

- Brief inference declared; dials explicit; system/aesthetic chosen honestly; redesign mode/audit done.
- No visible em/en dash separators.
- One page theme, one accent, one radius system.
- Buttons/forms meet contrast; CTAs do not wrap; duplicate CTA intent removed.
- Serif/premium-consumer/italic-descender checks passed.
- Hero fits viewport, top padding <= `pt-24`, max 4 text elements, trust/logo wall below hero.
- Eyebrow count <= ceil(section count / 3); no split-header default; no 3+ zigzag sections.
- Nav one line, <=80px. Bento exact cell count and visual diversity.
- Real images/logos used; no div fake screenshots, hand-rolled decorative SVGs, image overlay tags, or
  decorative photo credits.
- Copy self-audited; quotes <=3 lines; no fake precision; content density sane.
- Motion is motivated and visible when claimed; marquee max one; GSAP pin/scrub params correct.
- No manual scroll listeners; reduced motion wired; effects clean up.
- Dark mode tokens tested or single-mode lock justified.
- Mobile collapse explicit; `min-h-[100dvh]`; no `h-screen`.
- Loading/empty/error states exist. Cards omitted where spacing suffices.
- Icons from allowed library. One design system. Core Web Vitals plausible.
- No AI tells from Section 9.

# Appendices

Install commands, canonical source URLs, and the Apple Liquid Glass web approximation now live in
`reference/taste-sources.md` - load it when you need a design system's install/setup or source links.
