#!/usr/bin/env bash
# /supergoal QA gate — the literal exit condition for the QA phase (GREENFIELD/LEGACY,
# and any web-bug check in DEBUG). It converts "QA actually drove the app with playwright-cli
# and captured user-observable evidence" from prose (reference/qa.md) into a machine-checkable
# backstop. QA was the one phase gate that stayed instruction-only, which let a run silently
# fall back to a headless-Chrome render, skip the as-is/to-be proof, and still pass delivery.
# playwright-cli is now the single sanctioned driver — this gate rejects any other.
# NEVER edit this script to make a non-compliant QA pass — re-run QA properly instead.
#
# Usage: qa-gate.sh <vault-dir> <browser|cli>
#   <vault-dir>  the run's changelog folder, e.g. docs/changelog/2026-06/10-my-objective
#   browser|cli  app type under test: a browser app (drives a real browser) or a CLI/library
#
# Exit 0 only if:
#   browser: QA.md has a '## QA' section; qa/as-is-* and qa/to-be-* evidence files
#            exist (the user-observable proof, same framing); and the driver is named on a
#            'Tool:' line that is playwright-cli (a silent headless-Chrome render, agent-browser,
#            or any other tool fails here).
#   cli:     QA.md has a '## QA' section recording an integration smoke (no browser
#            evidence required — CLI/library has no browser).

set -euo pipefail

usage() { echo "usage: qa-gate.sh <vault-dir> <browser|cli>" >&2; exit 2; }
[ $# -ge 2 ] || usage
VAULT="$1"; APPTYPE="$2"
VERIF="$VAULT/QA.md"
QA="$VAULT/qa"
fail() { echo "QA-GATE FAIL: $*" >&2; exit 1; }

case "$APPTYPE" in browser|cli) ;; *) usage ;; esac

echo "== /supergoal QA gate =="
echo "vault: $VAULT  app-type: $APPTYPE"

[ -s "$VERIF" ] || fail "QA.md missing/empty — QA recorded nothing"
grep -qiE '^##[[:space:]]+QA([[:space:]]|$)' "$VERIF" \
  || fail "QA.md has no '## QA' section — QA evidence was never recorded"
QA_SECTION="$(awk '
  /^[[:space:]]*##[[:space:]]+QA([[:space:]]|$)/ { in_qa = 1; next }
  /^[[:space:]]*##[[:space:]]+/ { if (in_qa) exit }
  in_qa { print }
' "$VERIF")"

# UI/UX contrast enforcement — makes "contrast is computed, not eyeballed" a real gate for BOTH the
# Expressive (taste-skill-v2) and Functional (functional-ui) tiers. It fires when the run declares a
# tier (a 'UI-tier: Expressive|Functional' line in ## QA) OR a pairs file exists. A UI run that omits
# the pair list, or whose palette has a sub-threshold pair, fails here — no silent eyeballed contrast.
PAIRS="$QA/contrast-pairs.json"
ui_tier_line="$(printf '%s\n' "$QA_SECTION" | grep -iE '^[[:space:]]*[-*]?[[:space:]]*UI-tier:' | head -1 || true)"
if printf '%s' "$ui_tier_line" | grep -qiE 'expressive|functional' || [ -f "$PAIRS" ]; then
  [ -f "$PAIRS" ] \
    || fail "UI-tier run but no 'qa/contrast-pairs.json' — enumerate the text/bg pairs (reference/ui-ux.md or reference/functional-ui.md)"
  CONTRAST_GATE="$(dirname "$0")/contrast-gate.mjs"
  [ -f "$CONTRAST_GATE" ] || fail "contrast-gate.mjs not found next to qa-gate.sh"
  node "$CONTRAST_GATE" "$PAIRS" \
    || fail "contrast gate failed on qa/contrast-pairs.json — fix the palette (rewind to Build); never lower the threshold"
  echo "  ok: contrast gate passed for UI run"
fi

# CLI/library: no browser, so an integration-smoke record under '## QA' is the whole contract.
if [ "$APPTYPE" = cli ]; then
  echo "  ok: ## QA present (CLI/library integration smoke; no browser evidence required)"
  echo "== QA GATE PASS =="
  exit 0
fi

# Browser app from here.
# 1) User-observable proof: non-empty as-is/to-be evidence (any extension) under qa/.
require_evidence() {
  local pattern="$1" purpose="$2" found=0 path
  [ -d "$QA" ] || fail "no 'qa/${pattern}' evidence — capture the ${purpose} state at a fixed route/viewport (reference/qa.md as-is/to-be)"
  for path in "$QA"/$pattern; do
    [ -e "$path" ] || [ -L "$path" ] || continue
    [ -f "$path" ] \
      || fail "'qa/${pattern}' evidence match is not a file: ${path}"
    [ -s "$path" ] \
      || fail "empty 'qa/${pattern}' evidence: ${path} — capture a real screenshot/text proof"
    found=1
  done
  [ "$found" = 1 ] \
    || fail "no 'qa/${pattern}' evidence — capture the ${purpose} state at a fixed route/viewport (reference/qa.md as-is/to-be)"
}
require_evidence "as-is-*" "before"
require_evidence "to-be-*" "after"

# 2) The driver that exercised the app must be named on a 'Tool:' line, and it must be playwright-cli
#    — the single sanctioned driver. No agent-browser, no Playwright MCP, no silent headless render.
tool_line="$(printf '%s\n' "$QA_SECTION" | grep -iE '^[[:space:]]*[-*]?[[:space:]]*Tool:' | head -1 || true)"
[ -n "$tool_line" ] \
  || fail "## QA has no 'Tool:' line — name the driver that exercised the app (must be playwright-cli)"

# 3) The driver must be playwright-cli — the only sanctioned driver. Any other name (agent-browser,
#    Playwright MCP, a headless-Chrome render) fails here; re-run QA with playwright-cli.
printf '%s' "$tool_line" | grep -qiF 'playwright-cli' \
  || fail "## QA 'Tool:' line is not playwright-cli — playwright-cli is the only sanctioned driver (reference/qa.md, reference/playwright-cli.md); re-run QA with it"
echo "  ok: driven by playwright-cli"

echo "  ok: as-is/to-be evidence present + driver named"
echo "== QA GATE PASS =="
