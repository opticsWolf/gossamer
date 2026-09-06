"""Type stubs for the Rust core module.

``_core`` is built from ``src/lib.rs`` by maturin (PyO3, abi3). This stub
mirrors the exported ``#[pyfunction]`` signatures so type checkers see
real annotations instead of ``Any`` (see py.typed). Keep in sync with
src/lib.rs — the ``#[pyo3(signature = ...)]`` attributes are the source
of truth for parameter names and defaults.
"""

from typing import List, Optional, Tuple

def fetch_and_extract(
    url: str, max_bytes: Optional[int] = None
) -> Tuple[str, List[str]]:
    """Fetch *url* and return ``(markdown, links)``."""
    ...

def batch_research(
    urls: List[str],
    max_links: int = 500,
    max_concurrency: int = 8,
    domain_gap_ms: int = 0,
    max_bytes: Optional[int] = None,
) -> List[Tuple[str, Optional[str], Optional[str], Optional[List[str]], Optional[Tuple[int, str, Optional[str]]]]]:
    """Fetch *urls* concurrently on the shared runtime.

    Returns one ``(url, html, markdown_or_error, links, provenance)`` tuple
    per URL. The raw HTML lets the Python layer run the same metadata
    extraction single-page reads use; ``provenance`` is the
    ``(http_status, final_url, content_type)`` tuple so batch entries carry
    Tier 1.3 provenance. On failure ``html``, ``links`` and ``provenance``
    are ``None`` and the markdown slot carries the error string.
    """
    ...

def process_rendered_html(
    html: str, url: str
) -> Tuple[str, List[str], int]:
    """Parse caller-provided HTML; return ``(markdown, links, hidden_removed)``."""
    ...

def fetch_and_extract_linked(
    url: str, max_links: int = 100, max_bytes: Optional[int] = None
) -> Tuple[str, List[Tuple[str, str]]]:
    """Fetch *url*; return ``(markdown, links)`` with links as ``(href, text)`` pairs."""
    ...

def fetch_html_full(
    url: str, max_links: int = 100, max_bytes: Optional[int] = None
) -> Tuple[str, str, List[Tuple[str, str]], int, Tuple[int, str, Optional[str]]]:
    """Fetch *url*;
    return ``(html, markdown, links, hidden_removed, provenance)`` where
    provenance is ``(http_status, final_url, content_type)`` (Tier 1.3)."""
    ...

def fetch_html_conditional(
    url: str,
    max_links: int = 100,
    max_bytes: Optional[int] = None,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
) -> Tuple[
    bool,
    str,
    str,
    List[Tuple[str, str]],
    int,
    Tuple[int, str, Optional[str]],
    Optional[str],
    Optional[str],
]:
    """Conditional fetch of *url* (Tier 1.4).

    *etag* / *last_modified* are sent as If-None-Match / If-Modified-Since.
    Returns ``(not_modified, html, markdown, links, hidden_removed,
    provenance, etag, last_modified)`` where provenance is
    ``(http_status, final_url, content_type)``. On a 304 answer
    *not_modified* is True, html/markdown/links are empty, and the trailing
    etag/last_modified carry the (possibly rotated) response validators.
    """
    ...

def extract_links_from_html(
    html: str, url: str, max_links: int = 100
) -> List[Tuple[str, str]]:
    """Extract ``(href, text)`` links from caller-provided HTML,
    resolved against *url*."""
    ...

def extract_main_content_markdown(html: str) -> Tuple[str, str]:
    """Run main-content heuristics on caller-provided HTML.

    Returns ``(matched_selector_label, markdown_of_that_region)``.
    """
    ...

def extract_tables_from_html(
    html: str, max_tables: int = 20, max_rows: int = 500
) -> List[Tuple[str, List[str], List[List[str]]]]:
    """Extract HTML ``<table>`` grids as ``(name, headers, rows)`` (Tier 3.11).

    ``colspan``/``rowspan`` are expanded so every row has equal width; spanned
    cells are filled with the empty string. The first row becomes ``headers``
    when it contains at least one ``<th>``. Table names come from
    ``<caption>`` when present, otherwise ``table-N`` (1-based).
    """
    ...

def init_rust_logging(level: str) -> bool:
    """Initialize the Rust ``log`` -> Python ``logging`` bridge (Tier 2.6).

    *level* is one of ``trace``/``debug``/``info``/``warn``/``error``/``off``.
    Idempotent: the global logger is installed once; later calls adjust the
    level and re-emit the init marker. Returns True on success.
    """
    ...

def configure_http(
    proxy: Optional[str],
    user_agent: Optional[str],
    headers: List[Tuple[str, str]],
    cookies: List[Tuple[str, str]],
) -> None:
    """Set HTTP transport overrides (Tier 2.7): proxy, User-Agent, default
    headers, and cookies. Process-level settings baked into the lazily-built
    shared client at first use. No-op when every argument is empty/None.
    """
    ...

def normalize_url(raw: Optional[str], base: Optional[str] = None) -> str:
    """Port of ``gossamer.config.normalize_url`` (src/urls.rs).

    Raises ``ValueError`` with identical messages for non-URL input.
    """
    ...

def canonical_url(url: str, query: str = "keep") -> str:
    """Port of ``gossamer.config.canonical_url`` (src/urls.rs).

    ``query`` is ``keep``/``drop``/``drop-tracking`` (unknown falls
    through to ``keep``). Raises ``ValueError`` like the original.
    """
    ...

def content_hash(text: Optional[str] = None) -> str:
    """SHA-256 hex digest (port of ``gossamer.dedup.content_hash``)."""
    ...

def text_links_scan(text: str, max_links: int = 50) -> List[str]:
    """URL scan core (port of ``gossamer.text_links`` matching).

    Input validation (non-string/empty/non-positive cap) stays on the
    Python side; this never returns ``[]``-by-contract violations.
    """
    ...

def dedupe_plan(
    items: List[dict],
    by: Optional[List[str]] = None,
) -> tuple:
    """Dedup collision core (port of ``gossamer.dedup`` matching).

    *items* are pre-extracted ``doi/url/title/snippet/summary/description``
    maps (missing → ``None``). Returns ``(kept_indices, dropped)`` with
    dropped entries as ``(index, reason, match)`` tuples.
    """
    ...

class Section:
    anchor: str
    text: str
    offset: int

class SectionSelection:
    markdown: str
    total_sections: int
    selected_count: int
    @property
    def anchors(self) -> tuple: ...

def split_sections(markdown: str) -> List[Section]:
    """Port of ``gossamer.sections.split_sections`` (src/sections.rs)."""
    ...

def tokenize_text(text: str) -> List[str]:
    """Port of ``gossamer.sections.tokenize_text`` (src/sections.rs)."""
    ...

def bm25_scores(
    query_tokens: List[str],
    docs: List[str],
    k1: float = 1.5,
    b: float = 0.75,
) -> List[float]:
    """Port of ``gossamer.sections.bm25_scores`` (src/sections.rs)."""
    ...

def select_relevant_sections(
    markdown: str,
    query: str,
    max_chars: int,
) -> Optional[SectionSelection]:
    """Port of ``gossamer.sections.select_relevant_sections``."""
    ...

def normalize_scopes(scopes: Optional[List[str]] = None) -> List[str]:
    """Port of ``gossamer.guard._normalize_scopes`` (src/guard.rs)."""
    ...

def validate_guard_config(
    mode: str,
    threshold: float,
    chunk_chars: int,
    chunk_overlap: int,
    max_chunks: int,
    scopes: Optional[List[str]] = None,
) -> List[str]:
    """Port of ``GuardConfig.__post_init__`` validation (src/guard.rs)."""
    ...

def chunk_text(
    text: str,
    chunk_chars: int,
    overlap: int,
    max_chunks: int,
) -> List[tuple]:
    """Overlapping ``(offset, window)`` chunks (src/guard.rs)."""
    ...

def normalize_untrusted_text(text: str) -> str:
    """C* strip + NFKC (port of ``gossamer.guard``, src/guard.rs)."""
    ...

def redact_spans(text: str, spans: list) -> str:
    """Redact ``(offset, end, score)`` spans (src/guard.rs)."""
    ...

def wrap_untrusted(markdown: str, source_url: str) -> str:
    """Untrusted-content wrapper (src/guard.rs)."""
    ...

def classify_query(query: str | None = None) -> str:
    """Category name for *query* (port of the ``classify`` kernel)."""
    ...

def resolve_encoding(model_name: str) -> str:
    """Model → encoding name (port of ``token_budget`` resolution)."""
    ...

def embedded_encodings() -> List[str]:
    """Encoding names with embedded BPE ranks (Rust-side)."""
    ...

def count_tokens(text: str, model_name: str = "gpt-4o") -> int:
    """BPE token count (src/tokens.rs; ValueError on special tokens)."""
    ...

def truncate_to_tokens(
    text: str,
    max_tokens: int,
    model_name: str = "gpt-4o",
    ellipsis: Optional[str] = None,
) -> str:
    """Token-boundary truncation (src/tokens.rs)."""
    ...

def fit_context_window(
    pieces: List[str],
    max_tokens: int,
    model_name: str = "gpt-4o",
) -> List[str]:
    """Greedy budget packing (src/tokens.rs)."""
    ...

class BibliographicRecord:
    def __init__(
        self,
        title: Optional[str] = None,
        authors: Optional[List[str]] = None,
        year: Optional[str] = None,
        month: Optional[str] = None,
        day: Optional[str] = None,
        doi: Optional[str] = None,
        url: Optional[str] = None,
        venue: Optional[str] = None,
        publisher: Optional[str] = None,
        abstract: Optional[str] = None,
        extra: Optional[object] = None,
        id: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> None: ...
    title: Optional[str]
    authors: List[str]
    year: Optional[str]
    month: Optional[str]
    day: Optional[str]
    doi: Optional[str]
    url: Optional[str]
    venue: Optional[str]
    publisher: Optional[str]
    abstract: Optional[str]
    extra: object
    id: Optional[str]
    kind: Optional[str]

def citation_record_from_json(result_json: str) -> BibliographicRecord:
    """Build a record from a result dict snapshot (src/cite.rs)."""
    ...

def cite_bibtex(records: list) -> str: ...
def cite_csl_json(records: list) -> str: ...
def cite_apa_approx(records: list) -> str: ...
def cite_mla_approx(records: list) -> str: ...
def cite_venue_from_raw(raw: Optional[str]) -> Optional[str]: ...
def cite_abstract_from_raw(raw: Optional[str]) -> Optional[str]: ...

def ssrf_check_url(url: str, allow_private: bool = False) -> None:
    """SSRF policy check (src/ssrf.rs). Raises ValueError; the
    ``gossamer.ssrf`` wrapper maps it to ``SsrfBlockedError``."""
    ...

def robots_url_path(url: str) -> str:
    """Path+query for robots matching (src/robots.rs)."""
    ...

def robots_parse(text: str, user_agent: str) -> tuple:
    """Parse robots.txt → ([(allow, path)], delay|None) (src/robots.rs)."""
    ...

def robots_match_url(rules: list, url_path: str) -> bool:
    """Longest-match-wins evaluation (src/robots.rs)."""
    ...
