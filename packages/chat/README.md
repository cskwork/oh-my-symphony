# oh-my-symphony-chat

Optional operator chat for [oh-my-symphony](../../README.md): the chat panel
on the web board, `/api/v1/chat/*` routes (sessions and the WebSocket stream),
intent cards and intent filing. The core `oh-my-symphony` package runs
without it; the board, TUI, CLI and orchestrator do not need it.

Install it next to core from a checkout of this repository:

```bash
python -m pip install -e . -e packages/chat
```

The Python package keeps the `symphony.chat` import path: `symphony.chat`
(session manager), `symphony.chat.intent` (intent proposals) and
`symphony.chat.routes` (web routes). The browser UI stays in core and hides the
chat panel when this package is absent.

Licensed under Apache-2.0, like the core distribution; see `LICENSE` and
`NOTICE` at the repository root.
