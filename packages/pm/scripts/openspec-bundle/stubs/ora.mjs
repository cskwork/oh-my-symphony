// Spinner stub: Symphony runs OpenSpec non-interactively; messages go to stderr.
export default function ora(options) {
  const s = { text: typeof options === 'string' ? options : options?.text ?? '' };
  const say = (t) => { if (t ?? s.text) process.stderr.write(`${t ?? s.text}\n`); return s; };
  Object.assign(s, { start: (t) => { if (t) s.text = t; return s; }, stop: () => s, succeed: say, fail: say, warn: say, info: say, stopAndPersist: () => s, clear: () => s, render: () => s });
  return s;
}
