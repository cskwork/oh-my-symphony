# Bundled skill notices

Oh My Symphony redistributes pinned, factory-runtime derivatives of these
local authoritative upstream checkouts:

| Bundled skill | Upstream source | Pinned revision | License notice |
|---|---|---|---|
| Supergoal | <https://github.com/cskwork/supergoal-skill.git> | `295992a12cc1cd901584082d93d3afd69bb1b4bb` (`v0.6.1`) | `supergoal/LICENSE` |
| Superdesign | <https://github.com/cskwork/superdesign-skill.git> | `c5cae1b80475367279dd2a399fda099227f6e981` | `superdesign/LICENSE` |
| SuperPM | <https://github.com/cskwork/superpm-skill.git> | `bd7587f0f6c92882bf5744ed5f406ab8aaa0195b` | `superpm/LICENSE` |
| SuperQA | <https://github.com/cskwork/superqa-skill.git> | `d4e6a210a2b66cb10998164a159bfd68de569df6` (`v0.3.0`) | `superqa/LICENSE` |

Each listed upstream checkout supplies an MIT license with copyright notice
`Copyright (c) 2026 cskwork`; the complete notice is retained beside each
bundled skill.

## Adapted-source inventory

These are the upstreams explicitly named as adapted, distilled, or method
sources by files that ship in the factory bundle. The inventory describes the
relationship; it does not decide whether a particular use is legally a
derivative work.

| Named source | Shipped relationship | Upstream license evidence retained here |
|---|---|---|
| [taste-skill](https://github.com/leonxlnx/taste-skill) | Supergoal `taste-*` references and Superdesign `taste-core.md` / `aesthetics.md` identify condensed or adapted guidance. | `third_party_licenses/taste-skill-MIT.txt` |
| [impeccable](https://github.com/pbakaus/impeccable) | Superdesign `impeccable-rules.md` says concepts were distilled; `anti-slop-gate.mjs` says it is an independent implementation. | `third_party_licenses/impeccable-Apache-2.0.txt` |
| [last30days-skill](https://github.com/mvanhorn/last30days-skill) | SuperPM `reference/signal.md` identifies its research method as a source. | `third_party_licenses/last30days-skill-MIT.txt` |
| [Agent-Reach](https://github.com/Panniantong/Agent-Reach) | SuperPM `reference/signal.md` identifies its keyless-read techniques as a source. | `third_party_licenses/Agent-Reach-MIT.txt` |
| [storyboard-spec](https://github.com/cskwork/storyboard-spec) at `1b77079347daf2339e61c9b4cba0938c848c5c35` | SuperPM ships an adapted `scripts/shoot.sh` helper used by the storyboard template. | `third_party_licenses/storyboard-spec-MIT.txt` |
| [stitch-landing-skill](https://github.com/cskwork/stitch-landing-skill) at `4b4c7fb00d7d77d48403f6b7682c3fb502e0db0c` | Superdesign `reference/sources.md` identifies inspiration for the shipped `assets.md` and `web.md` guidance. | `third_party_licenses/stitch-landing-skill-MIT.txt` |
| Supergoal / Superdesign sibling reuse | Supergoal credits Oh My Symphony lineage; Superdesign names Supergoal structure, gates, and Playwright patterns. | The relevant pinned cskwork MIT texts are `supergoal/LICENSE` and `superdesign/LICENSE`. |

Reference-only catalogs in the shipped files also link to Playwright CLI,
design systems, component libraries, and asset tools. Those projects are not
copied into this bundle; users install them separately when a route needs
them. Superdesign also mentions Anthropic frontend-design lineage without a
pinned source artifact, so that lineage remains part of the residual review
below rather than being presented as a verified redistribution record.

Residual legal uncertainty: these notices are an engineering inventory based
on pinned local source trees, shipped attribution statements, and the
authoritative license texts available during this audit. This is not legal approval.
Before a public release, a maintainer should obtain legal review,
independently verify unversioned lineage statements and confirm whether any additional NOTICE or
attribution material is required.
