# stitch_web_researcher/text_links.py
"""Text-level link detection for document content.

The HTML pipeline gets its links from the extractor (``<a href>`` pairs
with anchor text). Documents (PDF, DOCX, ...) lose that structure: the
extractor hands back plain text / Markdown, and the URLs written into
the body ("See https://example.com/report", "www.example.com/docs")
would otherwise be invisible to the calling agent.

This module is the narrow end of that gap: a stdlib-only scanner that
finds absolute-ish URLs in arbitrary extracted text. It is deliberately
dumb and slow-safe — one compiled regex, one pass, bounded output — and
it never raises on odd input: a detector must degrade to "no links",
never break the extraction that found it.
"""
from __future__ import annotations

import re

__all__ = ["extract_links"]

# http(s) URLs and scheme-less www.host URLs. The stop class keeps a
# match from running over whitespace, quotes, angle brackets, closing
# brackets (markdown [text](url) targets end at the paren), and common
# sentence punctuation in Latin and CJK scripts.
_URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"'\)\]\}，。、；！？（）【】「」『』]+"
)

# Punctuation prose habitually leaves stuck to a URL's tail. (Closing
# brackets can never end a match — they are in the stop class.)
_TRAILING_PUNCT = ".,;:!?…\"'"


def extract_links(text: str, max_links: int = 50) -> list[str]:
    """Extract deduplicated URLs from extracted document text.

    Parameters
    ----------
    text :
        Extracted plain text or Markdown (any document content).
    max_links :
        Cap on the returned list (dedupe keeps first occurrence order).
        0 or negative returns an empty list.

    Returns
    -------
    list[str]
        Absolute URLs. A scheme-less ``www.`` match is promoted to
        ``http://`` so the result is directly fetchable. Bare domains
        without ``www.`` or a scheme are *not* matched — in body text
        that shape is usually prose, not a link, and the false-positive
        cost outweighs the recall gain.

    Never raises: non-string or empty input yields ``[]``.
    """
    if not isinstance(text, str) or not text:
        return []
    if max_links is None or max_links <= 0:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(_TRAILING_PUNCT)
        if url.startswith("www."):
            url = "http://" + url
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= max_links:
            break
    return out
