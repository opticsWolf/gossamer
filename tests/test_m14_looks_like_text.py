# tests/test_m14_looks_like_text.py
"""M14 — _looks_like_text must sample head + middle + tail, not only the
first 2000 chars. A payload that starts clean and degenerates later (e.g.
a partially decoded gzip) used to pass the gate and poison LLM context.
"""

from stitch_web_researcher.agent_tools import WebResearcherToolbox
from stitch_web_researcher.fetch import FetchService

_looks = FetchService._looks_like_text

CLEAN = "The quick brown fox jumps over the lazy dog. "
GARBAGE = "\x00\x01\x02"  # Cc control chars


class TestLooksLikeText:
    def test_empty_rejected(self):
        assert _looks("") is False
        assert _looks("   \n  ") is False

    def test_clean_text_accepted(self):
        assert _looks(CLEAN * 500) is True

    def test_short_clean_accepted(self):
        assert _looks("hello world") is True

    def test_cjk_clean_accepted(self):
        assert _looks("中文内容测试" * 500) is True

    def test_garbage_at_start_rejected(self):
        md = GARBAGE * 1000 + CLEAN * 500
        assert _looks(md) is False

    def test_garbage_in_middle_rejected(self):
        # Clean head (beyond the old 2000-char sample), garbage middle.
        md = CLEAN * 1500 + GARBAGE * 3000 + CLEAN * 1500
        assert _looks(md) is False

    def test_garbage_at_tail_rejected(self):
        md = CLEAN * 2000 + GARBAGE * 1500
        assert _looks(md) is False

    def test_light_noise_still_accepted(self):
        # A few control chars in an otherwise clean page (e.g. stray
        # \x0c form feeds from PDF-like HTML) must not trip the gate.
        md = (CLEAN * 3 + "\x0c") * 50
        assert _looks(md) is True
