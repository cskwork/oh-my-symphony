# DB access - optional read-only evidence

Use this when a GREENFIELD, DEBUG, LEGACY, or QA-ONLY task depends on database truth:
schema shape, persisted state, source-of-truth UI values, test auth, or dataset/env diffs.
DB-independent abstraction: the same contract covers PostgreSQL, MySQL, and SQLite.
Skip the DB phase when the user does not want an agent DB connection. If DB truth is load-bearing but
access is missing, skipped, or unsafe, record `DB evidence: Not covered` in the report with the concrete
blocker and residual risk instead of treating the DB check as passed.

## Connection

- Default to the cross-platform Node runner: `node templates/db-access/db-access.mjs <operation>`.
- Windows may use `templates\db-access\db-access.cmd`; macOS/Linux may use the `.sh` wrappers.
- The runner works without any external skill; it shells out to native database clients only.
- Supported clients: `psql` for PostgreSQL, `mysql` for MySQL, `sqlite3` for SQLite.
- Use `templates/db-access/.env.example` as the template.
- Default secret path: `.domain-agent/db/.env`, or `DB_ENV_FILE=<path>` for another gitignored path.
- If the `.env` file does not exist, ask the user to fill it, provide a path, or skip the DB phase.
- Never hardcode host, user, password, DSN, token, or PII in tracked files, prompts, reports, or tests.
- External helpers such as `postgres-intelligence` are optional only; do not require them.

## Read-only (hard rule)

- Issue ONLY `SELECT`/`WITH`/`SHOW`/`DESCRIBE`/`EXPLAIN`.
- NEVER issue write or admin SQL: `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `REPLACE`, `CREATE`,
  `ALTER`, `DROP`, `TRUNCATE`, `GRANT`, `REVOKE`, `CALL`, `COPY`, `VACUUM`, `ANALYZE`,
  `ATTACH`, `DETACH`, or write pragmas.
- Prefer a read-only DB account. If only a write-capable account exists, still issue read-only SQL only.
- For SQLite, open the database read-only (`sqlite3 -readonly <file>`).
- Do not weaken the guard or edit gate/scripts to pass a non-compliant DB run.

## Operations

| Operation | Purpose | Output |
| --- | --- | --- |
| `check-connection` | prove the configured DB opens read-only | dialect/client + pass/fail |
| `schema-summary` | inspect table/column shape | compact metadata, no row data |
| `read-only-query` | fetch named expected values or diffs | small named values/diff only |

Run DB reads inside the dedicated `db-reader` subagent (`agents/db-reader.md`), separate from browser `qa-auditor`.
Return pass/fail and a small diff; never paste raw row dumps, secrets, credentials, tokens, or PII.
`db-reader` may write sanitized expected values to `qa/expected.md`; auth stays transient only.

## Record

In `QA.md` `## QA`, add a `DB:` line naming dialect/client and read-only status, e.g.
`DB: postgres (read-only via supergoal db-access)`.
List checks by name with pass/fail and small diff. No credentials, no raw rows, no PII.
