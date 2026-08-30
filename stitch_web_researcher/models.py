"""Result models, provenance helpers, and markdown utilities.

Extracted from ``agent_tools.py`` as part of the composition split.
Pydantic output models (``ExtractionResult`` / ``InspectionResult`` /
``BatchEntry``), the fetch-observability ``FetchStats``, Tier 1.3
provenance helpers, and the M12 markdown-link absolutizer.
"""


from __future__ import annotations

import hashlib
import math
import re
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlparse, urljoin

from pydantic import BaseModel, Field

from stitch_web_researcher.structured_parser import FollowUpCandidate
# ── Fetch observability (Tier 2.6) ─────────────────────────────
class FetchStats:
    """Lightweight, thread-safe fetch observability (Tier 2.6).

    Tracks per-fetch latency (bounded sliding window), total bytes
    downloaded, per-domain request counts, and error counts by exception
    class. Exposed via ``get_stats()["fetches"]``.
    """

    def __init__(self, latency_window: int = 1024) -> None:
        self._lock = threading.Lock()
        self._latencies: deque = deque(maxlen=max(1, int(latency_window)))
        self._bytes = 0
        self._fetches = 0
        self._errors = 0
        self._by_domain: dict = {}
        self._by_error: dict = {}

    def record_success(self, domain: str, latency_s: float, nbytes: int) -> None:
        with self._lock:
            self._latencies.append(latency_s)
            self._bytes += max(0, int(nbytes))
            self._fetches += 1
            self._by_domain[domain] = self._by_domain.get(domain, 0) + 1

    def record_error(self, domain: str, latency_s: float, exc: BaseException) -> None:
        with self._lock:
            self._latencies.append(latency_s)
            self._fetches += 1
            self._errors += 1
            self._by_domain[domain] = self._by_domain.get(domain, 0) + 1
            cls = type(exc).__name__
            self._by_error[cls] = self._by_error.get(cls, 0) + 1

    @staticmethod
    def _percentile(sorted_vals, p: float) -> float:
        if not sorted_vals:
            return 0.0
        if len(sorted_vals) == 1:
            return float(sorted_vals[0])
        k = (len(sorted_vals) - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return float(sorted_vals[int(k)])
        return float(sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f))

    def to_dict(self) -> dict:
        with self._lock:
            lats = sorted(self._latencies)
            return {
                "fetches": self._fetches,
                "errors": self._errors,
                "bytes_downloaded": self._bytes,
                "latency_ms": {
                    "p50": round(self._percentile(lats, 0.50) * 1000, 1),
                    "p95": round(self._percentile(lats, 0.95) * 1000, 1),
                    "p99": round(self._percentile(lats, 0.99) * 1000, 1),
                    "max": round(lats[-1] * 1000, 1) if lats else 0.0,
                },
                "requests_by_domain": dict(
                    sorted(self._by_domain.items(), key=lambda kv: (-kv[1], kv[0]))
                ),
                "errors_by_class": dict(self._by_error),
            }


def _domain_of(url: str) -> str:
    """Best-effort host (netloc) for per-domain stats; falls back to url."""
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url


# Tier 2.6: Rust `tracing` -> Python `logging` bridge (opt-in via
# STITCH_RUST_LOG). Initialised once per process; no-op when unset.
# ── Inspection / extraction output models ──────────────────────

class ExtractionResult(BaseModel):
    """Unified result of a document extraction (cache hit or fresh).

    Both paths serialize to the same schema so consumers never need to
    branch on how the content was obtained.
    """
    source: str
    content: str = ""
    content_tokens: int = 0
    cache_hit: bool = False
    # Tier 1.2: set only for page-range reads (extract_document pages=...).
    # page_range echoes the request; page_start/page_end are the 1-based
    # inclusive bounds actually delivered (clamped to the document);
    # total_pages is the document's full page count. On a cache hit of a
    # range read the bounds are re-derivable only from the stored content,
    # so they stay 0/None there.
    page_range: Optional[str] = None
    page_start: Optional[int] = 0
    page_end: Optional[int] = 0
    total_pages: int = 0
    # Tier 1.3: provenance. fetched_at is the download/parse time (None
    # on cache hits — the stored content carries no timestamp);
    # content_hash is the SHA-256 of the full untruncated content, tying
    # range reads and cache hits back to the stored bytes. http_status /
    # final_url / content_type are set for URL sources.
    fetched_at: Optional[str] = None
    http_status: Optional[int] = None
    final_url: Optional[str] = None
    content_type: Optional[str] = None
    content_hash: Optional[str] = None
    # Deep-research support: URLs written into the document text (detected
    # from the full, untruncated content). Documents lose the <a href>
    # structure of HTML pages, so this is the only link signal they have.
    links: list[str] = Field(default_factory=list)
    # §7: optional prompt-injection guard block (present only when the
    # guard is enabled and a scanned scope was checked).
    guard: Optional[dict] = None


class InspectionResult(BaseModel):
    """Structured result of an HTML page inspection.

    ``truncated`` is True whenever the delivered link list is not the
    complete set found on the page — either the collection cap was hit
    (link_cap) or the output budget forced candidates to be dropped.
    """
    url: str
    markdown: str = ""
    markdown_tokens: int = 0
    follow_up_links: list[FollowUpCandidate] = Field(default_factory=list)
    delivered_links: int = 0
    total_links: int = 0
    truncated: bool = False
    fetch_method: Optional[str] = None
    cache_hit: bool = False
    # Tier 1.4: True when this read's cached entry had expired and a
    # conditional request (If-None-Match / If-Modified-Since) answered
    # 304 Not Modified, so the stored copy was re-freshened and re-served
    # without a body download. Content and fetched_at are the originals.
    revalidated: bool = False
    metadata: dict = Field(default_factory=dict)
    # Tier 1.1: set only when the caller passed a research query and the
    # page did not fit the output budget — the delivered markdown is then
    # the query-relevant subset of the page's sections.
    query: Optional[str] = None
    sections_available: int = 0
    sections_selected: int = 0
    section_anchors: list[str] = Field(default_factory=list)
    # Tier 1.2: paging metadata, set on every chunked (non-selection)
    # read. offset echoes the requested start; chars_total is the full
    # page length; next_offset is the character position to pass as
    # offset next to continue where this read stopped; has_more tells
    # whether anything follows this slice.
    offset: int = 0
    next_offset: Optional[int] = None
    has_more: bool = False
    chars_total: int = 0
    # Tier 1.3: provenance — which fetch this read was served from.
    # fetched_at is the original fetch time (the page cache preserves it
    # across chunked reads and cache hits); final_url is the URL after
    # redirects; content_hash is the SHA-256 of the full page markdown,
    # so every chunk of one page shares the same hash. cache_hit (above)
    # is the from_cache flag.
    fetched_at: Optional[str] = None
    http_status: Optional[int] = None
    final_url: Optional[str] = None
    content_type: Optional[str] = None
    content_hash: Optional[str] = None
    # §7: optional prompt-injection guard block (present only when the
    # guard is enabled and a scanned scope was checked).
    guard: Optional[dict] = None


# ───────────────────────────────
# Provenance helpers (Tier 1.3)
# ───────────────────────────────
# ── Provenance helpers (Tier 1.3) ──────────────────────────────

def _utc_now_iso() -> str:
    """Current time in ISO-8601 UTC (fetch timestamps)."""
    return datetime.now(timezone.utc).isoformat()


def _sha256_hex(text: str) -> str:
    """SHA-256 of a string, for content provenance hashes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _provenance_from_fetch_meta(
    meta: tuple,
    requested_url: str,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
) -> dict:
    """Normalize the Rust fetch's provenance tuple (http_status,
    final_url, content_type) into the payload dict form.

    Tier 1.4: when the server advertised validators, ``etag`` and
    ``last_modified`` are stored inside the provenance dict (the compact
    metadata whitelist keeps them out of the LLM payload) so an expired
    page can later be revalidated with If-None-Match / If-Modified-Since
    instead of re-downloaded.
    """
    status, final_url, content_type = meta
    prov = {
        "fetched_at": _utc_now_iso(),
        "http_status": status,
        "final_url": final_url or requested_url,
        "content_type": content_type,
    }
    if etag:
        prov["etag"] = etag
    if last_modified:
        prov["last_modified"] = last_modified
    return prov


def _browser_provenance(requested_url: str) -> dict:
    """Best-effort provenance for stealth-browser fetches. The browser
    layer does not surface the HTTP status or post-redirect URL, so a
    successful navigation is reported as 200 on the requested URL and
    content_type is left unknown."""
    return {
        "fetched_at": _utc_now_iso(),
        "http_status": 200,
        "final_url": requested_url,
        "content_type": None,
    }


# ───────────────────────────────
# Batch result record (M10, CODE_REVIEW_2026-08-27)
# ───────────────────────────────

@dataclass(frozen=True)
# ── Batch result record (M10) ──────────────────────────────────

class BatchEntry:
    """Tagged result of one URL in a batch fetch.

    The raw batch engine reports ``(url, md_or_error, links_or_None)``
    triples in which the failure marker is the links slot being None.
    This record makes that explicit: on success ``markdown``/``links``
    are set and ``error`` is None; on failure ``error`` is set and
    ``markdown``/``links`` are None. Callers branch on ``ok`` instead of
    guessing which slot carries what.
    """

    url: str
    markdown: Optional[str] = None
    links: Optional[List[str]] = None
    error: Optional[str] = None
    # Raw HTML of a successful fetch, so batch entries can run the same
    # meta-oxide extraction single-page reads do (bugfix 5).
    html: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _normalize_batch_results(results) -> List[BatchEntry]:
    """Convert raw engine tuples into tagged ``BatchEntry`` records (M10).

    Success: ``(url, html, markdown, links)`` with all three slots non-None.
    Failure: ``(url, None, error_message, None)`` (the message may be empty).
    """
    entries: List[BatchEntry] = []
    for url, html_opt, md_opt, links_opt in results:
        if md_opt is not None and links_opt is not None:
            entries.append(
                BatchEntry(
                    url=url, markdown=md_opt, links=links_opt, html=html_opt
                )
            )
        else:
            entries.append(BatchEntry(url=url, error=md_opt or "Unknown error"))
    return entries


# ───────────────────────────────
# Markdown link absolutization (M12, CODE_REVIEW_2026-08-27)
# ───────────────────────────────

# Smallest per-field character budget ``_fit_json`` will try before giving
# up and returning an overflow envelope. Below this a payload carries no
# usable content anyway, and halving further just burns tokenizer passes.
_JSON_FIT_FLOOR = 240

# Inline markdown link: [text](target) or [text](target "title"). The
# negative lookbehind skips image markers so ![alt](...) is untouched.
_MD_INLINE_LINK_RE = re.compile(
    r"(?<!!)\[([^\]]*)\]\(([^()\s]+)(?:\s+\"([^\"]*)\")?\)"
)

# Targets that are already self-contained or must not be rewritten.
_MD_NON_LINK_PREFIXES = (
    "#", "//", "mailto:", "tel:", "javascript:", "data:", "ftp:"
)

# ── Markdown link absolutization (M12) ─────────────────────────

def _absolutize_markdown_links(markdown: str, base_url: str) -> str:
    """Rewrite relative hrefs in a markdown body to absolute URLs (M12).

    The Rust core's markdown conversion keeps hrefs exactly as written
    (e.g. ``[A](/a)``); only the separate ``follow_up_links`` list is
    absolute. A model copying a markdown link would get an unresolvable
    URL, so the body is made self-contained via ``urljoin``. Absolute
    URLs, protocol-relative (``//``), fragment-only (``#``) and
    non-resource schemes (mailto/tel/data/javascript/ftp) pass through
    unchanged. Idempotent: re-running on absolutized text is a no-op.
    """
    if not markdown or "(" not in markdown:
        return markdown

    def _rewrite(m: re.Match[str]) -> str:
        target = m.group(2)
        if target.lower().startswith(_MD_NON_LINK_PREFIXES):
            return m.group(0)
        absolute = urljoin(base_url, target)
        if absolute == target:
            return m.group(0)
        title = m.group(3)
        if title is not None:
            return f"[{m.group(1)}]({absolute} \"{title}\")"
        return f"[{m.group(1)}]({absolute})"

    return _MD_INLINE_LINK_RE.sub(_rewrite, markdown)
