# gossamer/sections.py
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

# Implemented in Rust (``src/sections.rs``) — this module re-exports the
# PyO3 classes and functions under their original names, so all existing
# imports keep working. Contract pinned by
# ``tests/test_rust_parity_sections.py`` (vendored original + fuzz).
from gossamer._core import (
    Section,
    SectionSelection,
    bm25_scores,
    select_relevant_sections,
    split_sections,
    tokenize_text,
)

__all__ = [
    "Section",
    "SectionSelection",
    "split_sections",
    "tokenize_text",
    "bm25_scores",
    "select_relevant_sections",
]
