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
) -> List[Tuple[str, Optional[str], Optional[List[str]]]]:
    """Fetch *urls* concurrently on the shared runtime.

    Returns one ``(url, markdown_or_error, links)`` triple per URL; on
    failure the markdown slot carries the error string and links is
    ``None``.
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

def init_rust_logging(level: str) -> bool:
    """Initialize the Rust ``log`` -> Python ``logging`` bridge (Tier 2.6).

    *level* is one of ``trace``/``debug``/``info``/``warn``/``error``/``off``.
    Idempotent: the global logger is installed once; later calls adjust the
    level and re-emit the init marker. Returns True on success.
    """
    ...
