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
) -> List[Tuple[str, Optional[str], Optional[str], Optional[List[str]]]]:
    """Fetch *urls* concurrently on the shared runtime.

    Returns one ``(url, html, markdown_or_error, links)`` tuple per URL.
    The raw HTML lets the Python layer run the same metadata extraction
    single-page reads use. On failure ``html`` and ``links`` are ``None``
    and the markdown slot carries the error string.
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
