#!/usr/bin/env node
// anti-slop-gate - deterministic detector for the machine-checkable AI-design tells.
// The designer ships UI; THIS script scans the built source (HTML/JSX/TSX/CSS/MD) for the
// banned patterns in reference/taste-core.md + reference/impeccable-rules.md and returns a
// pass/fail verdict the critic can audit. The agent cannot eyeball "looks clean" past it.
// NEVER edit this script to make a failing surface pass - fix the design instead.
//
// Concepts distilled from impeccable (Apache-2.0); this is an independent implementation.
//
// Per-line escape hatch: a line containing "taste-ok" suppresses ALL rules on that line
// (use for a deliberate, justified choice, e.g. /* taste-ok: Inter neutral read */).
//
// Usage: node anti-slop-gate.mjs <file> [<file> ...]
// Exit 0 = no violations. Exit 1 = at least one violation. Exit 2 = usage/read error.

import { readFileSync } from "node:fs";

function usage(msg) {
  if (msg) console.error(`anti-slop-gate: ${msg}`);
  console.error("usage: node anti-slop-gate.mjs <file> [<file> ...]");
  process.exit(2);
}

// Overshoot easing = bounce/elastic feel. Parse cubic-bezier control points.
function bezierOvershoots(line) {
  const m = line.match(/cubic-bezier\(\s*([\d.+-]+)\s*,\s*([\d.+-]+)\s*,\s*([\d.+-]+)\s*,\s*([\d.+-]+)\s*\)/i);
  if (!m) return false;
  const p2 = parseFloat(m[2]);
  const p4 = parseFloat(m[4]);
  return p2 < 0 || p2 > 1 || p4 < 0 || p4 > 1;
}

// AI-purple accent hexes (violet/purple/fuchsia 400-700 family).
const PURPLE_HEX = /#(7c3aed|8b5cf6|a855f7|9333ea|6d28d9|7e22ce|c084fc|a78bfa|d8b4fe|6366f1|818cf8)\b/i;
const PURPLE_TW = /\b(bg|text|from|via|to|border|ring|fill|stroke)-(purple|violet|fuchsia)-(300|400|500|600|700|800)\b/;

const RULES = [
  { id: "em-dash", msg: "em/en dash as separator (use hyphen, comma, colon, paren, line break)",
    test: (l) => /[–—]/.test(l) },
  { id: "ai-purple", msg: "AI-purple accent (lock a deliberate, on-brand accent instead)",
    test: (l) => PURPLE_HEX.test(l) || PURPLE_TW.test(l) },
  { id: "inter-default", msg: "Inter font (avoid as default; add 'taste-ok' if a justified neutral read)",
    test: (l) => /font-family\s*:\s*[^;{]*\bInter\b/i.test(l) || /fontFamily\s*[:=]\s*[^,;\n]*\bInter\b/i.test(l) || /font-\[?['"]?Inter\b/i.test(l) },
  { id: "bounce-easing", msg: "bounce/elastic easing (reads dated; use a calm cubic-bezier)",
    test: (l) => bezierOvershoots(l) || /(ease(InOut|Out|In)?Back|ease(Out|In)Bounce|back\.(in|out|inOut)|elastic\.(in|out|inOut)|Bounce\.easeOut)/.test(l) },
  { id: "pure-bw", msg: "pure #000/#fff (tint to off-black/off-white)",
    test: (l) => /#000(000)?\b/i.test(l) || /#fff(fff)?\b/i.test(l) || /rgba?\(\s*0\s*,\s*0\s*,\s*0\s*[,)]/.test(l) || /rgba?\(\s*255\s*,\s*255\s*,\s*255\s*[,)]/.test(l) },
  { id: "black-shadow", msg: "heavy pure-black shadow on (likely) light bg (tint the shadow)",
    test: (l) => /box-shadow[^;]*rgba?\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0?\.[3-9]/i.test(l) || /(shadow|drop-shadow)[^;]*#000(000)?\b/i.test(l) },
  { id: "placeholder-name", msg: "placeholder identity (use real or domain-plausible names)",
    test: (l) => /\b(john doe|jane doe|acme|nexus|smartflow|lorem ipsum)\b/i.test(l) },
  { id: "filler-verb", msg: "AI filler verb (rewrite with concrete copy)",
    test: (l) => /\b(elevate|seamless|unleash|next-gen|revolutionize|supercharge)\b/i.test(l) },
  { id: "scroll-listener", msg: "manual scroll listener / scrollY in render (use IntersectionObserver or Motion)",
    test: (l) => /addEventListener\(\s*['"]scroll['"]/.test(l) || /window\.(scrollY|onscroll)/.test(l) },
];

const files = process.argv.slice(2);
if (files.length === 0) usage("missing <file>");

let findings = [];
let suppressed = 0;
let read = 0;

for (const file of files) {
  let text;
  try {
    text = readFileSync(file, "utf8");
  } catch (e) {
    console.error(`anti-slop-gate: cannot read ${file}: ${e.message}`);
    continue;
  }
  read++;
  const lines = text.split(/\r?\n/);
  lines.forEach((line, i) => {
    if (/taste-ok/i.test(line)) { suppressed++; return; }
    for (const rule of RULES) {
      if (rule.test(line)) {
        findings.push({ file, line: i + 1, id: rule.id, msg: rule.msg, snippet: line.trim().slice(0, 80) });
      }
    }
  });
}

if (read === 0) usage("no readable files");

if (findings.length > 0) {
  console.log("== ANTI-SLOP GATE: FAIL ==");
  for (const f of findings) {
    console.log(`${f.file}:${f.line}  [${f.id}]  ${f.msg}`);
    console.log(`    > ${f.snippet}`);
  }
  console.log(`\n${findings.length} violation(s) across ${read} file(s)${suppressed ? `, ${suppressed} line(s) suppressed by taste-ok` : ""}.`);
  console.log("Fix the design, not this gate.");
  process.exit(1);
}

console.log(`== ANTI-SLOP GATE: PASS == (${read} file(s)${suppressed ? `, ${suppressed} taste-ok suppression(s)` : ""})`);
process.exit(0);
