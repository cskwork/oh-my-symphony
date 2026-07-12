#!/usr/bin/env bash
# preflight-gate - superdesign exit check. Runs the deterministic detectors on a built surface
# and confirms the designer left an auditable claim. Exit 0 = ship-ready; 1 = violation; 2 = usage.
#
# A "vault" is the work dir for one surface. It must contain:
#   - claims.md           (designer's claim: dials declared + a run-to-prove)
#   - contrast-pairs.json  (critic's enumerated FG/BG pairs for contrast-gate)
# Source files to scan: pass as args, or the gate auto-discovers *.html/*.css/*.jsx/*.tsx in the vault.
#
# Usage: bash preflight-gate.sh <vault-dir> [source-file ...]

set -u
HERE="$(cd "$(dirname "$0")" && pwd)"

fail() { echo "PREFLIGHT FAIL: $*" >&2; exit 1; }
usage() { echo "usage: bash preflight-gate.sh <vault-dir> [source-file ...]" >&2; exit 2; }

VAULT="${1:-}"
[ -n "$VAULT" ] || usage
[ -d "$VAULT" ] || fail "vault not found: $VAULT"
shift || true

# 1. claims.md exists and declares dials.
CLAIMS="$VAULT/claims.md"
[ -f "$CLAIMS" ] || fail "claims.md missing in vault (designer must record the claim + run-to-prove)"
grep -q "DESIGN_VARIANCE" "$CLAIMS" || fail "claims.md does not declare dials (DESIGN_VARIANCE / MOTION_INTENSITY / VISUAL_DENSITY)"

# 2. Collect source files (args, or auto-discover in the vault).
SRC=()
if [ "$#" -gt 0 ]; then
  SRC=("$@")
else
  while IFS= read -r f; do SRC+=("$f"); done < <(find "$VAULT" -type f \( -name '*.html' -o -name '*.css' -o -name '*.jsx' -o -name '*.tsx' -o -name '*.js' -o -name '*.ts' -o -name '*.svelte' -o -name '*.vue' -o -name '*.astro' \) -not -path '*/node_modules/*' -not -path '*/dist/*' -not -path '*/build/*' -not -path '*/.next/*' 2>/dev/null)
fi
[ "${#SRC[@]}" -gt 0 ] || fail "no source files to scan (pass files as args or put *.html/*.css/*.jsx/*.tsx in the vault)"

# 3. Anti-slop detector.
echo "-- anti-slop-gate --"
node "$HERE/anti-slop-gate.mjs" "${SRC[@]}" || fail "anti-slop violations (see above)"

# 4. Contrast gate (critic must have enumerated the pairs).
PAIRS="$VAULT/contrast-pairs.json"
[ -f "$PAIRS" ] || fail "contrast-pairs.json missing in vault (critic must enumerate every text/bg pair)"
echo "-- contrast-gate --"
node "$HERE/contrast-gate.mjs" "$PAIRS" || fail "contrast below threshold (see above)"

echo "== PREFLIGHT PASS =="
exit 0
