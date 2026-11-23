"""Replayable parity pins for ``gossamer.settings`` and ``gossamer.env``.

Every assertion here stays true regardless of the ambient environment, the
working directory or the user's home folder, so ``rust-anything diff`` can
replay the recorded calls later.

The file layer (discovery, parsing, mtime cache, permission warnings) is
covered by ``tests/test_settings.py``; those calls depend on ``monkeypatch``
state and temporary directories and therefore cannot be replayed out of
process.
"""

from __future__ import annotations

from gossamer import env, settings


def test_keystore_template_is_ordered_and_empty():
    template = settings.keystore_template()
    assert template["OPENALEX_KEY"] == ""
    assert template["GOOGLE_API_KEY"] == ""


def test_config_template_holds_documented_defaults():
    template = settings.config_template()
    assert template["max_concurrency"] == 8
    assert template["fetch_mode"] == "auto"
    assert template["guard"]["max_chunks"] == 40


def test_getenv_falls_back_to_the_default():
    assert env.getenv("GOSSAMER_PARITY_UNSET_KEY", "fallback") == "fallback"


def test_getenv_returns_none_without_a_default():
    assert env.getenv("GOSSAMER_PARITY_UNSET_KEY") is None


def test_getenv_bool_falls_back_to_the_default():
    assert env.getenv_bool("GOSSAMER_PARITY_UNSET_FLAG", True) is True


def test_clear_caches_is_idempotent():
    settings.clear_caches()
    settings.clear_caches()
