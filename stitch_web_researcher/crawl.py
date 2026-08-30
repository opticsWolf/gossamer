"""Semantic-crawl scoring helpers.

Extracted from ``agent_tools.py`` as part of the composition split.
Holds the live-vocabulary ``_CrawlCorpus`` (BM25-style idf) and the
offline thesaurus loader consumed by the crawl scorer.
"""


from __future__ import annotations

import json
import logging
import math
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)
# ── Live-site corpus + offline thesaurus (semantic crawl) ──────
class _CrawlCorpus:
    """Live site vocabulary for BM25-style crawl scoring (semantic A).

    Tracks how many fetched pages contain each term (document
    frequency).  While fewer than *min_corpus* pages have been read,
    every idf is 1.0, which keeps the scorer on flat v0.4.6-style
    weights; afterwards rare terms outweigh common ones and the frontier
    ranking sharpens as the crawl reads the site.
    """

    __slots__ = ("n", "df", "min_corpus")

    def __init__(self, min_corpus: int = 3) -> None:
        self.n = 0
        self.df: dict = {}
        self.min_corpus = min_corpus

    def add_page(self, tokens: set) -> None:
        """Register one fetched page (term presence, not occurrences)."""
        self.n += 1
        for t in tokens:
            self.df[t] = self.df.get(t, 0) + 1

    def idf(self, t: str) -> float:
        """BM25 inverse document frequency (flat 1.0 while degenerate)."""
        if self.n < self.min_corpus:
            return 1.0
        d = self.df.get(t, 0)
        return max(0.0, math.log(1.0 + (self.n - d + 0.5) / (d + 0.5)))


@lru_cache(maxsize=1)
def _load_thesaurus() -> tuple:
    """Load the offline thesaurus (semantic B); fail-open.

    Returns ``(version, clusters)`` with clusters as a tuple of tuples in
    file order (order matters: query expansion iterates deterministically
    over it).  Any problem -- missing file, bad JSON, wrong shape --
    degrades to ``(0, ())``: expansion is disabled, the crawl is
    otherwise unaffected.
    """
    try:
        path = Path(__file__).resolve().parent / "thesaurus.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        version = int(raw["version"])
        clusters = tuple(
            tuple(str(t).lower() for t in cluster) for cluster in raw["clusters"]
        )
        return version, clusters
    except Exception:
        logger.warning("crawl thesaurus unavailable; expansion disabled")
        return 0, ()
