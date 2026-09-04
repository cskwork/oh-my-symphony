# SDLC config

kit: /Users/danny/.pi/agent/skills/sdlc-kit   # re-point this if the kit is moved or cloned elsewhere
kit_version: v0.4.0   # kit version this project was seeded with

# Real commands agents must use for proof (fill these in — brownfield: copy from CI/Makefile)

# AGENTS: if a command below is empty when you need it, STOP and ask the human to fill it in

build: python3.12 -m pip wheel . --no-deps --wheel-dir /tmp/oh-my-symphony-wheel
test: env -u SYMPHONY_LANG .venv/bin/python -m pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80
lint: .venv/bin/python -m ruff check src tests && .venv/bin/python scripts/check_i18n.py && .venv/bin/symphony-pyright
run: .venv/bin/python -m symphony --help

# Human gates: intent only; spec, plan and ship auto-approve after their reviews (AGENTS.md rule 3).
# Set on the owner's explicit instruction, 2026-09-05.
lazymode: 3
