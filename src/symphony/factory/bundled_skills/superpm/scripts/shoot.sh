#!/usr/bin/env bash
# Capture storyboard HTML pages with a locally installed Chrome-family browser.
# Adapted from cskwork/storyboard-spec at commit
# 1b77079347daf2339e61c9b4cba0938c848c5c35 (MIT); see bundle notices.

set -euo pipefail

DIR="${1:?usage: shoot.sh <dir> [one-file.html]}"
ONE="${2:-}"
case "$DIR" in /*) ;; *) DIR="$(cd "$DIR" && pwd)" ;; esac

THUMB_W="${THUMB_W:-1000}"
THUMB_H="${THUMB_H:-820}"
FULL_W="${FULL_W:-1500}"
FULL_H="${FULL_H:-1500}"

if [ -z "${CHROME:-}" ]; then
  for candidate in \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/Applications/Chromium.app/Contents/MacOS/Chromium" \
    "$(command -v google-chrome 2>/dev/null || true)" \
    "$(command -v chromium 2>/dev/null || true)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      CHROME="$candidate"
      break
    fi
  done
fi
[ -n "${CHROME:-}" ] || { echo "Chrome/Chromium not found; set CHROME" >&2; exit 1; }

shoot() {
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars \
    --window-size="$2,$3" --screenshot="$1" "file://$4" >/dev/null 2>&1
}

if [ -n "$ONE" ]; then
  file="$DIR/$(basename "$ONE")"
  [ -f "$file" ] || file="$ONE"
  output="${TMPDIR:-/tmp}/sb-verify-$(basename "$ONE" .html).png"
  shoot "$output" "$FULL_W" "$FULL_H" "$file"
  echo "verify shot: $output"
  exit 0
fi

mkdir -p "$DIR/thumbs"
count=0
for file in "$DIR"/sb-*.html; do
  [ -e "$file" ] || continue
  name="$(basename "$file" .html)"
  shoot "$DIR/thumbs/${name}.png" "$THUMB_W" "$THUMB_H" "$file"
  echo "thumb: thumbs/${name}.png"
  count=$((count + 1))
done
[ "$count" -gt 0 ] || echo "no sb-*.html found in $DIR" >&2
echo "done ($count thumbnails)"
