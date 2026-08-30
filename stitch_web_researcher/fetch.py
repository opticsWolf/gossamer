"""Low-level page-fetch entry points (browser_oxide + static fallback).

Extracted from ``agent_tools.py`` as part of the composition split.
Holds the module-level fetch helpers; the per-page fetch methods that
used to live on ``WebResearcherToolbox`` are added in a later phase.
"""


from __future__ import annotations

import logging
import os

from stitch_web_researcher import meta_extractor
from stitch_web_researcher._core import (
    extract_main_content_markdown,
    extract_links_from_html as _extract_links_from_html,
    fetch_html_full,
    process_rendered_html as _process_rendered_html,
    init_rust_logging as _init_rust_logging,
)
from stitch_web_researcher.models import (_browser_provenance, _provenance_from_fetch_meta)
from stitch_web_researcher.ssrf import validate_public_url

logger = logging.getLogger(__name__)
# ── Rust `tracing` -> Python `logging` bridge (opt-in) ─────────
_rust_log_initialized = False


def _maybe_init_rust_logging() -> None:
    global _rust_log_initialized
    if _rust_log_initialized:
        return
    level = os.environ.get("STITCH_RUST_LOG", "").strip()
    if not level:
        _rust_log_initialized = True
        return
    try:
        _init_rust_logging(level)
    except Exception:
        logger.debug("Rust logging bridge init failed", exc_info=True)
    _rust_log_initialized = True
# ── Smart fetch (browser_oxide with fallback) ──────────────────

_browser_oxide_available = False
try:
    import browser_oxide
    _browser_oxide_available = True
except ImportError:
    pass


def _fetch_with_browser_oxide(url: str) -> tuple[str, list[str], dict]:
    """Fetch a page using browser_oxide (stealth headless browser).

    Navigation runs in browser_oxide; link extraction and markdown
    conversion run in the Rust core (``_core.process_rendered_html``),
    so only ``browser_oxide`` itself needs to be installed.

    Returns (markdown, links, metadata) tuple.
    """
    browser = browser_oxide.Browser(profile=browser_oxide.Profile.chrome())
    try:
        page = browser.navigate(url, max_iterations=5)
        if page.is_challenge:
            raise RuntimeError(
                f"Anti-bot challenge detected ({page.verdict}) for {url}"
            )
        html = page.html
    finally:
        browser.close()

    # Extract HTML metadata via meta-oxide
    metadata = meta_extractor.extract_all(html, url)

    # Debug visibility: record which main-content container the Rust core's
    # heuristics selected (article / main / [role='main'] / .content / …).
    selector_label, _md = extract_main_content_markdown(html)
    metadata["content_selector"] = selector_label

    # Anchored links + markdown via the Rust core
    links = _extract_links_from_html(html, url, 100)
    markdown, _links, removed = _process_rendered_html(html, url)
    # S2: report how many hidden nodes the Rust core stripped.
    if removed:
        metadata["hidden_blocks_removed"] = removed
    # Tier 1.3: best-effort provenance (the browser layer does not
    # surface the HTTP status or the post-redirect URL).
    metadata["provenance"] = _browser_provenance(url)
    return markdown, links, metadata


# ───────────────────────────────
# Link classification & follow-up helpers
# (moved to structured_parser.py in 0.1.4 so the structured payload can
#  share the same FollowUpCandidate model; re-exported above for
#  backwards compatibility)
# ───────────────────────────────


def fetch_smart_page(url: str) -> tuple[str, list[str], dict]:
    """Fetch a page with headless JS rendering via browser_oxide.

    Falls back to a static Rust fetch if browser_oxide is unavailable or
    fails. The fallback now extracts metadata from the fetched HTML too
    (C2), so both paths return the same (markdown, links, metadata) shape.

    Returns (markdown, links, metadata) tuple.
    """
    # S1: this function is reachable with LLM-supplied URLs; the Rust
    # static path enforces the SSRF policy itself, but the browser path
    # needs the check here.
    validate_public_url(url)

    if _browser_oxide_available:
        try:
            return _fetch_with_browser_oxide(url)
        except Exception as e:
            logger.warning(
                "browser_oxide smart fetch failed for %s: %s -- falling back to static",
                url, e,
            )

    # Fallback to static Rust fetch — fetch_html_full keeps the raw HTML so
    # metadata extraction matches the browser path (C2).
    html, md, links, removed, prov = fetch_html_full(url, 100)
    metadata = meta_extractor.extract_all(html, url)
    if removed:
        metadata["hidden_blocks_removed"] = removed
    metadata["provenance"] = _provenance_from_fetch_meta(prov, url)
    return md, links, metadata
