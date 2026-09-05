# tests/test_m4_truncate_fallback.py
"""M4 — without tiktoken, truncate_to_tokens sliced negatively for
small budgets: with the default ellipsis (34 chars) and max_tokens=2,
it computed text[:8-34] == text[:-26], returning 74 chars of a 100-char
string for a 2-token budget. The cut is now clamped at 0, so a budget
smaller than the ellipsis returns just the truncation marker.
"""

import gossamer.token_budget as token_budget
from gossamer.token_budget import truncate_to_tokens

ELLIPSIS = "\n\n... [truncated for token budget]"


class TestFallbackClamp:
    """Char-based fallback (tiktoken disabled)."""

    def test_small_budget_returns_just_ellipsis(self, monkeypatch):
        """The exact M4 case: 2-token budget, 100-char text. Before the
        fix this returned text[:-26] + ellipsis (74 content chars)."""
        monkeypatch.setattr(token_budget, "_tiktoken_available", False)
        text = "x" * 100
        result = truncate_to_tokens(text, 2)
        assert result == ELLIPSIS
        assert len(result) == len(ELLIPSIS)

    def test_zero_content_cut_is_safe(self, monkeypatch):
        """Budgets where max_chars < len(ellipsis) clamp to cut=0 —
        the result is never a negative slice."""
        monkeypatch.setattr(token_budget, "_tiktoken_available", False)
        text = "y" * 200
        for budget in (1, 2, 3, 4, 5, 8):  # 8*4=32 < 34 = len(ellipsis)
            result = truncate_to_tokens(text, budget)
            assert result == ELLIPSIS, f"budget={budget}"

    def test_larger_budget_truncates_at_clamped_cut(self, monkeypatch):
        """With budget 20: max_chars=80, cut=80-34=46, total=80 chars."""
        monkeypatch.setattr(token_budget, "_tiktoken_available", False)
        text = "z" * 500
        result = truncate_to_tokens(text, 20)
        assert result == "z" * 46 + ELLIPSIS
        assert len(result) == 20 * 4  # content + ellipsis fits budget

    def test_fitting_text_unchanged(self, monkeypatch):
        monkeypatch.setattr(token_budget, "_tiktoken_available", False)
        text = "a" * 40  # <= 20*4
        assert truncate_to_tokens(text, 20) == text

    def test_empty_text(self, monkeypatch):
        monkeypatch.setattr(token_budget, "_tiktoken_available", False)
        assert truncate_to_tokens("", 10) == ""


class TestTiktokenPath:
    """The primary (tiktoken) path is untouched."""

    def test_long_text_truncated_with_ellipsis(self):
        text = "word " * 1000  # ~1000+ tokens
        result = truncate_to_tokens(text, 50)
        assert result.endswith(ELLIPSIS)
        assert len(result) < len(text)
        from gossamer.token_budget import count_tokens

        # content (<= 50 - ellipsis_tokens) + ellipsis, with a small
        # margin for BPE re-tokenization at the cut boundary
        assert count_tokens(result) <= 55

    def test_short_text_unchanged(self):
        assert truncate_to_tokens("hello world", 100) == "hello world"
