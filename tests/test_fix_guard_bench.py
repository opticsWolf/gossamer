# tests/test_fix_guard_bench.py
"""Bugfix 8 — the guard's cost and accuracy must be measurable.

Making the guard optional and configurable was only half the Prio-2
requirement; the other half was being able to *check its impact*. Every
control existed — ``GuardConfig``, the five scopes, the ``STITCH_GUARD_*``
passthrough, ``get_stats()["guard"]`` — but ``benchmarks.py`` had no guard
scenario and there was no labelled corpus, so the overhead had never been
measured and nothing decided whether ``redact`` mode is safe to default to.

These tests exercise the harness, not the detector: ``jailguard`` is an
optional extra and is absent in CI, so the benchmark falls back to a stub
model injected into the real ``JailGuardGuard``. That keeps the chunking,
the verdict cache and the stats path under test; only the accuracy numbers
need the extra installed.
"""

import benchmarks
import pytest

from stitch_web_researcher import guard as guard_mod


class TestCorpus:
    def test_corpus_loads(self):
        corpus = benchmarks.load_guard_corpus()
        assert corpus, "the labelled corpus must ship with the tests"

    def test_both_labels_are_present(self):
        labels = {injected for _n, injected, _t in benchmarks.load_guard_corpus()}
        assert labels == {True, False}, "a corpus of one class measures nothing"

    def test_documents_are_not_empty(self):
        for name, _injected, text in benchmarks.load_guard_corpus():
            assert text.strip(), name

    def test_benign_set_includes_security_prose(self):
        # The expensive false positive is a page that *discusses* prompt
        # injection. If that case leaves the corpus, the FP rate stops
        # meaning anything for our traffic mix.
        texts = [
            t for n, injected, t in benchmarks.load_guard_corpus()
            if not injected
        ]
        assert any("system prompt" in t.lower() for t in texts)


class TestBackendFallback:
    def test_falls_back_to_a_stub_when_jailguard_is_absent(self):
        make, backend = benchmarks._guard_backend()
        try:
            import jailguard  # noqa: F401
        except ImportError:
            assert "stub" in backend
        else:
            assert backend == "jailguard"

    def test_disabled_backend_is_the_real_noop_guard(self):
        make, _backend = benchmarks._guard_backend()
        assert isinstance(make(False), guard_mod.NoopGuard)

    def test_enabled_backend_is_the_real_guard_class(self):
        # Only the model is stubbed: chunking, caching and stats stay
        # production code, which is the point of measuring at all.
        make, _backend = benchmarks._guard_backend()
        assert isinstance(make(True), guard_mod.JailGuardGuard)


class TestScenariosRun:
    def test_bench_guard_reports_without_the_optional_dependency(self, capsys):
        result = benchmarks.bench_guard()
        out = capsys.readouterr().out
        assert result is not None
        assert "Guard off:" in out and "Guard on:" in out
        assert "Overhead:" in out

    def test_bench_guard_prints_the_stats_block(self, capsys):
        benchmarks.bench_guard()
        out = capsys.readouterr().out
        for field in ("calls", "chunks scanned", "p50 / p95 ms", "flag rate"):
            assert field in out

    def test_corpus_scenario_reports_both_rates(self, capsys):
        counts = benchmarks.bench_guard_corpus()
        out = capsys.readouterr().out
        assert set(counts) == {"tp", "fp", "tn", "fn"}
        assert sum(counts.values()) == len(benchmarks.load_guard_corpus())
        assert "false-positive rate" in out
        assert "detection rate" in out

    def test_corpus_scenario_names_a_redact_verdict(self, capsys):
        # The measurement exists to decide something; the report must say
        # what it decided.
        benchmarks.bench_guard_corpus()
        assert "verdict" in capsys.readouterr().out


class TestCliWiring:
    @pytest.mark.parametrize("flag", ["--guard", "--corpus"])
    def test_flags_run_offline_scenarios_only(self, flag, capsys):
        # Neither flag may reach the network scenarios: CI has no egress.
        benchmarks.main([flag])
        out = capsys.readouterr().out
        assert "Rust Core Fetch" not in out
        assert "Prompt-injection guard" in out
