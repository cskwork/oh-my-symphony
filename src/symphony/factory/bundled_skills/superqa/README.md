# SuperQA bundled runtime

This is the factory-pinned SuperQA runtime copied by Oh My Symphony. From a
generated factory project, install the editable command with:

```bash
python -m pip install -e skills/superqa
python -m playwright install chromium
```

The editable install provides the `superqa` command and installs the declared
Python dependencies. Browser binaries are a separate Playwright install and
may require network access. See `SKILL.md` for the agent routes and
`reference/tui.md` for human-facing CLI, TUI, recording, and scheduling usage.
