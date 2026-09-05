"""Shared result-deduplication helpers (Workstream 2).

Pure, offline, side-effect free. Previously each tool kept its own
``seen``-set dedup (``research`` on normalised URLs, ``SearchService`` on
normalised URLs), so duplicate detection lived in several places. This
module centralises it.

Matching is layered by identity strength:

  * ``doi``   -- strongest identity signal (the same work, possibly with
    different landing pages / tracking params);
  * ``url``   -- normalised URL (scheme/host lower-cased, default port and
    trailing slash dropped, query + fragment removed);
  * ``hash``  -- SHA-256 of ``title`` + ``snippet`` (weak signal, last
    resort for near-duplicate records that share neither DOI nor URL).

``dedupe`` returns ``(kept, dropped)`` where *dropped* is a list of
``{"index", "reason", "match"}`` -- the original position, the field the
duplicate collided on, and the matching key value -- so a caller can report
or log exactly why an entry was removed.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from gossamer import _core as _rust
from gossamer.config import canonical_url

__all__ = ["content_hash", "dedupe"]

# A result is either a mapping (search / provider dict) or an object with
# ``doi`` / ``url`` / ``title`` / ``snippet`` attributes (e.g. a
# ``BibliographicRecord``). These tuples name the keys/attributes to try,
# in priority order, for each identity field.
_DOI_KEYS = ("doi",)
_URL_KEYS = ("url",)
_TITLE_KEYS = ("title",)
_SNIPPET_KEYS = ("snippet", "summary", "description")


def content_hash(text: str) -> str:
    """SHA-256 hex digest of *text* (content identity for weak dedup)."""
    # Implemented in Rust (src/urls.rs).
    return _rust.content_hash(text)


def _scalar(item: Any, keys: Sequence[str]) -> Optional[str]:
    """First non-empty value for *keys* from a dict (by key) or object (attr)."""
    if isinstance(item, Mapping):
        candidates = (item.get(k) for k in keys)
    else:
        candidates = (getattr(item, k, None) for k in keys)
    for v in candidates:
        if v:
            return v if isinstance(v, str) else str(v)
    return None


def _doi_key(item: Any) -> Optional[str]:
    doi = _scalar(item, _DOI_KEYS)
    return doi.strip().lower() if doi else None


def _url_key(item: Any) -> Optional[str]:
    """Canonical URL key: same resource regardless of scheme/host case,
    ``www.``, default port, trailing slash, query string, or fragment.

    Delegates to :func:`gossamer.config.canonical_url` (``drop`` mode) so
    the shared dedup agrees with the cache and search identities (B.1).
    """
    url = _scalar(item, _URL_KEYS)
    if not url:
        return None
    try:
        return canonical_url(url, query="drop")
    except ValueError:
        return None


def _hash_key(item: Any) -> Optional[str]:
    title = _scalar(item, _TITLE_KEYS) or ""
    snippet = _scalar(item, _SNIPPET_KEYS) or ""
    if not title and not snippet:
        return None
    return content_hash(f"{title.strip()}||{snippet.strip()}")


_FIELD_FOR: Dict[str, Callable[[Any], Optional[str]]] = {
    "doi": _doi_key,
    "url": _url_key,
    "hash": _hash_key,
}


def dedupe(
    results: Sequence[Any],
    by: Tuple[str, ...] = ("doi", "url", "hash"),
) -> Tuple[list, list]:
    """Collapse duplicates in *results*, preserving first-seen order.

    Parameters
    ----------
    results:
        Iterable of result dicts (``{"url", "title", "snippet", "doi"}``)
        or objects exposing ``doi`` / ``url`` / ``title`` / ``snippet``
        attributes (e.g. ``BibliographicRecord``).
    by:
        Identity fields tried in priority order. An item is a duplicate of
        an already-kept item when it matches on the first *by* field that
        both carry a value. Defaults to ``("doi", "url", "hash")``.

    Returns
    -------
    (kept, dropped)
        *kept* is the surviving items in original order. *dropped* is a
        list of ``{"index", "reason", "match"}``.
    """
    kept: list = []
    dropped: list = []
    # field -> {key_value: kept_index}
    seen: dict = {}
    for i, item in enumerate(results):
        reason: Optional[str] = None
        match: Optional[str] = None
        for field in by:
            fn = _FIELD_FOR.get(field)
            if fn is None:
                continue
            key = fn(item)
            if not key:
                continue
            bucket = seen.setdefault(field, {})
            if key in bucket:
                reason, match = field, key
                break
        if reason is None:
            # Register every identity this kept item carries, so a later
            # item can collide on DOI *or* URL *or* hash as appropriate.
            for field in by:
                fn = _FIELD_FOR.get(field)
                if fn is None:
                    continue
                key = fn(item)
                if key:
                    seen.setdefault(field, {})[key] = len(kept)
            kept.append(item)
        else:
            dropped.append({"index": i, "reason": reason, "match": match})
    return kept, dropped
