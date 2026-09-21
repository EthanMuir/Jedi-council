"""Settings must survive a .env with NO_LLM= and USE_DATA_FIXTURES= present
but blank -- exactly what .env.example ships, on purpose, to mean
"auto-detect". A real user hit this as a ValidationError on first setup:
pydantic-settings hands an env var that's present-but-empty to the field as
the literal string "", which fails bool parsing outright instead of
falling back to the field's None default."""
from __future__ import annotations

import pytest

from council.config import Settings


@pytest.mark.parametrize("field", ["no_llm", "use_data_fixtures"])
def test_blank_env_value_resolves_to_none(monkeypatch, field):
    monkeypatch.setenv(field.upper(), "")
    settings = Settings(_env_file=None)
    assert getattr(settings, field) is None


def test_resolved_no_llm_auto_detects_with_blank_env(monkeypatch):
    monkeypatch.setenv("NO_LLM", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    settings = Settings(_env_file=None)
    assert settings.resolved_no_llm is True


def test_explicit_bool_env_value_still_respected(monkeypatch):
    monkeypatch.setenv("NO_LLM", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    settings = Settings(_env_file=None)
    assert settings.no_llm is False
    assert settings.resolved_no_llm is False
