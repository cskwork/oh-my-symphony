import { build, version as esbuildVersion } from 'esbuild';
import { createHash } from 'node:crypto';
import { cp, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const buildDir = path.dirname(fileURLToPath(import.meta.url));
process.chdir(buildDir);
const output = path.resolve('../../src/symphony/pm/resources/openspec-cli');
const upstream = 'node_modules/@fission-ai/openspec';
const require = createRequire(import.meta.url);
const packageJson = async (dir) => JSON.parse(await readFile(path.join(dir, 'package.json'), 'utf8'));
const openspec = await packageJson(upstream);
const commander = await packageJson(path.dirname(require.resolve('commander')));
if (openspec.version !== '1.14.2' || esbuildVersion !== '0.25.10') {
  throw new Error('Build requires OpenSpec 1.14.2 and esbuild 0.25.10');
}
if (commander.version !== '14.0.3') throw new Error('Build requires commander 14.0.3');

const result = await build({
  absWorkingDir: buildDir,
  entryPoints: ['entry.mjs'],
  outfile: 'openspec.mjs',
  bundle: true,
  platform: 'node',
  format: 'esm',
  target: 'node20',
  minify: true,
  metafile: true,
  write: false,
  alias: {
    '@inquirer/prompts': './stubs/inquirer.mjs',
    '@inquirer/core': './stubs/inquirer.mjs',
    ora: './stubs/ora.mjs',
    braces: './stubs/braces.cjs',
  },
  banner: { js: "import { createRequire as __cr } from 'node:module'; const require = __cr(import.meta.url);" },
  plugins: [{
    name: 'relative-imports',
    setup(api) {
      api.onResolve({ filter: /.*/ }, (args) => {
        if (path.isAbsolute(args.path) || path.win32.isAbsolute(args.path)) {
          throw new Error(`Absolute import is forbidden: ${args.path}`);
        }
      });
    },
  }],
});

// Collect only packages whose code enters the bundle, including nested installs.
const packageDirs = new Set();
for (const input of Object.keys(result.metafile.inputs)) {
  if (path.isAbsolute(input) || path.win32.isAbsolute(input)) {
    throw new Error(`Absolute metafile input is forbidden: ${input}`);
  }
  const marker = 'node_modules/';
  const index = input.lastIndexOf(marker);
  if (index < 0) continue;
  const parts = input.slice(index + marker.length).split('/');
  const name = parts.slice(0, parts[0].startsWith('@') ? 2 : 1).join('/');
  packageDirs.add(input.slice(0, index + marker.length) + name);
}
const packages = [];
const notices = [];
for (const dir of [...packageDirs].sort()) {
  const { name, version, license } = await packageJson(dir);
  if (name === 'commander' && version !== '14.0.3') {
    throw new Error('Bundled commander must be 14.0.3');
  }
  if (/posthog|telemetry/i.test(name)) throw new Error(`Forbidden package: ${name}`);
  const licenses = (await readdir(dir, { withFileTypes: true }))
    .filter((item) => item.isFile() && /^(licen[cs]e|copying)/i.test(item.name))
    .map((item) => item.name).sort();
  if (!licenses.length) throw new Error(`Missing license file: ${name} ${version}`);
  packages.push({ name, version, license });
  notices.push(`${name} ${version} — ${license}\n\n` + (await Promise.all(
    licenses.map(async (file) => `${file}:\n${await readFile(path.join(dir, file), 'utf8')}`),
  )).join('\n'));
}
if (/posthog|telemetry/i.test(result.outputFiles[0].text)) {
  throw new Error('Forbidden code in bundle');
}

// Validate all inputs before replacing the generated resource tree.
await rm(output, { recursive: true, force: true });
await mkdir(path.join(output, 'lib/openspec/cli'), { recursive: true });
await writeFile(path.join(output, 'lib/openspec/cli/openspec.mjs'), result.outputFiles[0].contents);
await writeFile(path.join(output, 'package.json'), JSON.stringify({
  name: openspec.name, version: openspec.version, license: openspec.license, type: 'module',
}, null, 2) + '\n');
await cp(path.join(upstream, 'LICENSE'), path.join(output, 'LICENSE'));
await cp(path.join(upstream, 'schemas/spec-driven'), path.join(output, 'schemas/spec-driven'), { recursive: true });
await writeFile(path.join(output, 'THIRD_PARTY_NOTICES.txt'), notices.join('\n\n') + '\n');
const files = [];
async function walk(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  for (const item of entries.sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0)) {
    const full = path.join(dir, item.name);
    if (item.isDirectory()) await walk(full);
    else {
      const data = await readFile(full);
      files.push({ path: path.relative(output, full).split(path.sep).join('/'), bytes: data.length,
        sha256: createHash('sha256').update(data).digest('hex') });
    }
  }
}
await walk(output);
await writeFile(path.join(output, 'bundle-manifest.json'), JSON.stringify({
  openspec: openspec.version, esbuild: esbuildVersion, node_min: '20.19.0', packages, files,
}, null, 2) + '\n');
console.log(`Built OpenSpec ${openspec.version}: ${result.outputFiles[0].contents.length} bytes, ${packages.length} packages`);
