// braces <=3.0.3 recurses without a depth limit (GHSA-vfj7-8cjw-p6xm, no
// patched release): deeply nested patterns crash Node with a stack overflow.
// Refuse them before braces walks them; OpenSpec reports the thrown error.
const braces = require('../node_modules/braces/index.js');

const MAX_DEPTH = 16;

function check(input) {
  for (const pattern of [].concat(input)) {
    if (typeof pattern !== 'string') continue;
    let depth = 0;
    for (let i = 0; i < pattern.length; i++) {
      if (pattern[i] === '\\') i++;
      else if (pattern[i] === '}' && depth > 0) depth--;
      else if (pattern[i] === '{' && ++depth > MAX_DEPTH) {
        throw new Error(`Brace pattern nests more than ${MAX_DEPTH} levels`);
      }
    }
  }
}

const guarded = (fn) => (input, options) => {
  check(input);
  return fn(input, options);
};

module.exports = Object.assign(guarded(braces), {
  parse: guarded(braces.parse),
  stringify: guarded(braces.stringify),
  compile: guarded(braces.compile),
  expand: guarded(braces.expand),
  create: guarded(braces.create),
});
