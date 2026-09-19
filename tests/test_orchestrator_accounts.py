from symphony.orchestrator.accounts import AccountBench, resolve_account
from symphony.workflow.config import AgentAccount

A = AgentAccount(id="primary", env={"H": "/a"})
B = AgentAccount(id="secondary", env={"H": "/b"})
POOL = (A, B)


def test_empty_pool_resolves_to_none():
    assert resolve_account((), "codex", None, AccountBench()) is None


def test_prefers_first_when_unpinned():
    assert resolve_account(POOL, "codex", None, AccountBench()) == A


def test_prefers_the_pin():
    assert resolve_account(POOL, "codex", "secondary", AccountBench()) == B


def test_stale_pin_falls_back_to_first():
    assert resolve_account(POOL, "codex", "removed", AccountBench()) == A


def test_skips_a_benched_account():
    bench = AccountBench()
    bench.bench("codex", "primary", 60_000)
    assert resolve_account(POOL, "codex", None, bench) == B


def test_benched_pin_is_passed_over():
    bench = AccountBench()
    bench.bench("codex", "secondary", 60_000)
    assert resolve_account(POOL, "codex", "secondary", bench) == A


def test_all_benched_returns_earliest_expiry():
    now = [0.0]
    bench = AccountBench(clock=lambda: now[0])
    bench.bench("codex", "primary", 5_000)     # expires at 5.0
    bench.bench("codex", "secondary", 50_000)  # expires at 50.0
    now[0] = 1.0
    assert bench.is_benched("codex", "primary") is True
    assert bench.is_benched("codex", "secondary") is True
    assert resolve_account(POOL, "codex", None, bench) == A


def test_bench_expires():
    now = [0.0]
    bench = AccountBench(clock=lambda: now[0])
    bench.bench("codex", "primary", 1_000)
    now[0] = 100.0
    assert bench.is_benched("codex", "primary") is False


def test_bench_is_scoped_per_kind():
    bench = AccountBench()
    bench.bench("codex", "primary", 60_000)
    assert bench.is_benched("claude", "primary") is False
