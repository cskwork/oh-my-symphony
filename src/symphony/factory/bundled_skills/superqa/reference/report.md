# Reports - written for the person who did not run the test

Every run writes to `~/.superqa/reports/<stamp>-<name>/`:

- `report.html` - the deliverable. Open it, send it, archive it. Self-contained,
  screenshots inline, pass/fail badges, side-effect table.
- `report.md` - same content for chat/PR pasting.
- `step-NN.png` - screenshot after every step (including failures).

## Language

- Engine-generated labels follow the scenario's `language:` field (ko/en built in).
- Step descriptions are authored text - write them in the user's language when
  generating scenarios; they flow into the report verbatim.
- The agent's chat summary follows the conversation language regardless of the
  report language.

## Agent summary template (chat, after a run)

1. One line: overall result - "8개 시나리오 중 7개 성공, 1개 실패".
2. Failures first: scenario, failing step in plain words, screenshot path, suspected
   cause (bug / data / environment).
3. Side effects triaged per `reference/side-effects.md` - bug candidates first.
4. Report paths.
5. If REGRESSION: what changed vs the previous run (new failures, new effect types).

## Masking

Values of secret-flagged vars (password/token/etc.) are replaced with `******` in all
report text and error messages. Do not paste raw engine errors into chat without the
same masking - copy from the report, which is already masked.
