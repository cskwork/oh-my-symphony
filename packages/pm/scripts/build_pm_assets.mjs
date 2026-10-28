import { build } from 'esbuild';
import { createHash } from 'node:crypto';
import { copyFile, mkdir, readFile, readdir, rm, stat, unlink, writeFile } from 'node:fs/promises';
import path from 'node:path';

const root = process.cwd();
const vendor = path.join(root, 'src/symphony/pm/static/vendor');
await rm(vendor, { recursive: true, force: true });
await mkdir(path.join(vendor, 'chunks'), { recursive: true });
await copyFile(path.join(root, 'node_modules/tabulator-tables/dist/js/tabulator_esm.min.mjs'), path.join(vendor, 'tabulator.min.mjs'));
await copyFile(path.join(root, 'node_modules/tabulator-tables/dist/css/tabulator.min.css'), path.join(vendor, 'tabulator.min.css'));
await build({
  entryPoints: [path.join(root, 'scripts/pm_diagram_entry.mjs')],
  outdir: vendor,
  entryNames: 'diagram-entry',
  chunkNames: 'chunks/[name]-[hash]',
  bundle: true,
  splitting: true,
  format: 'esm',
  platform: 'browser',
  target: ['es2022'],
  minify: true,
  metafile: true,
  write: true,
});
// Mermaid's full bundle emits lazy loaders for every diagram even though the
// runtime wrapper accepts only flowcharts and ERDs. Remove the named
// non-allowlisted diagram modules while retaining shared layout chunks (ELK is
// required by Mermaid 12's ER renderer).
const chunkDir = path.join(vendor, 'chunks');
const prohibitedChunks = /^(?:gantt|sequence|classDiagram|stateDiagram|journey|mindmap|pieDiagram|gitGraph|architecture|kanban)/;
for (const name of await readdir(chunkDir)) {
  if (prohibitedChunks.test(name)) await unlink(path.join(chunkDir, name));
}
const files = [];
async function walk(dir) {
  for (const name of await readdir(dir)) {
    const full = path.join(dir, name);
    const info = await stat(full);
    if (info.isDirectory()) await walk(full);
    else {
      const data = await readFile(full);
      files.push({ path: path.relative(vendor, full).replaceAll(path.sep, '/'), bytes: info.size, sha256: createHash('sha256').update(data).digest('hex') });
    }
  }
}
await walk(vendor);
await writeFile(path.join(vendor, 'asset-manifest.json'), JSON.stringify({ pins: { tabulator: '6.6.1', mermaid: '12.1.0', dompurify: '3.4.16', esbuild: '0.25.10' }, files }, null, 2));
const lock = JSON.parse(await readFile(path.join(root, 'package-lock.json'), 'utf8'));
const identities = [];
for (const [packagePath, record] of Object.entries(lock.packages || {})) {
  if (!packagePath || !packagePath.includes('node_modules/')) continue;
  const name = packagePath.slice(packagePath.lastIndexOf('node_modules/') + 13);
  let license = record.license || 'SEE LICENSE FILE';
  if (name === 'khroma' && license === 'SEE LICENSE FILE') license = 'MIT (LICENSE file)';
  identities.push(`${name} ${record.version || ''} — ${license}`);
}
identities.sort((a, b) => a.localeCompare(b));
const notices = [
  'PM Workspace browser assets — generated locally; no CDN runtime dependency.',
  '',
  ...identities,
].join('\n');
await writeFile(path.join(vendor, 'THIRD_PARTY_NOTICES.txt'), `${notices}\n`);
