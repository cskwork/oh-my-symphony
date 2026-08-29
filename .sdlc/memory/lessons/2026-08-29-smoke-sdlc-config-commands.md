# Lesson: Smoke every SDLC proof command before approval

- Date: 2026-08-29
- Tags: sdlc,verification,configuration,environment
- Feature: linear-polling-schema-drift
- Stage: 4-build

## Trap

The initial `.sdlc/config.md` used a virtual environment without pip, tried to run the orchestrator inside its protected source repository, and inherited a language override that made the full suite non-deterministic. These failures appeared only during build and verifier rounds.

## Correct move

After initializing SDLC config, run each non-destructive build, test, lint, and run command once. Use a safe `--help` run command for protected source repositories. Remove ambient environment overrides that would change configuration precedence.

## Promote?

- skills/1-intent: after project initialization, smoke every `.sdlc/config.md` command before writing `intent.md`; replace unsafe run commands with a non-mutating startup check and record required environment isolation.
