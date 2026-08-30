"""Focused semantic crawl orchestration for the toolbox.

Extracted from ``agent_tools.py`` as part of the composition split
(phase 5). Holds the live-vocabulary ``_CrawlCorpus`` (BM25-style idf),
the offline thesaurus loader, and the ``Crawler`` collaborator that
drives a best-first crawl over a site's link graph. ``Crawler`` reads
all shared toolbox state through ``self._tb`` (the toolbox), mirroring
``SearchService`` / ``FetchService`` / ``DocumentExtractor``.
"""


from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from stitch_web_researcher.config import normalize_url
from stitch_web_researcher.structured_parser import DOCUMENT_EXTENSIONS
from stitch_web_researcher.ssrf import SsrfBlockedError

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


"""Focused semantic crawl orchestration for the toolbox.

Phase 5 of the agent_tools.py composition split (god-class reduction).
The focused-crawl orchestration (best-first frontier scoring, BM25/IDF
term weights, offline thesaurus expansion, anchor context, URL-path
priors, ranked pages + excerpts) was moved out of the
``WebResearcherToolbox`` facade into this collaborator. The facade keeps
a thin ``crawl`` delegation so the public import surface and tool
dispatch are unchanged.

``Crawler`` reads all shared toolbox state through ``self._tb`` (the
toolbox), mirroring ``SearchService`` / ``FetchService`` /
``DocumentExtractor``: a caller that reassigns toolbox attributes is
seen live instead of via a stale captured copy.
"""

import copy
from typing import Optional
from urllib.parse import urlparse

class Crawler:
    """Focused best-first crawl over a site link graph.

    Thin orchestrator moved out of the WebResearcherToolbox facade
    (composition phase 5). Shared crawling helpers (_CrawlCorpus,
    _load_thesaurus) already live in this module; this class wires
    them into the toolbox fetch/cache/rate-limit pipeline.
"""

    def __init__(self, tb):
        self._tb = tb

    # ── Focused crawl (deep-research support) ─────────────────────
    # A bounded best-first crawl over the link graph: the frontier is
    # ranked by relevance (score * decay^depth) instead of blind BFS
    # order, so the page budget is spent on what looks like the
    # answer. With flat scores the order degrades to plain BFS (ties
    # break by discovery order), so hop 1 can never outrank depth 2+
    # unless the links there are actually more relevant.
    _CRAWL_MAX_DEPTH = 5        # hard cap for the max_depth parameter
    _CRAWL_MAX_PAGES = 50       # hard cap for the max_pages parameter
    _CRAWL_PAGE_CHARS = 300     # per-page skim kept in the crawl payload
    _CRAWL_QUEUE_CAP = 200      # bounded frontier; lowest scores dropped
    _CRAWL_DEPTH_DECAY = 0.7    # a depth-d link must outscore shallow ones ~1/0.7^d
    _CRAWL_QUERY_WEIGHT = 0.7   # weight of query coverage in the score
    _CRAWL_CONTEXT_WEIGHT = 0.3  # weight of containing-page topic coverage
    _CRAWL_RANK_BONUS = 0.1     # E1: search-prior rank i gets +0.1/(i+1)
    _CRAWL_TOPIC_WORDS = 40     # size of the per-page topic vocabulary
    _CRAWL_MIN_SCORE = 0.05     # default relevance floor (parameter)
    _CRAWL_LIST_CAP = 30        # cap for auxiliary lists in the payload
    _CRAWL_SKIP_EXTENSIONS = frozenset({
        ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif", ".svg",
        ".webp", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".otf",
        ".mp3", ".mp4", ".webm", ".avi", ".mov", ".zip", ".gz", ".tar",
        ".rar", ".exe", ".dmg",
    })
    _CRAWL_SKIP_PATH_PREFIXES = (
        "/login", "/signin", "/sign-in", "/signout", "/logout",
        "/signup", "/register", "/account", "/profile",
        "/cart", "/checkout", "/search", "/tag/", "/tags/",
        "/author/", "/feed", "/track",
    )
    _CRAWL_STOPWORDS = frozenset(
        "a about above after again against all am an and any are as at be "
        "because been before below being between both but by can did do does "
        "doing down during each few for from further had has have having he "
        "her here hers him his how i if in into is it its just me more most "
        "my no nor not of off on once only or other our out over own same "
        "she should so some such than that the their them then there these "
        "they this those through to too under until up very was we were what "
        "when where which while who why will with you your yours".split()
    )
    # Semantic crawl (A): BM25/IDF regime + anchor context + path priors.
    _CRAWL_IDF_MIN_CORPUS = 3   # flat v0.4.6-style weights until this many pages
    _CRAWL_CONTEXT_CHARS = 50   # anchor context window, each side of the anchor
    _CRAWL_CONTEXT_TOKEN_CAP = 8  # max tokens contributed by anchor context
    _CRAWL_EXPANSION_WEIGHT = 0.5  # thesaurus-expanded query terms weigh half
    _CRAWL_PATH_PRIOR_GROUPS = (
        (("/docs/", "/guide/", "/guides/", "/blog/", "/api/",
          "/changelog/", "/reference/"), 1.15),
        (("/pricing", "/careers", "/contact", "/about"), 0.85),
    )
    _CRAWL_EXCERPT_WINDOW = 300  # keyword-densest excerpt window (chars)
    _CRAWL_EXCERPT_STEP = 100    # excerpt window slide (chars)

    # Focused crawl (deep-research support)
    # ───────────────────────────────

    @classmethod
    def _crawl_tokens(cls, text: str) -> set:
        """Content words of *text* (lowercase alnum, stopwords removed)."""
        return {
            t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if t not in cls._CRAWL_STOPWORDS
        }

    @classmethod
    def _crawl_topic_words(cls, text: str) -> set:
        """Top content words of a page (TF-ranked, capped, deterministic).

        Runs over the page's full delivered text (not just the title or
        first lines) — that is the neighbourhood signal its outgoing
        links are scored against.
        """
        counts: dict = {}
        for t in re.findall(r"[a-z0-9]+", (text or "").lower()):
            if t in cls._CRAWL_STOPWORDS:
                continue
            counts[t] = counts.get(t, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return {t for t, _ in ranked[: cls._CRAWL_TOPIC_WORDS]}

    @classmethod
    def _crawl_is_document(cls, url: str) -> bool:
        """True when the URL path carries a document extension (D routing)."""
        path = (urlparse(url).path or "").lower()
        return any(path.endswith(ext) for ext in DOCUMENT_EXTENSIONS)

    @classmethod
    def _crawl_anchor_context(cls, md: str, anchor: str) -> frozenset:
        """Topic words near an anchor in the page body (semantic A).

        Finds the anchor text (case-insensitive) in the page's full
        delivered markdown and harvests content words from a
        ±_CRAWL_CONTEXT_CHARS window around it.  When the window holds
        more than _CRAWL_CONTEXT_TOKEN_CAP distinct words, only the
        highest-frequency survive (ties alphabetical) -- deterministic.
        Empty when the anchor does not appear verbatim in the rendered
        markdown (link labels often do not; fail-open).
        """
        text = (md or "").lower()
        needle = (anchor or "").lower().strip()
        if not needle:
            return frozenset()
        pos = text.find(needle)
        if pos < 0:
            return frozenset()
        window = text[
            max(0, pos - cls._CRAWL_CONTEXT_CHARS):
            pos + len(needle) + cls._CRAWL_CONTEXT_CHARS
        ]
        counts: dict = {}
        for t in re.findall(r"[a-z0-9]+", window):
            if t not in cls._CRAWL_STOPWORDS:
                counts[t] = counts.get(t, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return frozenset(t for t, _ in ranked[: cls._CRAWL_CONTEXT_TOKEN_CAP])

    @classmethod
    def _crawl_path_prior(cls, url: str) -> float:
        """Mild topic prior from the URL path (semantic A).

        Documentation-ish paths are weighted up, transactional ones down;
        the first group with any matching prefix wins.  Table-driven so
        the mapping is unit-testable.
        """
        path = (urlparse(url).path or "").lower()
        for prefixes, weight in cls._CRAWL_PATH_PRIOR_GROUPS:
            if any(path.startswith(p) for p in prefixes):
                return weight
        return 1.0

    @classmethod
    def _crawl_term_hits(cls, text: str, terms: set) -> int:
        """Query-term occurrences in *text*'s token stream (semantic C).

        Counts occurrences, not unique terms: a page that repeats the
        topic 40 times signals substance.
        """
        if not terms or not text:
            return 0
        return sum(
            1 for t in re.findall(r"[a-z0-9]+", text.lower()) if t in terms
        )

    @classmethod
    def _crawl_excerpt(
        cls,
        text: str,
        terms: set,
        window: Optional[int] = None,
        step: Optional[int] = None,
    ) -> Optional[str]:
        """Keyword-densest window of *text* (semantic C, opt-in).

        Slides a *window*-char window over the full markdown in
        *step*-char strides and counts query-term occurrences per
        window; the densest window wins, ties go to the earliest, and
        zero density yields None (an empty excerpt is noise). Ellipses
        mark a window that does not touch the head or the tail.
        """
        text = text or ""
        if not terms or not text:
            return None
        if window is None:
            window = cls._CRAWL_EXCERPT_WINDOW
        if step is None:
            step = cls._CRAWL_EXCERPT_STEP
        best = None  # (density, start)
        for start in range(0, len(text), step):
            density = cls._crawl_term_hits(text[start:start + window], terms)
            if best is None or density > best[0]:
                best = (density, start)
        density, start = best
        if density == 0:
            return None
        excerpt = text[start:start + window]
        prefix = "\u2026" if start > 0 else ""
        suffix = "\u2026" if start + len(excerpt) < len(text) else ""
        return prefix + excerpt + suffix

    @classmethod
    def _crawl_expand_query(
        cls, base_terms: set, clusters: tuple = None
    ) -> tuple:
        """Expand *base_terms* with thesaurus synonyms (semantic B).

        Deterministic iteration: base terms sorted, clusters in file
        order, members in cluster order, each term at most once.
        Expansion is capped at ``len(base_terms)`` additions so the query
        never grows past twice its size.  Returns
        ``(expanded_set, added_count)``.
        """
        base = set(base_terms)
        if not base:
            return base, 0
        if clusters is None:
            _version, clusters = _load_thesaurus()
        seen = set(base)
        added = 0
        cap = len(base)
        for term in sorted(base):
            for cluster in clusters:
                if term not in cluster:
                    continue
                for member in cluster:
                    if member in seen:
                        continue
                    seen.add(member)
                    added += 1
                    if added >= cap:
                        return seen, added
        return seen, added

    @classmethod
    def _crawl_score(
        cls,
        url: str,
        anchor: str,
        depth: int,
        query_terms: set,
        page_terms: set,
        corpus: _CrawlCorpus = None,
        label_extra: frozenset = frozenset(),
        base_terms: set = None,
    ) -> float:
        """Relevance score of a frontier candidate (see ``crawl``).

        Legacy form (``corpus is None``) is exactly the v0.4.6 formula:
        ``score = QUERY_WEIGHT * cover(label, query)
                + CONTEXT_WEIGHT * cover(label, page_topic)``
        with the label = anchor text + URL path tokens and uniform
        weights.

        Semantic crawl (A/B): with a live *corpus*, term weights become
        BM25-style idfs (flat 1.0 until the corpus has read
        ``_CRAWL_IDF_MIN_CORPUS`` pages, i.e. flat weights early on),
        *label_extra* contributes anchor-context words, *base_terms*
        marks the caller's original query terms so thesaurus expansions
        weigh half, and URL path priors apply from the non-degenerate
        regime on.  The depth decay is applied by the caller so the
        reported per-page score is depth-independent and comparable.
        """
        label = cls._crawl_tokens(anchor)
        label |= cls._crawl_tokens(urlparse(url).path)
        label |= set(label_extra)
        if corpus is None:
            score = 0.0
            if query_terms:
                score += cls._CRAWL_QUERY_WEIGHT * len(label & query_terms) / len(query_terms)
            if label and page_terms:
                score += cls._CRAWL_CONTEXT_WEIGHT * len(label & page_terms) / len(label)
            return score
        base = base_terms if base_terms is not None else query_terms

        def w_q(t: str) -> float:
            idf = corpus.idf(t)
            return idf if t in base else cls._CRAWL_EXPANSION_WEIGHT * idf

        q_weight = sum(w_q(t) for t in query_terms)
        score = 0.0
        if query_terms and q_weight > 0:
            score += (
                cls._CRAWL_QUERY_WEIGHT
                * sum(w_q(t) for t in label & query_terms)
                / q_weight
            )
        l_weight = sum(corpus.idf(t) for t in label)
        if label and page_terms and l_weight > 0:
            score += (
                cls._CRAWL_CONTEXT_WEIGHT
                * sum(corpus.idf(t) for t in label & page_terms)
                / l_weight
            )
        if corpus.n >= cls._CRAWL_IDF_MIN_CORPUS:
            score *= cls._crawl_path_prior(url)
        return score

    @staticmethod
    def _shrink_crawl(result: dict, budget: Optional[int]) -> str:
        """Serialize a crawl result with the page list capped to fit.

        Per-page content is already skims (``_CRAWL_PAGE_CHARS``); the
        only remaining lever under a tight budget is dropping pages from
        the tail — which matches their (descending) priority anyway.
        """
        out = copy.deepcopy(result)
        if budget is None:
            return json.dumps(out, indent=2)
        # A page record is at most _CRAWL_PAGE_CHARS plus ~200 chars of
        # envelope, so 500 chars per kept page is a safe unit.
        keep = max(1, budget // 500)
        pages = out.get("pages") or []
        if len(pages) > keep:
            out["pages_omitted"] = len(pages) - keep
            out["pages"] = pages[:keep]
        return json.dumps(out, indent=2)

    def crawl(
        self,
        root_url: str,
        query: Optional[str] = None,
        max_depth: int = 3,
        max_pages: int = 15,
        same_host: bool = False,
        min_score: float = 0.05,
        excerpts: bool = False,
        search_prior: bool = False,
        seed_urls: Optional[list] = None,
    ) -> str:
        """Bounded focused crawl over a site's link graph.

        BFS from *root_url*, but the frontier is a priority queue ranked
        by relevance, so the page budget goes to the most relevant links
        instead of the first ones in the HTML:

        1. Fetch the root through the normal page pipeline (cache,
           robots, SSRF, rate limits, provenance) — depth 0.
        2. Score each outgoing link: query coverage (0.7) plus
           containing-page topic coverage (0.3), both BM25-style: term
           weights are idfs over the pages fetched so far (flat until
           the crawl has read a few pages), the query is expanded with
           the offline thesaurus (expansions weigh half), the link's
           surrounding page text joins its label, and documentation-ish
           URL paths get a mild prior.
        3. Pop the highest ``score * 0.7**depth`` (ties: discovery
           order — flat scores therefore degrade to plain BFS) and
           fetch it, until *max_pages* pages are fetched, the frontier
           is exhausted, or *max_depth* is reached.

        *query* focuses the ranking; when omitted the root page's own
        title and content words stand in for it. Links to documents
        (PDF/DOCX/...) are never fetched here — they are collected, scored
        at first sighting, and returned as a rank-ordered ``documents``
        list (entries below *min_score* are counted and reported in
        ``skipped``) so the agent can read them via extract_document
        (which surfaces the URLs written inside them). Failed fetches
        do not count against *max_pages*. Every fetched page stays in
        the page cache in full, so a later ``inspect_html_page`` of the
        same URL is a cache hit delivering the complete content.

        *search_prior* (E1, opt-in) runs one site-scoped web search
        before the crawl and feeds its top-5 results into the frontier
        at depth 1 with a small rank bonus; they are exempt from
        *min_score* (the engine already ranked them), and any search
        failure is non-fatal (the crawl degrades to link-graph only).
        *seed_urls* (E2) are caller/agent-supplied starting URLs,
        normalised against the root and SSRF-checked in full; they are
        pushed at depth 0 (their children are depth 1) and respect
        *min_score* — a below-floor seed is skipped with a reason,
        never silent.

        Each page record also carries richness stats: ``content_chars``
        (full delivered size, pre-skim) and ``term_hits`` (query-term
        occurrences in the full body). With ``excerpts=True`` each page
        additionally gets an ``excerpt`` — the keyword-densest 300-char
        window of its full body (raises the payload; pair with a lower
        *max_pages*).

        Returns
        -------
        str
            JSON: root, query echo, parameters (echoing search_prior and,
            when it is on, how many search results were eligible),
            per-page records (url, depth, title, score, markdown skim,
            links_total, content_chars, term_hits, optional excerpt),
            errors, ranked documents, skipped (with reasons), counters,
            and the stop reason.
        """
        try:
            root = normalize_url(root_url)
        except ValueError as e:
            return json.dumps({"error": f"crawl: {e}"}, indent=2)
        try:
            self._tb._validate_url(root)
        except Exception as e:
            return json.dumps({"error": f"crawl: {e}"}, indent=2)

        try:
            max_depth = int(max_depth)
        except (TypeError, ValueError):
            max_depth = 3
        max_depth = max(0, min(max_depth, self._CRAWL_MAX_DEPTH))
        try:
            max_pages = int(max_pages)
        except (TypeError, ValueError):
            max_pages = 15
        max_pages = max(1, min(max_pages, self._CRAWL_MAX_PAGES))
        try:
            min_score = float(min_score)
        except (TypeError, ValueError):
            min_score = self._CRAWL_MIN_SCORE
        min_score = max(0.0, min_score)
        excerpts = bool(excerpts)
        search_prior = bool(search_prior)
        if isinstance(seed_urls, str):
            seed_urls = [seed_urls]
        seeds = [str(s) for s in (seed_urls or [])]

        root_key = self._tb._crawl_host_key(root)
        # Politeness is scoped to the crawl's own host: same-domain pages
        # are spaced out with delay+jitter, external hosts are fetched
        # without throttling (each is visited at most once).
        politeness_root = root_key
        queue: list = []  # (effective score, seq, url, depth, anchor)
        seq = 0
        queue_dropped = 0
        documents_total = 0
        documents_below_score = 0
        skipped_total = 0
        visited: set = set()
        doc_seen: set = set()
        skip_seen: set = set()
        pages: list = []
        errors: list = []
        skipped: list = []
        documents: list = []
        # Semantic A: live site vocabulary.  Fed after every successful
        # fetch and *before* that page's links are scored, so each page's
        # candidates are ranked against everything read up to that page.
        corpus = _CrawlCorpus(min_corpus=self._CRAWL_IDF_MIN_CORPUS)

        def note_skipped(url: str, reason: str) -> None:
            nonlocal skipped_total
            mark = (url, reason)
            if mark in skip_seen:
                return
            skip_seen.add(mark)
            skipped_total += 1
            if len(skipped) < self._CRAWL_LIST_CAP:
                skipped.append({"url": url, "reason": reason})

        def expand(page: dict, page_url: str, depth: int) -> None:
            """Score a fetched page's links and push the survivors on."""
            nonlocal seq, queue, queue_dropped, documents_total
            nonlocal documents_below_score

            if depth >= max_depth:
                return
            title = str((page.get("metadata") or {}).get("title") or "")
            page_md = page.get("markdown") or ""
            page_terms = self._crawl_topic_words(page_md + " " + title)
            context_cache: dict = {}
            for cand in page.get("follow_up_links") or []:
                if not isinstance(cand, dict):
                    continue
                raw_url = str(cand.get("url") or "")
                if not raw_url:
                    continue
                try:
                    url = normalize_url(raw_url, base=page_url)
                except ValueError:
                    continue  # non-http or malformed: never fatal
                key = url.split("#", 1)[0]
                if key in visited:
                    continue
                if cand.get("type") == "document":
                    if key not in doc_seen:
                        doc_seen.add(key)
                        documents_total += 1
                        # Semantic D: documents are reference material,
                        # not crawl targets. Scored at first sighting
                        # (depth 0, no decay) with the corpus as it is
                        # now, ranked in the payload, floored like pages.
                        doc_score = self._crawl_score(
                            url, str(cand.get("title") or ""), 0,
                            query_terms, page_terms,
                            corpus=corpus,
                            base_terms=base_terms,
                        )
                        if doc_score < min_score:
                            documents_below_score += 1
                            note_skipped(url, "below min score")
                        elif len(documents) < self._CRAWL_LIST_CAP:
                            documents.append({
                                "url": url,
                                "anchor": str(cand.get("title") or ""),
                                "score": round(doc_score, 3),
                            })
                    continue
                if same_host and self._tb._crawl_host_key(url) != root_key:
                    note_skipped(url, "external host")
                    continue
                path = (urlparse(url).path or "").lower()
                if any(path.startswith(p) for p in self._CRAWL_SKIP_PATH_PREFIXES):
                    note_skipped(url, "boilerplate path")
                    continue
                if os.path.splitext(path)[1] in self._CRAWL_SKIP_EXTENSIONS:
                    note_skipped(url, "asset")
                    continue
                anchor = str(cand.get("title") or "")
                # Semantic A: words around the anchor in the page body
                # join the label (cached per page per anchor; repeated
                # labels are common in nav footers).  A label that does
                # not appear in the rendered markdown contributes none.
                if anchor and anchor != "(untitled)":
                    context = context_cache.get(anchor)
                    if context is None:
                        context = self._crawl_anchor_context(page_md, anchor)
                        context_cache[anchor] = context
                else:
                    context = frozenset()
                score = self._crawl_score(
                    url, anchor, depth + 1,
                    query_terms, page_terms,
                    corpus=corpus,
                    label_extra=context,
                    base_terms=base_terms,
                )
                if score < min_score:
                    note_skipped(url, "below min score")
                    continue
                visited.add(key)
                seq += 1
                queue.append((
                    score * (self._CRAWL_DEPTH_DECAY ** (depth + 1)),
                    seq,
                    key,
                    depth + 1,
                    str(cand.get("title") or ""),
                ))
            trim_queue()

        def add_external(
            url: str,
            anchor: str,
            push_depth: int,
            rank_bonus: float = 0.0,
            exempt_floor: bool = False,
            below_reason: str = "below min score",
        ) -> bool:
            """Filter, score, and enqueue one external candidate (E1/E2).

            Documents are routed to the ranked list exactly like page
            links (D). Page candidates are scored with the current corpus
            (the root page's topic words as containing context) and pushed
            at *push_depth* (seeds 0, search results 1). Returns True when
            the candidate entered the page frontier.
            """
            nonlocal seq, documents_total, documents_below_score
            key = url.split("#", 1)[0]
            if key in visited:
                return False
            if self._crawl_is_document(url):
                if key in doc_seen:
                    return False
                doc_seen.add(key)
                documents_total += 1
                doc_score = self._crawl_score(
                    url, anchor, 0,
                    query_terms, root_terms,
                    corpus=corpus,
                    base_terms=base_terms,
                )
                if doc_score < min_score:
                    documents_below_score += 1
                    note_skipped(url, "below min score")
                elif len(documents) < self._CRAWL_LIST_CAP:
                    documents.append({
                        "url": url,
                        "anchor": anchor,
                        "score": round(doc_score, 3),
                    })
                return False
            if same_host and self._tb._crawl_host_key(url) != root_key:
                note_skipped(url, "external host")
                return False
            path = (urlparse(url).path or "").lower()
            if any(path.startswith(p) for p in self._CRAWL_SKIP_PATH_PREFIXES):
                note_skipped(url, "boilerplate path")
                return False
            if os.path.splitext(path)[1] in self._CRAWL_SKIP_EXTENSIONS:
                note_skipped(url, "asset")
                return False
            score = self._crawl_score(
                url, anchor, push_depth,
                query_terms, root_terms,
                corpus=corpus,
                base_terms=base_terms,
            )
            if score < min_score and not exempt_floor:
                note_skipped(url, below_reason)
                return False
            visited.add(key)
            seq += 1
            queue.append((
                (score + rank_bonus) * (self._CRAWL_DEPTH_DECAY ** push_depth),
                seq,
                key,
                push_depth,
                anchor,
            ))
            return True

        def fetch_record(url: str, depth: int, score_eff: float) -> None:
            """Fetch one candidate through the normal page pipeline."""
            record = {
                "url": url,
                "depth": depth,
                "score": round(score_eff, 3),
            }
            try:
                raw_page = self._tb._fetch._inspect_html_page_impl(
                    url, None, "", 0, 1, politeness_root
                )
                try:
                    page = json.loads(raw_page)
                except json.JSONDecodeError:
                    page = raw_page
            except Exception as e:
                logger.warning("crawl fetch failed for %s: %s", url, e)
                errors.append({"url": url, "depth": depth, "error": str(e)})
                return
            # The impl reports failures (fetch errors, robots disallow,
            # already visited) as {"error"|"warning": ...} dicts.
            if isinstance(page, dict) and ("error" in page or "warning" in page):
                errors.append({
                    "url": url,
                    "depth": depth,
                    "error": str(page.get("error") or page.get("warning")),
                })
                return
            md = page.get("markdown") or ""
            record["status"] = "ok"
            # Expose which method served this page (auto -> static, falling
            # back to the stealth browser on non-text/JS pages). crawl runs
            # through _inspect_html_page_impl, so the page payload already
            # carries it; single-page inspect reports the same field.
            record["fetch_method"] = page.get("fetch_method")
            record["title"] = str(
                (page.get("metadata") or {}).get("title") or ""
            )
            record["markdown"] = md[: self._CRAWL_PAGE_CHARS]
            record["links_total"] = int(page.get("total_links") or 0)
            # Semantic C: richness stats on the full delivered body.
            record["content_chars"] = len(md)
            record["term_hits"] = self._crawl_term_hits(md, query_terms)
            if excerpts:
                excerpt = self._crawl_excerpt(md, query_terms)
                if excerpt is not None:
                    record["excerpt"] = excerpt
            pages.append(record)
            corpus.add_page(self._crawl_tokens(md))
            expand(page, url, depth)

        def trim_queue() -> None:
            """Evict the weakest candidates, keeping the frontier bounded."""
            nonlocal queue, queue_dropped
            if len(queue) > self._CRAWL_QUEUE_CAP:
                queue.sort(key=lambda e: (e[0], e[1]))
                queue_dropped += len(queue) - self._CRAWL_QUEUE_CAP
                queue = queue[-self._CRAWL_QUEUE_CAP:]

        # Root: always fetched (depth 0); a root failure kills the crawl.
        try:
            raw_root = self._tb._fetch._inspect_html_page_impl(
                root, None, "", 0, 1, politeness_root
            )
            try:
                root_page = json.loads(raw_root)
            except json.JSONDecodeError:
                root_page = raw_root
        except Exception as e:
            return json.dumps(
                {"error": f"crawl: root fetch failed: {e}", "root": root},
                indent=2,
            )
        if isinstance(root_page, dict) and (
            "error" in root_page or "warning" in root_page
        ):
            return json.dumps(
                {
                    "error": "crawl: root fetch failed: "
                    + str(root_page.get("error") or root_page.get("warning")),
                    "root": root,
                },
                indent=2,
            )
        root_md = root_page.get("markdown") or ""
        root_title = str(
            (root_page.get("metadata") or {}).get("title") or ""
        )
        pages.append({
            "url": root,
            "depth": 0,
            "status": "ok",
            "title": root_title,
            "score": 1.0,
            "markdown": root_md[: self._CRAWL_PAGE_CHARS],
            "links_total": int(root_page.get("total_links") or 0),
            # Mirror the per-page record: report which method served the
            # root (auto -> static, falling back to the stealth browser).
            "fetch_method": root_page.get("fetch_method"),
        })
        visited.add(root.split("#", 1)[0])
        corpus.add_page(self._crawl_tokens(root_md))
        # E1/E2 scoring context: the root page's own topic words stand in
        # for a containing page when the candidate comes from outside the
        # link graph (seeds, search results).
        root_terms = self._crawl_topic_words(root_md + " " + root_title)

        # Effective query: the caller's focus, or the root page itself,
        # then expanded with the offline thesaurus (semantic B).  The
        # base terms keep full weight; expansions weigh half.
        query = (query or "").strip()
        if query:
            base_terms = self._crawl_tokens(query)
            query_echo = query
        else:
            base_terms = self._crawl_topic_words(root_md + " " + root_title)
            query_echo = "derived from root page"
        query_terms, expanded = self._crawl_expand_query(base_terms)
        if expanded:
            query_echo += f" +{expanded}"
        # Semantic C: the root record gets the same richness fields
        # (its score stays 1.0 by construction).
        root_rec = pages[0]
        root_rec["content_chars"] = len(root_md)
        root_rec["term_hits"] = self._crawl_term_hits(root_md, query_terms)
        if excerpts:
            excerpt = self._crawl_excerpt(root_md, query_terms)
            if excerpt is not None:
                root_rec["excerpt"] = excerpt
        expand(root_page, root, 0)

        # E2: caller/agent-supplied seed URLs. Seeds are LLM-supplied, so
        # the SSRF policy applies in full (S1). Each is pushed at depth 0
        # (its children land at depth 1 within max_depth) and respects the
        # min_score floor — a below-floor seed is skipped, never silent.
        for seed in seeds:
            try:
                seed_url = normalize_url(seed, base=root)
            except ValueError:
                note_skipped(seed, "invalid url")
                continue
            try:
                self._tb._validate_url(seed_url)
            except SsrfBlockedError:
                note_skipped(seed, "ssrf blocked")
                continue
            except ValueError:
                note_skipped(seed, "invalid url")
                continue
            add_external(seed_url, "", 0, below_reason="seed below min score")

        # E1: search prior. One site-scoped search seeds the frontier with
        # the engine's own top results (rank bonus 0.1/(i+1), depth 1,
        # exempt from the floor — the engine already ranked them). Any
        # failure is non-fatal: the crawl degrades to link-graph only.
        search_results_count = 0
        if search_prior and max_depth >= 1:
            if query:
                focus = str(query)
            elif root_title:
                focus = root_title
            else:
                focus = " ".join(sorted(base_terms)[:6]) or "site content"
            site_query = f"site:{self._tb._crawl_host_key(root)} {focus}"
            try:
                raw = self._tb.search_web(site_query, max_results=5)
            except Exception:
                raw = json.dumps({"error": "search prior failed"})
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                payload = None
            results = None
            if isinstance(payload, list):
                results = payload
            elif isinstance(payload, dict):
                if "error" in payload:
                    logger.warning("crawl search prior: %s", payload.get("error"))
                else:
                    results = payload.get("results")
            if not isinstance(results, list):
                logger.warning(
                    "crawl search prior failed for %r; continuing link-graph only",
                    site_query,
                )
                results = []
            for i, res in enumerate(results[:5]):
                if not isinstance(res, dict):
                    continue
                cand_url = str(res.get("url") or "")
                try:
                    cand_url = normalize_url(cand_url, base=root)
                except ValueError:
                    continue
                if add_external(
                    cand_url,
                    str(res.get("title") or ""),
                    1,
                    rank_bonus=self._CRAWL_RANK_BONUS / (i + 1),
                    exempt_floor=True,
                ):
                    search_results_count += 1

        trim_queue()

        stop = "frontier exhausted"
        while queue:
            if len(pages) >= max_pages:
                stop = "max_pages reached"
                break
            # Best-first: highest effective score, ties by discovery
            # order (so flat scores degrade to plain BFS).
            queue.sort(key=lambda e: (-e[0], e[1]))
            score_eff, _s, url, depth, _anchor = queue.pop(0)
            fetch_record(url, depth, score_eff)

        # Semantic D: documents ranked by score; the stable sort keeps
        # first-sighting order for ties.
        documents.sort(key=lambda d: -d["score"])

        result = {
            "root": root,
            "query": query_echo,
            "max_depth": max_depth,
            "max_pages": max_pages,
            "same_host": same_host,
            "min_score": min_score,
            "excerpts": excerpts,
            "search_prior": search_prior,
            "pages": pages,
            "errors": errors[: self._CRAWL_LIST_CAP],
            "errors_total": len(errors),
            "documents": documents,
            "documents_total": documents_total,
            "documents_below_score": documents_below_score,
            "skipped": skipped,
            "skipped_total": skipped_total,
            "queue_dropped": queue_dropped,
            "count": len(pages),
            "stop": stop,
        }
        if search_prior:
            result["search_results"] = search_results_count
        return self._tb._budget._fit_json(
            lambda b: self._shrink_crawl(result, b),
            self._tb.max_markdown_chars,
            self._tb.max_tokens,
            {
                "root": root,
                "error": "crawl result too large for the output budget",
                "hint": "lower max_pages or raise max_tokens",
            },
        )

