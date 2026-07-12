#!/usr/bin/env bash
# render-gate - superdesign rendered-verification exit check. Sibling of preflight-gate.sh.
# preflight-gate scans static SOURCE (pure Node, no browser). THIS gate confirms the Critic actually
# RENDERED the surface with playwright-cli and left auditable proof: a '## Render' block in claims.md
# naming playwright-cli as the driver, a screenshot per framing under the vault's render/ dir, and a
# clean-console attestation. It converts "the page was rendered and is paint-clean" from prose into a
# machine-checkable backstop, the way supergoal's qa-gate.sh does for QA.
#
# playwright-cli is the ONLY sanctioned render driver (reference/playwright-cli.md). There is no
# skip/fallback path: a missing block, a non-playwright-cli Tool line, no screenshot, or a console that
# is not attested clean all FAIL. If the driver cannot be installed the Critic STOPs and asks the user -
# it never reaches this gate with a substitute. NEVER edit this script to make a non-rendered surface
# pass - re-run the Critic's render step instead.
#
# Usage: bash render-gate.sh <vault-dir>
#   <vault-dir>  the surface vault, default .superdesign/<surface>/, holding claims.md + render/
#
# Exit 0 = rendered + proven; 1 = violation; 2 = usage.

set -u
fail() { echo "RENDER-GATE FAIL: $*" >&2; exit 1; }
usage() { echo "usage: bash render-gate.sh <vault-dir>" >&2; exit 2; }

VAULT="${1:-}"
[ -n "$VAULT" ] || usage
[ -d "$VAULT" ] || fail "vault not found: $VAULT"

CLAIMS="$VAULT/claims.md"
[ -f "$CLAIMS" ] || fail "claims.md missing in vault (designer records the surface; critic fills the Render block)"

echo "== superdesign render gate =="
echo "vault: $VAULT"

# 1) The Critic must have recorded a '## Render' block (the rendered-verification evidence).
grep -qiE '^##[[:space:]]+Render\b' "$CLAIMS" \
  || fail "no '## Render' block in claims.md - the critic must render the surface with playwright-cli and record it (reference/playwright-cli.md)"

# 2) The driver that rendered the surface must be named on a 'Tool:' line, and it must be playwright-cli
#    - the single sanctioned render driver. Any other name (Playwright MCP, a headless-Chrome script)
#    fails here; there is no skip path.
tool_line="$(grep -iE '^[[:space:]]*[-*]?[[:space:]]*Tool:' "$CLAIMS" | head -1 || true)"
[ -n "$tool_line" ] \
  || fail "Render block has no 'Tool:' line - name the driver that rendered the surface (must be playwright-cli)"
printf '%s' "$tool_line" | grep -qiE 'playwright-cli' \
  || fail "'Tool:' line is not playwright-cli - it is the only sanctioned render driver (reference/playwright-cli.md); re-render with it"
echo "  ok: rendered by playwright-cli"

# 3) At least one screenshot under the vault's render/ dir (the as-rendered proof, one per framing).
shot_count="$(find "$VAULT/render" -maxdepth 1 -type f \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.webp' \) 2>/dev/null | wc -l | tr -d ' ')"
[ "${shot_count:-0}" -gt 0 ] \
  || fail "no screenshot under $VAULT/render/ - capture one per framing with playwright-cli screenshot --filename=$VAULT/render/<view>.png"
echo "  ok: $shot_count screenshot(s) under render/"

# 4) Console attested clean (zero render errors). Warnings are allowed; an un-clean or missing
#    attestation fails. The critic writes 'Console: clean' (or '0 errors') only after reading the console.
console_line="$(grep -iE '^[[:space:]]*[-*]?[[:space:]]*Console:' "$CLAIMS" | head -1 || true)"
[ -n "$console_line" ] \
  || fail "Render block has no 'Console:' attestation - record 'Console: clean' after reading playwright-cli console (zero render errors)"
printf '%s' "$console_line" | grep -qiE 'clean|\b0 errors?\b|\bno errors?\b' \
  || fail "'Console:' line is not attested clean ($console_line) - fix the render errors, do not soften the attestation"
echo "  ok: console attested clean"

echo "== RENDER GATE PASS =="
exit 0
