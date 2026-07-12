#!/usr/bin/env node
// /supergoal contrast gate — the literal UI/UX exit check for "contrast is computed, not eyeballed"
// (reference/ui-ux.md, taste-skill-v2 §14). It removes the eyeball/hallucinate-the-ratio failure mode:
// the QA/Designer agent enumerates the FG/BG pairs it actually found in the CSS (the judgment part a
// critic can audit), and THIS script computes the WCAG 2.x ratios and the pass/fail verdict
// deterministically. The agent cannot fudge the math, and a silent sub-AA pair cannot pass.
// NEVER edit this script to make a failing palette pass — fix the colors instead.
//
// Usage: contrast-gate.mjs <pairs.json>
//   <pairs.json>  a JSON array of the text/background pairs actually used, e.g. <vault>/qa/contrast-pairs.json
//     [ { "el": "body text",   "fg": "#f4efe7", "bg": "#16140f", "size": "body" },
//       { "el": "term-title",  "fg": "#8a8275", "bg": "#221e17", "size": "normal" },
//       { "el": "step idx",    "fg": "#d97757", "bg": "#1d1a14", "size": "large" },
//       { "el": "term dot",    "fg": "#3f3b35", "bg": "#221e17", "size": "decorative" } ]
//   Colors must be OPAQUE hex (#rgb or #rrggbb) — pre-composite any alpha over its real background first.
//
// size -> required ratio (the skill rule: body AAA, all other text AA; large-text AA allowance):
//   body=7 (AAA)  normal=4.5 (AA)  large=3 (AA large >=18pt / >=14pt bold)  decorative=skip
//
// Exit 0 = every text pair clears its threshold. Exit 1 = at least one FAIL. Exit 2 = usage/parse error.

import { readFileSync } from 'node:fs';

const NEED = { body: 7, normal: 4.5, large: 3, decorative: 0 };

function usage(msg) {
  if (msg) process.stderr.write(`contrast-gate: ${msg}\n`);
  process.stderr.write('usage: contrast-gate.mjs <pairs.json>\n');
  process.exit(2);
}

function parseHex(raw, where) {
  if (typeof raw !== 'string') usage(`${where}: color must be a hex string, got ${JSON.stringify(raw)}`);
  let h = raw.trim().replace(/^#/, '');
  if (/^[0-9a-fA-F]{3}$/.test(h)) h = h.split('').map((c) => c + c).join('');
  if (!/^[0-9a-fA-F]{6}$/.test(h)) usage(`${where}: not an opaque hex color: "${raw}" (pre-composite alpha first)`);
  return { r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16) };
}

const lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
const lum = ({ r, g, b }) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
function ratio(fg, bg) {
  const a = lum(fg), b = lum(bg);
  const hi = Math.max(a, b), lo = Math.min(a, b);
  return (hi + 0.05) / (lo + 0.05);
}

const file = process.argv[2];
if (!file) usage('missing <pairs.json>');

let pairs;
try {
  pairs = JSON.parse(readFileSync(file, 'utf8'));
} catch (e) {
  usage(`cannot read/parse ${file}: ${e.message}`);
}
if (!Array.isArray(pairs) || pairs.length === 0) usage(`${file}: expected a non-empty JSON array of pairs`);

console.log('== /supergoal contrast gate ==');
console.log(`pairs file: ${file}`);
console.log('  el                              fg        bg        ratio   need   result');

let fails = 0, checked = 0;
for (let i = 0; i < pairs.length; i++) {
  const p = pairs[i];
  const where = `pair[${i}]${p && p.el ? ` "${p.el}"` : ''}`;
  if (!p || typeof p !== 'object') usage(`${where}: not an object`);
  const size = (p.size || 'normal').toLowerCase();
  if (!(size in NEED)) usage(`${where}: unknown size "${p.size}" (use body|normal|large|decorative)`);
  const fg = parseHex(p.fg, `${where} fg`);
  const bg = parseHex(p.bg, `${where} bg`);
  const r = ratio(fg, bg);
  const need = NEED[size];
  let result;
  if (size === 'decorative') {
    result = 'n/a (decorative)';
  } else {
    checked++;
    const ok = r + 1e-9 >= need;
    if (!ok) fails++;
    result = ok ? 'PASS' : 'FAIL';
  }
  const el = String(p.el || `pair[${i}]`).slice(0, 30).padEnd(30);
  console.log(`  ${el}  ${String(p.fg).padEnd(8)}  ${String(p.bg).padEnd(8)}  ${r.toFixed(2).padStart(5)}  ${String(need || '-').padStart(4)}   ${result}`);
}

console.log(`  checked ${checked} text pair(s), ${fails} below threshold`);
if (fails > 0) {
  console.error(`CONTRAST-GATE FAIL: ${fails} text pair(s) below WCAG threshold — fix the palette (rewind to Build); never lower the threshold`);
  process.exit(1);
}
console.log('== CONTRAST GATE PASS ==');
