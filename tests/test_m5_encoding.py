# tests/test_m5_encoding.py
"""M5 — gpt-4o was mapped to cl100k_base, but tiktoken (and OpenAI)
use o200k_base for it. resolve_encoding now prefers tiktoken's own
model map when available and falls back to the (updated) local table
for models tiktoken does not know (e.g. Anthropic) or when tiktoken
is missing.
"""

import stitch_web_researcher.token_budget as token_budget
from stitch_web_researcher.token_budget import count_tokens, resolve_encoding


class TestTiktokenPreferred:
    def test_gpt_4o_uses_o200k(self):
        """The M5 bug: gpt-4o must resolve to o200k_base, not cl100k_base."""
        assert resolve_encoding("gpt-4o") == "o200k_base"

    def test_gpt_4o_mini_uses_o200k(self):
        assert resolve_encoding("gpt-4o-mini") == "o200k_base"

    def test_gpt_4_still_cl100k(self):
        assert resolve_encoding("gpt-4") == "cl100k_base"

    def test_date_suffixed_gpt_4o(self):
        assert resolve_encoding("gpt-4o-2024-08-06") == "o200k_base"

    def test_count_tokens_matches_tiktoken_directly(self):
        """count_tokens must use the same encoding tiktoken itself
        assigns to gpt-4o (proves the o200k path is taken)."""
        import tiktoken

        text = "The quick brown fox jumps over the lazy dog." * 5
        direct = len(tiktoken.encoding_for_model("gpt-4o").encode(text))
        assert count_tokens(text, "gpt-4o") == direct


class TestTableFallback:
    """Models tiktoken does not know fall back to the local table."""

    def test_claude_via_table(self):
        assert resolve_encoding("claude-3-sonnet") == "cl100k_base"

    def test_unknown_defaults_to_cl100k(self):
        assert resolve_encoding("some-unknown-model") == "cl100k_base"

    def test_tiktoken_absent_gpt_4o_table_updated(self, monkeypatch):
        """With tiktoken unavailable the (updated) local table still
        returns o200k_base for the gpt-4o family."""
        monkeypatch.setattr(token_budget, "_tiktoken_available", False)
        assert resolve_encoding("gpt-4o") == "o200k_base"
        assert resolve_encoding("gpt-4o-mini") == "o200k_base"
        assert resolve_encoding("gpt-4") == "cl100k_base"

    def test_tiktoken_absent_prefix_match(self, monkeypatch):
        """Prefix fallback for date-suffixed models tiktoken/table
        don't list exactly (table path, not tiktoken)."""
        monkeypatch.setattr(token_budget, "_tiktoken_available", False)
        assert resolve_encoding("claude-3-sonnet-20241022") == "cl100k_base"
        assert resolve_encoding("gpt-4o-some-future-date") == "o200k_base"

    def test_tiktoken_absent_count_tokens_char_fallback(self, monkeypatch):
        """count_tokens degrades to the char heuristic (no crash)."""
        monkeypatch.setattr(token_budget, "_tiktoken_available", False)
        n = count_tokens("x" * 400, "gpt-4o")
        assert n == 100  # len // 4
