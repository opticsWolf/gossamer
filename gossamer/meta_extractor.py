"""
HTML metadata extraction via meta-oxide.

Wraps the meta-oxide Rust crate (through ``gossamer._core``) to
extract 13 metadata formats (Open Graph, Twitter Cards, JSON-LD,
Microdata, Dublin Core, etc.) from raw HTML. Falls back to the
legacy ``meta_oxide`` Python package when present, else to empty
results — metadata is always best-effort.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from gossamer import _core as _rust

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────
# Extraction lives in Rust (`src/metaextract.rs`, meta_oxide crate).
# The legacy `meta_oxide` Python package is an optional last-resort
# fallback (kept for lone-surrogate inputs, which cannot cross PyO3).
# ────────────────────────────────────────────────────────────────

_meta_oxide = None
try:
    import meta_oxide as _meta_oxide  # noqa: F401
except ImportError:
    pass


# ────────────────────────────────────────────────────────────────
# 1. Core Extraction
# ────────────────────────────────────────────────────────────────

def extract_all(html: str, base_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Extract all supported metadata formats from HTML.

    Returns a dict with keys:
      - meta: standard HTML meta (title, description, canonical, …)
      - opengraph: Open Graph properties
      - twitter: Twitter Card properties
      - jsonld: list of JSON-LD / Schema.org objects
      - microdata: list of HTML5 microdata items
      - microformats: nested dict of h-card, h-entry, …
      - dublin_core: Dublin Core elements
      - rdfa: list of RDFa triples
      - rel_links: dict mapping rel type → list of URLs
      - oembed: oEmbed endpoint discovery
      - manifest: Web App Manifest link

    If extraction is unavailable returns empty dicts.
    """
    try:
        return json.loads(_rust.meta_extract_all(html, base_url))
    except Exception as e:
        logger.debug("Rust meta extraction failed: %s", e)
    if _meta_oxide is not None:
        try:
            return _meta_oxide.extract_all(html, base_url)
        except Exception as e:
            logger.warning("meta-oxide extraction failed: %s", e)
    else:
        logger.debug("meta-oxide not installed — returning empty metadata")
    return _empty_result()


def extract_meta(html: str, base_url: Optional[str] = None) -> Dict[str, Any]:
    """Extract only standard HTML meta tags."""
    try:
        return json.loads(_rust.meta_extract_meta(html, base_url))
    except Exception:
        pass
    if _meta_oxide is None:
        return {}
    try:
        return _meta_oxide.extract_meta(html, base_url)
    except Exception:
        return {}


def extract_opengraph(html: str, base_url: Optional[str] = None) -> Dict[str, Any]:
    """Extract Open Graph metadata."""
    try:
        return json.loads(_rust.meta_extract_opengraph(html, base_url))
    except Exception:
        pass
    if _meta_oxide is None:
        return {}
    try:
        return _meta_oxide.extract_opengraph(html, base_url)
    except Exception:
        return {}


def extract_twitter(html: str, base_url: Optional[str] = None) -> Dict[str, Any]:
    """Extract Twitter Card metadata.

    Uses the with-fallback mapping (missing fields filled from Open
    Graph), matching what `extract_all` always returned — see
    ``src/metaextract.rs``.
    """
    try:
        return json.loads(_rust.meta_extract_twitter(html, base_url))
    except Exception:
        pass
    if _meta_oxide is None:
        return {}
    try:
        return _meta_oxide.extract_twitter_with_fallback(html, base_url)
    except Exception:
        return {}


def extract_jsonld(html: str, base_url: Optional[str] = None) -> List[Dict[str, Any]]:
    """Extract JSON-LD structured data."""
    try:
        return json.loads(_rust.meta_extract_jsonld(html, base_url))
    except Exception:
        pass
    if _meta_oxide is None:
        return []
    try:
        return _meta_oxide.extract_jsonld(html, base_url)
    except Exception:
        return []


# ────────────────────────────────────────────────────────────────
# 2. Merge metadata into DocumentMetadata
# ────────────────────────────────────────────────────────────────

def merge_into_document_metadata(
    html_meta: Dict[str, Any],
    base_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Merge HTML metadata extracted by meta-oxide into a base DocumentMetadata dict.

    Enriches the base metadata with:
      - title / description from HTML meta (if not already set)
      - og_title, og_image, og_type from Open Graph
      - twitter_card, twitter_title from Twitter Cards
      - canonical URL
      - jsonld (raw list of structured data objects)
      - rel_links, dublin_core, microformats (raw)

    Parameters
    ----------
    html_meta : dict
        Raw output from meta_oxide.extract_all().
    base_metadata : dict
        Pre-populated metadata dict (e.g. from file-level info).

    Returns
    -------
    dict
        Enriched metadata dict ready for DocumentMetadata construction.
    """
    merged = dict(base_metadata)

    # Field copies as (html_meta section, source key, merged key) triples.
    # A value is copied only when truthy; base values are never overwritten.
    field_copies = [
        # ── Standard HTML meta ────────────────────────────────────
        ("meta", "description", "description"),
        ("meta", "canonical", "canonical"),
        ("meta", "keywords", "keywords"),
        ("meta", "language", "language"),
        ("meta", "robots", "robots"),
        # ── Open Graph ────────────────────────────────────────────
        ("opengraph", "title", "og_title"),
        ("opengraph", "type", "og_type"),
        ("opengraph", "image", "og_image"),
        ("opengraph", "description", "og_description"),
        ("opengraph", "site_name", "og_site_name"),
        ("opengraph", "url", "og_url"),
        # ── Twitter Cards ─────────────────────────────────────────
        ("twitter", "card", "twitter_card"),
        ("twitter", "title", "twitter_title"),
        ("twitter", "description", "twitter_description"),
        ("twitter", "image", "twitter_image"),
        ("twitter", "site", "twitter_site"),
    ]
    for section, source_key, target_key in field_copies:
        value = html_meta.get(section, {}).get(source_key)
        if value:
            merged[target_key] = value

    # Title is special: an already-set base title wins over HTML meta.
    std_meta = html_meta.get("meta", {})
    if not merged.get("title") and std_meta.get("title"):
        merged["title"] = std_meta["title"]

    # og_images additionally must be a list (defensive against odd output).
    og_images = html_meta.get("opengraph", {}).get("images")
    if og_images and isinstance(og_images, list):
        merged["og_images"] = og_images

    # Whole-section passthroughs: raw sections copied verbatim when present.
    for section in (
        "jsonld",
        "rel_links",
        "dublin_core",
        "microformats",
        "microdata",
        "rdfa",
        "manifest",
    ):
        value = html_meta.get(section)
        if value:
            merged[section] = value

    return merged


def _empty_result() -> Dict[str, Any]:
    """Return an empty but structurally complete result dict."""
    return {
        "meta": {},
        "opengraph": {},
        "twitter": {},
        "jsonld": [],
        "microdata": [],
        "microformats": {},
        "dublin_core": {},
        "rdfa": [],
        "rel_links": {},
        "oembed": {},
        "manifest": {},
    }
