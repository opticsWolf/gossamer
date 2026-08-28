# stitch_web_researcher/sections.py
"""Query-relevant section selection (Tier 1.1, CODE_REVIEW_2026-08-27).

A page that does not fit the output budget used to be truncated
head-first, so relevant content past the cut was simply lost. This
module splits markdown into heading-anchored sections, scores them
against the research query with BM25 (no new dependencies), and lets
the caller keep only the sections that matter.

The selection is deliberately *lossy-transparent*: the outcome carries
``total_sections`` plus the selected anchors so the consuming model
knows how much of the page it is actually seeing.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

__all__ = [
    "Section",
    "SectionSelection",
    "split_sections",
    "tokenize_text",
    "bm25_scores",
    "select_relevant_sections",
]

# ATX *and* Setext headings. The comment this replaces claimed the Rust
# html2md converter emits ATX; it does not. It emits Setext for h1/h2 and
# closed ATX for h3+:
#
#     Alpha            <- h1
#     ==========
#     Bravo            <- h2
#     ----------
#     ### Charlie ###  <- h3
#
# Matching ATX alone made h1/h2 invisible, so a real page collapsed into a
# single "(intro)" section and BM25 had nothing to choose between -- query
# relevant selection silently did nothing on exactly the pages it exists
# for. The ATX branch also drops the optional closing hashes, which used
# to leak into the section title ("Charlie ###").
#
# The Setext branch is deliberately conservative: the underline must be two
# or more ``=``/``-`` characters, and the title line may not be blank, a
# rule of its own, or a list item -- otherwise thematic breaks (``---``),
# bullet lists and table separators would all read as headings.
_HEADING_RE = re.compile(
    r"^\#{1,6}[ \t]+(?P<atx>.+?)(?:[ \t]+\#+)?[ \t]*$"
    r"|"
    r"^(?P<setext>(?![ \t]*$)(?![ \t]*[-=]+[ \t]*$)(?![-*+][ \t])[^\n]+?)"
    r"[ \t]*\n[ \t]*(?:=|-){2,}[ \t]*$",
    re.MULTILINE,
)
_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Japanese kana, CJK ideographs, Hangul — bigram-tokenized below.
_CJK_RUN_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]+")

# A deliberately small English stopword list: BM25 over one document's
# sections only needs the obvious function words gone.
_STOPWORDS = frozenset(
    """
    a an and are as at be been but by can could did do does for from had
    has have he her his i if in into is it its just me my no not of on
    one only or our she so that the their them then these they this to
    too was we were what when which who will with you your
    """.split()
)


@dataclass(frozen=True)
class Section:
    """One heading-anchored slice of a markdown document.

    ``text`` starts at the heading line itself (the heading text is
    part of the section body), so selected sections stay readable on
    their own. ``anchor`` is the heading text, or ``"(intro)"`` for the
    preamble before the first heading.
    """

    anchor: str
    text: str
    offset: int


@dataclass(frozen=True)
class SectionSelection:
    """Outcome of relevance-based section selection.

    ``markdown`` holds the selected sections concatenated in *original
    document order* (not score order) so prose context is preserved.
    """

    markdown: str
    total_sections: int
    selected_count: int
    anchors: tuple[str, ...]


def split_sections(markdown: str) -> list[Section]:
    """Split markdown into heading-anchored sections.

    Text before the first heading becomes a single ``"(intro)"``
    section (if non-empty). A document without headings is one section.
    """
    if not markdown or not markdown.strip():
        return []
    matches = list(_HEADING_RE.finditer(markdown))
    if not matches:
        return [Section("(intro)", markdown, 0)]
    sections: list[Section] = []
    preamble_end = matches[0].start()
    if markdown[:preamble_end].strip():
        sections.append(Section("(intro)", markdown[:preamble_end], 0))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        title = m.group("atx") or m.group("setext") or ""
        sections.append(Section(title.strip(), markdown[m.start():end], m.start()))
    return sections


def tokenize_text(text: str) -> list[str]:
    """Lowercase BM25 tokens: ASCII words (stopwords and single
    characters dropped) plus CJK bigrams (singletons for 1-char runs).
    """
    text = text.lower()
    tokens = [
        t for t in _ASCII_TOKEN_RE.findall(text) if len(t) > 1 and t not in _STOPWORDS
    ]
    for run in _CJK_RUN_RE.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def bm25_scores(
    query_tokens: list[str], docs: list[str], k1: float = 1.5, b: float = 0.75
) -> list[float]:
    """BM25 score of each doc against the query.

    Zero (not negative) when a doc shares no query term — BM25's raw
    IDF can go slightly negative for near-universal terms, and a
    "score" of -0.01 should not read as "harmful content".
    """
    n = len(docs)
    if n == 0 or not query_tokens:
        return [0.0] * n
    doc_tokens = [tokenize_text(d) for d in docs]
    avgdl = sum(len(d) for d in doc_tokens) / n or 1.0
    df: dict[str, int] = {}
    for d in doc_tokens:
        for term in set(d):
            df[term] = df.get(term, 0) + 1
    # Dedupe query terms, keep order; drop terms absent from every doc.
    query_terms = [t for t in dict.fromkeys(query_tokens) if t in df]
    if not query_terms:
        return [0.0] * n
    scores: list[float] = []
    for d in doc_tokens:
        tf: dict[str, int] = {}
        for term in d:
            tf[term] = tf.get(term, 0) + 1
        score = 0.0
        for q in query_terms:
            f = tf.get(q, 0)
            if f == 0:
                continue
            n_q = df[q]
            idf = math.log(1.0 + (n - n_q + 0.5) / (n_q + 0.5))
            score += idf * (f * (k1 + 1)) / (f + k1 * (1.0 - b + b * len(d) / avgdl))
        scores.append(max(score, 0.0))
    return scores


def select_relevant_sections(
    markdown: str, query: str, max_chars: int
) -> SectionSelection | None:
    """Pick the query-relevant sections of *markdown* under *max_chars*.

    Returns ``None`` when selection is unnecessary or uninformative —
    the content already fits, the query carries no tokens, the document
    has a single section, or no section matches the query at all — and
    the caller should fall back to head-first truncation. Otherwise
    returns the highest-scoring sections (original document order) that
    fit the budget; the single most relevant section is taken head-first
    if even it alone is oversized.
    """
    if max_chars <= 0 or not markdown:
        return None
    if len(markdown) <= max_chars:
        return None
    query_tokens = tokenize_text(query)
    if not query_tokens:
        return None
    sections = split_sections(markdown)
    if len(sections) <= 1:
        return None
    scores = bm25_scores(query_tokens, [s.text for s in sections])
    if max(scores) <= 0.0:
        return None

    order = sorted(range(len(sections)), key=lambda i: (-scores[i], i))
    picked: list[tuple[int, str]] = []
    remaining = max_chars
    for i in order:
        if scores[i] <= 0.0:
            break
        text = sections[i].text
        if len(text) <= remaining:
            picked.append((i, text))
            remaining -= len(text)
        elif remaining > 0:
            # Oversized top section: take its head rather than nothing.
            picked.append((i, text[:remaining]))
            remaining = 0
            break
    if not picked:
        return None
    picked.sort(key=lambda t: t[0])
    selected = "\n\n".join(text.strip() for _, text in picked).strip()
    if not selected:
        return None
    return SectionSelection(
        markdown=selected,
        total_sections=len(sections),
        selected_count=len(picked),
        anchors=tuple(sections[i].anchor for i, _ in picked),
    )
