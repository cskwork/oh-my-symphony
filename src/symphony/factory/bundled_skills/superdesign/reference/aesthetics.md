<!--
DERIVATIVE - aesthetic families. Adapted from taste-skill specialized sub-skills
(https://github.com/leonxlnx/taste-skill, MIT). Overlay on taste-core, never replace it.
-->

# aesthetics - selectable families

Pick AT MOST ONE when the Design Read names its vibe. A family OVERLAYS `taste-core.md`; base universal rules (em-dash ban, real images, WCAG AA, reduced-motion, GPU-safe motion, no AI-placeholder names) still hold. Never mix two families. No family signal -> base taste-core alone.

## Selection map

| Read says | Family |
|---|---|
| clean / calm / Notion-like / content-led / "less" | `minimalist` |
| expensive / $150k agency / Apple-esque / Linear-tier / Awwwards | `high-end` |
| raw / tactical / Swiss print / terminal / anti-corporate | `brutalist` |
| editorial / publication / heritage / long-form | `editorial` |
| glassy / frosted / premium media / Apple-adjacent | `glass` |
| admin / dashboard / analytics console / internal tool / back-office / data table | `dashboard.md` overlay |

`data-dense business apps` are NOT a family in this list: route them to the `reference/dashboard.md` application overlay (loaded at Build, like `engagement.md`), which sets density-first dials and replaces the hero with an app shell. It pairs with the chosen medium, not in place of one.

## minimalist

Dials `VAR 5-6 / MOT 3-4 / DEN 2-3`. Off-black `#111`/`#2F3437` on warm-mono canvas `#FFFFFF`/`#F7F6F3`, ultra-light borders `#EAEAEA`. Accents only as washed pastels. Bento cards radius 8-12px, padding 24-40px, macro whitespace `py-24/32`, `max-w-4xl/5xl`. Motion near-invisible: scroll entry `translateY(12px)+opacity` 600ms `cubic-bezier(.16,1,.3,1)`. One icon family, one stroke weight. Pre-flight delta - fail: Inter/Roboto default, neon, gradient, `shadow-lg`, `rounded-full` on big cards.

## high-end

Dials `VAR 7-9 / MOT 7-9 / DEN 3-4`. Cinematic premium: asymmetric layouts, large empty zones, refined type scale, grain on fixed `pointer-events-none` layer. Mobile collapses asymmetry to `w-full px-4 py-8`, drop rotations. Pre-flight delta - fail: Inter/Arial/Helvetica, thick Material icons, generic 1px gray borders, harsh dark shadows, `linear`/instant transitions, anything static on load, flat (non-nested) premium cards.

## brutalist

Dials `VAR 7-9 / MOT 2-4 / DEN bimodal (telemetry 7-9, Swiss-print 3-4)`. Swiss light (`#F4F4F0` bg, `#050505` ink) OR tactical dark (`#0A0A0A` bg, phosphor `#EAEAEA`). Accent = hazard red `#E61919`. Heavy neo-grotesque macro type `clamp(4rem,10vw,15rem)`. Hard borders, flat blocks, offset shadows, mono detail. Keep WCAG contrast despite rawness.

## editorial

Dials `VAR 5-7 / MOT 3-5 / DEN 3-5`. Justified serif allowed here (name the face): GT Sectra, Tiempos, Spectral, Newsreader. Strong measure `max-w-[68ch]`, drop caps, hanging punctuation, generous leading `1.6`. Restrained palette + one ink accent. Motion = reveal only.

## glass

Dials `VAR 6-8 / MOT 5-7 / DEN 3-4`. Layered frosted panels: `backdrop-blur`, inner border `1px rgba(255,255,255,.18)`, soft outer shadow, subtle specular highlight. Background needs real depth (image/gradient/3D) for glass to read. Mandatory reduced-transparency fallback (solid tinted panel). Not generic glass on every element - reserve for elevated surfaces.
