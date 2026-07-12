<!--
DERIVATIVE - condensed, self-contained anti-slop visual authority.
Adapted from taste-skill v2 (https://github.com/leonxlnx/taste-skill, MIT) for a standalone skill.
Refresh: re-pull upstream, re-condense, preserve the section contract + the gate's machine-checkable bans.
-->

# taste-core - anti-slop visual authority

Always loaded at Build + Critique. Every rule is contextual: read the brief, apply what fits. Machine-checkable bans here are enforced by `templates/anti-slop-gate.mjs`.

## Dials

Set and carry exact names: `DESIGN_VARIANCE` (1 symmetric -> 10 chaotic), `MOTION_INTENSITY` (1 static -> 10 cinematic), `VISUAL_DENSITY` (1 airy -> 10 cockpit). Baseline `8 / 6 / 4` unless the read overrides.

| Read | VAR | MOT | DEN |
|---|--:|--:|--:|
| minimalist / calm / Linear / editorial | 5-6 | 3-4 | 2-3 |
| premium consumer / Apple-y / luxury | 7-8 | 5-7 | 3-4 |
| playful / Awwwards / agency | 9-10 | 8-10 | 3-4 |
| mainstream landing / portfolio | 7-9 | 6-8 | 3-5 |
| trust-first / public-sector / a11y-critical | 3-4 | 2-3 | 4-5 |
| dashboard / data-dense business app / admin / internal tool | 3-5 | 2-3 | 6-8 |

## Foundation

Use an official design system when the brief maps to one (Fluent/Material/Carbon/Polaris/Atlaskit/Primer/govuk/uswds/Radix/shadcn). Do not recreate official CSS by hand; do not mix systems. Install commands in `sources.md`. Aesthetic-only directions (glass, bento, brutalism, editorial, dark-tech, aurora) have no package - implement honestly with CSS/Tailwind/Motion/GSAP. Apple Liquid Glass is official only on Apple platforms; web = frosted approximation.

## Typography

- Display: `text-4xl md:text-6xl tracking-tighter leading-none`. Body: `text-base leading-relaxed max-w-[65ch]`.
- Avoid Inter by default. Prefer Geist, Outfit, Cabinet Grotesk, Satoshi, or brand type. Inter OK for explicit neutral/Linear/public-sector.
- Serif only when brand names one or the read is genuinely editorial/luxury. Not Fraunces/Instrument Serif as defaults.
- Emphasis = italic/bold same family, never a random serif word. Italic descenders (`y g j p q`) need `leading-[1.1]` + `pb-1`.

## CJK / Korean text

When the UI ships Korean/Japanese/Chinese, apply these (universal, gate-adjacent):

- Line-break at word (어절) boundaries, never mid-word: set `word-break: keep-all` scoped to the CJK lang (`html[lang="ko"]`); Latin stays `normal`. CJK wraps between any glyphs by default, which splits words uglily without this.
- Real CJK webfont: KO Pretendard or Noto Sans KR; JP Noto Sans JP; SC/TC Noto Sans CJK. List a Latin face first for mixed runs. Never render Hangul/Kanji in a Latin-only font.
- Natural copy, not transliterated English word order: correct particles + endings, one register per surface (`-합니다` vs `-해요`), no telegraphic noun-stacks.
- Tighten measure: CJK carries more meaning per character, so cap line length shorter than Latin. Do not force `<br>` mid-phrase; let keep-all wrap.
- Punctuation: middle dot (`·`), comma, line break. The em/en-dash ban still holds; no half-width Latin punctuation inside CJK runs.
- Set `lang` on `<html>` (or the block) so the browser applies CJK line-breaking and font selection. Mixed EN/KO: keep both faces legible and any language toggle keyboard-reachable.

## Color

- Max one accent. Saturation < 80% default. Lock one palette; no warm/cool gray drift.
- LILA: no default AI-purple/blue glow, neon gradients, purple CTA slop. Purple only if brand owns it.
- Premium-consumer ban: do not default to beige/cream + brass/clay/oxblood + espresso. Rotate (cold luxury, forest, cobalt/cream, olive/brick, mono + one pop) unless brand owns warm-craft.
- Never pure `#000`/`#fff` - tint to off-black/off-white. No gray text on colored backgrounds.

## Layout, material, states

- Avoid centered hero when `DESIGN_VARIANCE > 4` unless editorial/manifesto.
- Cards only when elevation = hierarchy; else spacing/borders/dividers. No cards-in-cards.
- Tint shadows to bg; no pure-black drop shadow on light. One radius system.
- Hero fits viewport: headline <=2 desktop lines, subtext <=20 words/<=4 lines, CTA visible, top padding <= `pt-24`, <=4 text elements. Logo wall below hero.
- Nav one line, 64-72px (80 max). Bento needs exact cell count + visual variety (no empty/repeated cells).
- Eyebrows rationed: <= ceil(sections/3). No split-header default. No 3+ consecutive zigzag splits.
- Implement loading / empty / error / focus / hover / active. Buttons/forms meet WCAG AA. CTA 1-3 words, one line, one label per intent. Form labels above inputs, never placeholder-as-label.

## Images & SVG

- Landing/portfolio need real visuals. Source order: generate (`assets.md`) -> real web image -> documented TODO. Even minimalist needs 2-3 real images unless brief forbids.
- Logo walls = real SVG (Simple Icons/devicon) or generated marks; logos only.
- Hand-rolled decorative SVG discouraged; icon-library SVG fine. Div-based fake screenshots banned. Hero needs a real visual; text + gradient blob is a placeholder.
- Icons: one family (Phosphor/HugeIcons/Radix/Tabler). Lucide only if requested/installed. Never hand-roll icon paths.

## Copy

- Section default: headline <=8 words, body <=25 words, one visual/CTA. Long lists (>5) need tabs/accordion/grouped cards/carousel.
- Re-read every string; rewrite broken grammar, AI-cute phrasing, forced metaphors. No fake precise numbers unless real/labeled mock. One register per page. Quotes <=3 lines, attribution = name + role.

## Motion & perf

- Every animation needs a reason (hierarchy/feedback/state/story). Animate only `transform`/`opacity`; `will-change` sparingly.
- `MOTION_INTENSITY > 3` honors `prefers-reduced-motion`. `> 4` must visibly move or lower the dial. Marquee max one. GSAP pin/scrub: correct `start`/`end`/`scrub`, cleanup `ctx.revert()`, reduced-motion fallback.
- Forbidden: `window.addEventListener("scroll")`, React state from `scrollY`/pointer/RAF, GSAP+Three+Motion fighting one tree.
- Core Web Vitals: LCP <2.5s, INP <200ms, CLS <0.1. Reserve image/font space. Documented z-index scale, no arbitrary `z-50` spam.

## Dark mode

One token strategy (Tailwind `dark:` pairs OR CSS vars/semantic tokens). Off-black/off-white not pure. Test both modes. One page theme; no mid-page inversion unless a deliberate device.

## AI tells - banned (gate-enforced where marked *)

- Visual: default neon glow, pure `#000`*, oversaturated accent, gradient-text spam, custom cursor, generic glass everywhere, AI-purple*.
- Type: Inter as default*, oversized shouting H1, unjustified serif.
- Layout: equal 3-card rows, awkward floating gaps, repeated zigzags, split headers, eyebrow everywhere.
- Content: John Doe/Sarah/Acme/Nexus/SmartFlow*, generic avatars, fake-perfect numbers, filler verbs (elevate/seamless/unleash/next-gen/revolutionize)*.
- Resources: hand-rolled icons/decorative SVG, broken Unsplash links, div fake screenshots.
- Motion: `bounce`/`elastic` easing*, manual scroll listeners*.

### Em-dash ban (gate-enforced)

Visible output contains zero em-dash (`-` U+2014) and zero en-dash (U+2013) as separators. Use hyphen, comma, colon, parentheses, line break. A single visible em/en dash fails pre-flight.

## Pattern vocabulary

Hero: Asymmetric Split, Editorial Manifesto, Media Mask, Kinetic Type, Scroll-Pinned. Nav: Dock Magnification, Magnetic Button, Dynamic Island, Mega Menu. Layout: Bento, Masonry, Split-Screen Scroll, Sticky Stack. Cards: Parallax Tilt, Spotlight Border, Glassmorphism, Swipe Stack. Scroll: Horizontal Hijack, Zoom Parallax, Progress Path. Type: Kinetic Marquee, Text Mask, Gradient Stroke. Micro: Particle Button, Skeleton Shimmer, Ripple, SVG Line Draw, Mesh Gradient.

Library: Motion for UI/state; GSAP+ScrollTrigger for scrolltelling/pin; Three.js for 3D. Isolate in leaf components, clean up effects.

## Pre-flight (taste side; the gate computes the machine-checkable subset)

Brief + dials + system/family declared. No visible em/en dash. One theme, one accent, one radius. Buttons/forms meet contrast; CTAs do not wrap; no duplicate CTA intent. Hero fits viewport. Eyebrow count sane; no split-header default; no 3+ zigzag. Real images/logos; no div fake screenshots. Copy self-audited; no fake precision. Motion motivated + reduced-motion wired; no manual scroll listeners. Dark mode tested or single-mode justified. Loading/empty/error exist. One icon family. No AI tells.
