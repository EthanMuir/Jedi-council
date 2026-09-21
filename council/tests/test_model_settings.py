"""Task #74 -- persisted per-seat model overrides."""
from __future__ import annotations

import pytest

from council.engine import model_settings


@pytest.fixture
def conn(tmp_path):
    return model_settings.connect(str(tmp_path / "settings.db"))


def test_no_override_falls_back_to_recommended(conn):
    assert model_settings.get_effective_model(conn, "technician") == "claude-sonnet-5"
    assert model_settings.get_effective_model(conn, "prosecutor") == "claude-opus-5"


def test_set_override_takes_effect(conn):
    model_settings.set_override(conn, "technician", "gpt-5-nano")
    assert model_settings.get_effective_model(conn, "technician") == "gpt-5-nano"
    # other roles unaffected
    assert model_settings.get_effective_model(conn, "prosecutor") == "claude-opus-5"


def test_set_override_twice_updates_not_duplicates(conn):
    model_settings.set_override(conn, "technician", "gpt-5-nano")
    model_settings.set_override(conn, "technician", "gemini-3-pro")
    assert model_settings.get_effective_model(conn, "technician") == "gemini-3-pro"
    assert model_settings.get_overrides(conn) == {"technician": "gemini-3-pro"}


def test_clear_override_reverts_to_recommended(conn):
    model_settings.set_override(conn, "technician", "gpt-5-nano")
    model_settings.clear_override(conn, "technician")
    assert model_settings.get_effective_model(conn, "technician") == "claude-sonnet-5"


def test_clear_override_that_was_never_set_is_a_no_op(conn):
    model_settings.clear_override(conn, "technician")  # must not raise
    assert model_settings.get_effective_model(conn, "technician") == "claude-sonnet-5"


def test_set_override_rejects_unknown_role(conn):
    with pytest.raises(ValueError):
        model_settings.set_override(conn, "not_a_real_seat", "gpt-5")


def test_set_override_rejects_unknown_model_id(conn):
    with pytest.raises(ValueError):
        model_settings.set_override(conn, "technician", "not-a-real-model")


def test_get_overrides_reflects_all_set_roles(conn):
    model_settings.set_override(conn, "technician", "gpt-5-nano")
    model_settings.set_override(conn, "prosecutor", "gemini-3-pro")
    assert model_settings.get_overrides(conn) == {
        "technician": "gpt-5-nano",
        "prosecutor": "gemini-3-pro",
    }


def test_persists_across_connections(tmp_path):
    db_path = str(tmp_path / "settings.db")
    conn1 = model_settings.connect(db_path)
    model_settings.set_override(conn1, "technician", "gpt-5-nano")
    conn1.close()

    conn2 = model_settings.connect(db_path)
    assert model_settings.get_effective_model(conn2, "technician") == "gpt-5-nano"
