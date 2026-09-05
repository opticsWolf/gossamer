# tests/test_fix_bare_filename.py
"""Bugfix 6 — a bare filename is not a bare domain.

M1 taught ``normalize_url`` to reject local paths, and ``./report.pdf``,
``../a/b.pdf`` and ``docs/x.md`` all raise correctly. The single-segment
form did not: ``report.pdf`` has no slash, so the bare-domain heuristic
promoted it to ``https://report.pdf`` — a DNS lookup for a nonexistent
host, reported to the model as a network failure rather than "that is a
file, not a URL". It is also the exact example the original report used.

The fix rejects a slashless token whose suffix is a known document or
media extension, reusing the table ``classify_link`` already owns. The
tests below pair every rejection with a bare domain that must still work,
because the failure mode of an over-eager fix is refusing real hosts.
"""

import pytest

from gossamer.agent_tools import normalize_url
from gossamer.structured_parser import DOCUMENT_EXTENSIONS


class TestBareFilenamesAreRejected:
    @pytest.mark.parametrize(
        "raw",
        [
            "report.pdf",
            "notes.md",
            "data.csv",
            "slides.pptx",
            "sheet.xlsx",
            "manual.docx",
            "REPORT.PDF",  # case must not matter
        ],
    )
    def test_slashless_filename_raises(self, raw):
        with pytest.raises(ValueError) as exc:
            normalize_url(raw)
        assert "local file path" in str(exc.value)

    def test_every_known_extension_is_covered(self):
        # Guards the table wiring: if DOCUMENT_EXTENSIONS grows, the
        # heuristic must grow with it rather than silently lagging.
        for ext in DOCUMENT_EXTENSIONS:
            with pytest.raises(ValueError):
                normalize_url(f"file{ext}")

    @pytest.mark.parametrize(
        "raw", ["./report.pdf", "../a/b.pdf", "docs/x.md"]
    )
    def test_pathlike_forms_still_raise(self, raw):
        # M1's behaviour must not regress while fixing the slashless case.
        with pytest.raises(ValueError):
            normalize_url(raw)


class TestRealHostsStillWork:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("example.com", "https://example.com"),
            ("sub.example.co.uk", "https://sub.example.co.uk"),
            ("example.com/report.pdf", "https://example.com/report.pdf"),
            ("https://example.com/a.md", "https://example.com/a.md"),
        ],
    )
    def test_bare_domain_promotion_survives(self, raw, expected):
        assert normalize_url(raw).rstrip("/") == expected.rstrip("/")

    def test_document_extension_in_a_path_is_fine(self):
        # The rule is about the *whole slashless token*, not about the
        # string containing ".pdf" anywhere.
        assert normalize_url("files.example.com/x.pdf").endswith("/x.pdf")
