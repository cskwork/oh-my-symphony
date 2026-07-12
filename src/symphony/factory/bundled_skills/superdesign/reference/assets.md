# assets - image + SVG fallback chain

Real or generated assets, never div-mockups or invented data. Each tier degrades gracefully to the next; if all fail, leave a documented placeholder and tell the user.

## Images (in order)

1. **gpt-image-2** (Codex/ChatGPT subscription, local skill). Best for hero art, illustration, textures, product shots, style transfer. Trigger the `gpt-image-2` skill with a concrete prompt (subject, style, palette to match the accent, aspect ratio).
2. **Stitch MCP** (`stitch.withgoogle.com`). Best for generating whole UI screens/layouts to design from. Use when the ask is a full screen, not a single image. Tested models `GEMINI_3_FLASH`, `GEMINI_3_1_PRO`.
3. **Real web images**. Unsplash/Pexels for photography; Simple Icons / devicon for brand logos (real SVG). Verify the URL resolves; no broken links.
4. **Documented placeholder**. A neutral box with exact dimensions + a `TODO: replace with <description>` note, listed in the final report. Never a fake screenshot, never invented logos.

## SVG / marks (in order)

1. **Icon library**. Phosphor, HugeIcons, Radix, Tabler (web); SF Symbols / Material Symbols (native). One family per project.
2. **Generated brand mark**. For invented brands, generate a simple wordmark/monogram (gpt-image-2 or clean hand-authored geometry with a documented rule).
3. **Hand-rolled decorative SVG** - last resort only, and only simple geometry (dividers, blobs) with a one-line rationale. Hand-rolling icon paths stays banned.

## Availability check + fallback

- Before tier 1/2, confirm the tool/skill is reachable. If not, skip to the next tier silently and note the substitution in the report.
- Offline / no tools: go straight to real web images, then placeholder. The design must still ship and pass gates with placeholders flagged.
- Match generated assets to the locked accent + palette from Direction; mismatched stock art is slop.

Done when: every asset is real, generated, or a flagged placeholder with dimensions; manifest lists the tier per asset; no broken links.
