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

from typing import Any, Mapping, Optional, Sequence, Tuple

from gossamer import _core as _rust

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


def _extract_raw(item: Any) -> dict:
    """Raw identity fields for the Rust matching core.

    First truthy value per group, stringified — the same rule the old
    ``_scalar`` applied, so semantics are unchanged while the key
    computation and the collision loop live in ``src/dedupe.rs``.
    """

    def first(keys: Sequence[str]) -> Optional[str]:
        if isinstance(item, Mapping):
            candidates = (item.get(k) for k in keys)
        else:
            candidates = (getattr(item, k, None) for k in keys)
        for v in candidates:
            if v:
                return v if isinstance(v, str) else str(v)
        return None

    return {
        "doi": first(_DOI_KEYS),
        "url": first(_URL_KEYS),
        "title": first(_TITLE_KEYS),
        "snippet": first(_SNIPPET_KEYS[:1]),
        "summary": first(_SNIPPET_KEYS[1:2]),
        "description": first(_SNIPPET_KEYS[2:]),
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

    The matching core lives in Rust (``src/dedupe.rs``); this extracts
    the raw identity fields and reassembles the outcome with the
    original item objects. Pinned by ``tests/test_rust_parity_dedupe.py``.
    """
    items = list(results)
    raw = [_extract_raw(item) for item in items]
    kept_idx, dropped_plan = _rust.dedupe_plan(raw, list(by))
    kept = [items[i] for i in kept_idx]
    dropped = [
        {"index": i, "reason": reason, "match": match}
        for (i, reason, match) in dropped_plan
    ]
    return kept, dropped
