// Only the OpenSpec 1.14.2 calls Symphony makes.
import { Argument, Command } from 'commander';
import { createRequire } from 'node:module';
import { asStatus } from './node_modules/@fission-ai/openspec/dist/commands/shared-output.js';
import { ArchiveCommand } from './node_modules/@fission-ai/openspec/dist/core/archive.js';
import { registerSchemaCommand } from './node_modules/@fission-ai/openspec/dist/cli/commands/schema.js';
import { ValidateCommand } from './node_modules/@fission-ai/openspec/dist/commands/validate.js';
import { statusCommand, applyInstructionsCommand } from './node_modules/@fission-ai/openspec/dist/commands/workflow/index.js';

function failWithError(error, json) {
  if (json?.enabled) {
    console.log(JSON.stringify({ ...(json.payload ?? {}), status: [asStatus(error, json.fallbackCode ?? 'command_error')] }, null, 2));
    process.exitCode = 1; return;
  }
  console.error(`Error: ${error.message}`);
  const fix = error.diagnostic?.fix; if (fix) console.error(`Fix: ${fix}`);
  process.exitCode = process.exitCode ?? 1;
}
const { version } = createRequire(import.meta.url)('../../../package.json');
const program = new Command();
program.name('openspec').version(version);

// schema: keep only `validate` from upstream's group (which/fork/init are not registered; fork/init write files).
const donor = new Command();
registerSchemaCommand(donor);
const upstream = donor.commands.find((c) => c.name() === 'schema');
const schema = program.command('schema').description(upstream.description());
schema.hook('preAction', () => { console.error('Note: Schema commands are experimental and may change.'); });
schema.addCommand(upstream.commands.find((c) => c.name() === 'validate'));

program.command('status').option('--change <id>').option('--json')
  .action(async (o) => { try { await statusCommand(o); } catch (e) { failWithError(e, { enabled: o.json, fallbackCode: 'change_error' }); process.exit(1); } });
program.command('instructions').addArgument(new Argument('<artifact>').choices(['apply'])).option('--change <id>').option('--json')
  .action(async (_a, o) => { try { await applyInstructionsCommand(o); } catch (e) { failWithError(e, { enabled: o.json, fallbackCode: 'change_error' }); process.exit(1); } });
program.command('validate <item-name>').option('--type <type>').option('--strict').option('--json').option('--no-interactive')
  .action(async (n, o) => { try { await new ValidateCommand().execute(n, o); } catch (e) { failWithError(e, { enabled: o?.json, fallbackCode: 'validate_error' }); process.exit(1); } });
program.command('archive <change-name>').option('-y, --yes').option('--json')
  .action(async (n, o) => { try { await new ArchiveCommand().execute(n, o); } catch (e) { failWithError(e); process.exit(1); } });
program.parse(process.argv);
