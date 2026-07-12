#!/usr/bin/env node
// contrast-gate - WCAG contrast is computed, not eyeballed.
// Adapted from supergoal-skill (cskwork, MIT). The critic enumerates the FG/BG pairs it found
// in the CSS (the judgment a reviewer can audit); THIS script computes WCAG 2.x ratios and the
// pass/fail verdict deterministically. The agent cannot fudge the math; a silent sub-AA pair
// cannot pass. NEVER edit this script to make a failing palette pass - fix the colors instead.
//
// Input pairs.json: [{ "el": "body text", "fg": "#f4efe7", "bg": "#16140f", "size": "body" }, ...]
// size thresholds (WCAG AA): body/normal = 4.5, large = 3, aaa = 7, decorative = skip.
// Exit 0 = every text pair clears its threshold. Exit 1 = at least one FAIL. Exit 2 = usage/parse error.

import { readFileSync } from "node:fs";

const NEED = { aaa: 7, body: 4.5, normal: 4.5, large: 3, decorative: 0 };

function usage(msg) {
  if (msg) console.error(`contrast-gate: ${msg}`);
  console.error("usage: node contrast-gate.mjs <pairs.json>");
  process.exit(2);
}

function hex(h) {
  const s = String(h).trim().replace(/^#/, "");
  const full = s.length === 3 ? s.split("").map((c) => c + c).join("") : s;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return null;
  return { r: parseInt(full.slice(0, 2), 16), g: parseInt(full.slice(2, 4), 16), b: parseInt(full.slice(4, 6), 16) };
}

const lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
const lum = ({ r, g, b }) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
function ratio(fg, bg) {
  const a = lum(fg), b = lum(bg);
  const hi = Math.max(a, b), lo = Math.min(a, b);
  return (hi + 0.05) / (lo + 0.05);
}

const file = process.argv[2];
if (!file) usage("missing <pairs.json>");

let pairs;
try {
  pairs = JSON.parse(readFileSync(file, "utf8"));
} catch (e) {
  usage(`cannot read/parse ${file}: ${e.message}`);
}
if (!Array.isArray(pairs)) usage("pairs.json must be an array");

let failed = 0;
console.log("element                          fg        bg        ratio  need  verdict");
for (let i = 0; i < pairs.length; i++) {
  const p = pairs[i];
  const fg = hex(p.fg), bg = hex(p.bg);
  const size = (p.size || "normal").toLowerCase();
  const need = NEED[size] ?? NEED.normal;
  if (!fg || !bg) usage(`pair ${i} (${p.el || "?"}) has a bad hex: fg=${p.fg} bg=${p.bg}`);
  const r = ratio(fg, bg);
  const pass = r >= need;
  if (!pass) failed++;
  const label = String(p.el || `pair ${i}`).slice(0, 32).padEnd(32);
  console.log(`${label} ${String(p.fg).padEnd(9)} ${String(p.bg).padEnd(9)} ${r.toFixed(2).padStart(5)}  ${String(need).padStart(4)}  ${pass ? "ok" : "FAIL below threshold"}`);
}

if (failed > 0) {
  console.log(`\n${failed} pair(s) below threshold. Fix the colors or lower the size tier honestly.`);
  process.exit(1);
}
console.log("\n== CONTRAST GATE PASS ==");
process.exit(0);
