#!/usr/bin/env node
import { existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";

const DEFAULT_ENV_FILE = ".domain-agent/db/.env";
const SECRET_KEY = /(pass|password|pwd|token|secret|dsn)/i;

function fail(message, code = 1) {
  console.error(`DB-ACCESS FAIL: ${message}`);
  process.exit(code);
}

function usage() {
  console.error("Usage: node templates/db-access/db-access.mjs <check-connection|schema-summary|read-only-query> [SQL]");
  process.exit(64);
}

function parseEnvLine(line) {
  const trimmed = line.trim();
  if (!trimmed || trimmed.startsWith("#")) return null;
  const raw = trimmed.startsWith("export ") ? trimmed.slice(7).trim() : trimmed;
  const match = raw.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
  if (!match) return null;
  let value = match[2].trim();
  if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
    value = value.slice(1, -1);
  }
  return [match[1], value];
}

function loadEnv() {
  const envFile = process.env.DB_ENV_FILE || DEFAULT_ENV_FILE;
  if (!existsSync(envFile)) {
    console.error(`DB env missing: ${envFile}`);
    console.error("Ask the user to fill it from templates/db-access/.env.example, provide DB_ENV_FILE, or skip the DB phase.");
    process.exit(2);
  }

  const loaded = {};
  for (const line of readFileSync(envFile, "utf8").split(/\r?\n/)) {
    const parsed = parseEnvLine(line);
    if (parsed) loaded[parsed[0]] = parsed[1];
  }

  const env = { ...process.env, ...loaded };
  if (!["postgres", "mysql", "sqlite"].includes(env.DB_DIALECT || "")) {
    fail("DB_DIALECT must be postgres, mysql, or sqlite");
  }
  return env;
}

function redact(text, env) {
  let out = text;
  for (const [key, value] of Object.entries(env)) {
    if (!SECRET_KEY.test(key) || !value || value.length < 4) continue;
    out = out.split(value).join("[redacted]");
  }
  return out;
}

function readonlyGuard(sql) {
  const compact = sql.replace(/\s+/g, " ").trim();
  if (!/^(select|with|show|describe|explain)($|[\s;(])/i.test(compact)) {
    fail("SQL must start with SELECT, WITH, SHOW, DESCRIBE, or EXPLAIN");
  }

  if (/(^|[^A-Za-z0-9_])(insert|update|delete|merge|replace|create|alter|drop|truncate|grant|revoke|call|copy|vacuum|analyze|attach|detach)([^A-Za-z0-9_]|$)/i.test(compact)) {
    fail("write/admin SQL rejected");
  }

  if (/(^|[^A-Za-z0-9_])pragma\s+[^;]*(=|journal_mode|writable_schema|synchronous|locking_mode)($|[^A-Za-z0-9_])/i.test(compact)) {
    fail("write-capable SQLite PRAGMA rejected");
  }
}

function required(env, key) {
  if (!env[key]) fail(`${key} is required`);
  return env[key];
}

function run(command, args, env) {
  const result = spawnSync(command, args, {
    env,
    encoding: "utf8",
    shell: false,
    maxBuffer: 2 * 1024 * 1024,
  });

  if (result.error) {
    if (result.error.code === "ENOENT") fail(`missing client: ${command}`);
    fail(result.error.message);
  }

  if (result.stdout) process.stdout.write(redact(result.stdout, env));
  if (result.stderr) process.stderr.write(redact(result.stderr, env));
  process.exitCode = result.status ?? 1;
}

function runSql(sql, env) {
  readonlyGuard(sql);

  switch (env.DB_DIALECT) {
    case "postgres":
      run("psql", ["-X", "-v", "ON_ERROR_STOP=1", "-A", "-F", "\t", "-c", sql], env);
      break;
    case "mysql":
      run("mysql", [
        `--host=${required(env, "MYSQL_HOST")}`,
        `--port=${env.MYSQL_PORT || "3306"}`,
        `--user=${required(env, "MYSQL_USER")}`,
        `--database=${required(env, "MYSQL_DATABASE")}`,
        "--batch",
        "--raw",
        "--execute",
        sql,
      ], env);
      break;
    case "sqlite":
      run("sqlite3", ["-readonly", required(env, "SQLITE_DB_PATH"), sql], env);
      break;
  }
}

function queryFor(command, env) {
  switch (command) {
    case "check-connection":
      if (env.DB_DIALECT === "postgres") return "SELECT current_database() AS database_name, current_schema() AS schema_name;";
      if (env.DB_DIALECT === "mysql") return "SELECT DATABASE() AS database_name;";
      return "SELECT sqlite_version() AS sqlite_version;";
    case "schema-summary":
      if (env.DB_DIALECT === "postgres") {
        return `SELECT table_schema, table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY table_schema, table_name, ordinal_position
LIMIT 500;`;
      }
      if (env.DB_DIALECT === "mysql") {
        return `SELECT table_schema, table_name, column_name, column_type
FROM information_schema.columns
WHERE table_schema = DATABASE()
ORDER BY table_name, ordinal_position
LIMIT 500;`;
      }
      return `SELECT name, sql
FROM sqlite_schema
WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'
ORDER BY name
LIMIT 200;`;
    default:
      return null;
  }
}

const [command, ...rest] = process.argv.slice(2);
if (!command) usage();

const env = loadEnv();
if (command === "read-only-query") {
  const sql = rest.join(" ").trim();
  if (!sql) usage();
  runSql(sql, env);
} else {
  const sql = queryFor(command, env);
  if (!sql) usage();
  runSql(sql, env);
  if (command === "check-connection" && process.exitCode === 0) {
    console.log(`DB: ${env.DB_DIALECT} (read-only via supergoal db-access)`);
  }
}
