"""Task #63 -- a real deliberation billed real Anthropic API cost despite
the user believing NO_LLM=true made it free. The gate that actually
decides whether a network call happens was never wrong (LLMClient checks
settings.resolved_no_llm before every single call, the only place the SDK
is ever touched -- see test_llm_client*.py) but nothing printed which mode
was active before spending money, so a misconfigured .env / wrong-terminal
env var went unnoticed until the bill showed it. `_print_llm_mode` makes
that unmissable."""
from __future__ import annotations

import council.cli as cli
from council.config import Settings


def test_fixture_mode_prints_zero_cost_guarantee(capsys):
    settings = Settings(no_llm=True, anthropic_api_key="test-key")
    cli._print_llm_mode(settings)
    out = capsys.readouterr().out
    assert "LLM MODE: FIXTURE" in out
    assert "$0 cost, guaranteed" in out


def test_live_mode_with_key_prints_billing_warning(capsys):
    settings = Settings(no_llm=False, anthropic_api_key="sk-real-looking-key")
    cli._print_llm_mode(settings)
    out = capsys.readouterr().out
    assert "LLM MODE: LIVE" in out
    assert "a key is configured" in out


def test_live_mode_with_no_key_warns_calls_will_fail(capsys):
    settings = Settings(no_llm=False, anthropic_api_key="")
    cli._print_llm_mode(settings)
    out = capsys.readouterr().out
    assert "LLM MODE: LIVE" in out
    assert "NO KEY CONFIGURED" in out


def test_auto_detected_fixture_mode_with_no_key_prints_fixture_not_live(capsys):
    # The common real-world case: no NO_LLM set explicitly at all, and no
    # key present -- resolved_no_llm auto-detects True.
    settings = Settings(no_llm=None, anthropic_api_key="")
    cli._print_llm_mode(settings)
    out = capsys.readouterr().out
    assert "LLM MODE: FIXTURE" in out
