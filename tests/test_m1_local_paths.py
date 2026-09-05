"""M1: normalize_url must not turn local file paths into URLs.

``extract_document("report.pdf")`` used to call normalize_url, which
promoted the bare filename to ``https://report.pdf`` (the first path
segment contains a dot) and then fetched it over the network instead
of reading the file from disk.
"""

import json
from pathlib import Path

import pytest

from gossamer.agent_tools import WebResearcherToolbox, normalize_url


class TestNormalizeLocalPaths:
    def test_relative_dot_slash_rejected(self):
        with pytest.raises(ValueError, match="local file path"):
            normalize_url("./report.pdf")

    def test_relative_parent_rejected(self):
        with pytest.raises(ValueError, match="local file path"):
            normalize_url("../docs/guide.pdf")

    def test_existing_local_file_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        Path("report.pdf").write_bytes(b"%PDF-1.4 fake")
        with pytest.raises(ValueError, match="local file path"):
            normalize_url("report.pdf")

    def test_nonexistent_bare_word_still_rejected(self):
        with pytest.raises(ValueError):
            normalize_url("report-pdf")

    def test_bare_domain_promotion_unchanged(self):
        assert normalize_url("example.com/doc.pdf") == "https://example.com/doc.pdf"

    def test_www_domain_promotion_unchanged(self):
        assert normalize_url("www.example.com") == "https://www.example.com"

    def test_absolute_posix_path_rejected(self):
        with pytest.raises(ValueError):
            normalize_url("/home/user/report.pdf")

    def test_absolute_windows_path_rejected(self):
        with pytest.raises(ValueError):
            normalize_url("C:\\Users\\user\\report.pdf")


class TestExtractDocumentLocalRouting:
    def test_existing_local_file_goes_to_disk(self, tmp_path, monkeypatch):
        """An existing relative file must hit _extract_local, not the network."""
        monkeypatch.chdir(tmp_path)
        Path("report.pdf").write_bytes(b"%PDF-1.4 fake")

        tb = WebResearcherToolbox()
        called = {}

        def fake_extract_local(source):
            called["source"] = source
            return "LOCAL CONTENT"

        monkeypatch.setattr(tb._doc, "_extract_local", fake_extract_local)

        result = json.loads(tb.extract_document("report.pdf"))
        assert "error" not in result
        assert result["content"] == "LOCAL CONTENT"
        assert called["source"] == "report.pdf"
        assert "report.pdf" not in tb.visited_urls  # never treated as a URL

    def test_explicit_relative_missing_file_errors_gracefully(
        self, tmp_path, monkeypatch
    ):
        """'./missing.pdf' is an explicit relative path even when the file
        does not exist — a clean disk error, never a network fetch."""
        monkeypatch.chdir(tmp_path)
        tb = WebResearcherToolbox()
        result = json.loads(tb.extract_document("./missing.pdf"))
        assert "error" in result
        assert not any("missing.pdf" in u for u in tb.visited_urls)
