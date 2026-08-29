# Lesson: Unset ambient language when testing configuration precedence

- Date: 2026-08-29
- Tags: tests,environment,i18n,configuration
- Feature: linear-polling-schema-drift
- Stage: 4-build

## Trap

The verifier inherited `SYMPHONY_LANG=ko`. Setting it to `en` fixed tests for the default language but broke tests where workflow configuration explicitly selected Korean.

## Correct move

For the full suite, run `env -u SYMPHONY_LANG ...`. This removes the ambient override and lets tests cover both the default language and explicit workflow language precedence.

## Promote?

- none
