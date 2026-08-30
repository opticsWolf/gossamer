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

# Imports for FetchService (Phase 3 composition):
import asyncio
import json
import time
from typing import Optional

from stitch_web_researcher._core import batch_research, fetch_html_conditional
from stitch_web_researcher.token_budget import count_tokens
from stitch_web_researcher.structured_parser import build_follow_up_candidates
from stitch_web_researcher.sections import select_relevant_sections
from stitch_web_researcher.guard import evaluate, wrap_untrusted
from stitch_web_researcher.config import (
    _coerce_fetch_mode,
    _resolve_fetch_strategy,
    FetchMode,
    normalize_url,
)
from stitch_web_researcher.models import (
    _absolutize_markdown_links,
    _domain_of,
    _normalize_batch_results,
    _sha256_hex,
    InspectionResult,
)


class FetchService:

    """Fetch/inspect collaborator (fetch.py)."""

    def __init__(self, tb):
        """Store the toolbox reference; read all shared state via self._tb."""
        self._tb = tb

    def _format_follow_ups(self, anchored_links) -> list:
        """Turn (url, anchor_text) pairs into LLM-friendly candidates.

        Each candidate carries the anchor text (so the model can judge
        relevance by name) and a 'type' hint: 'document' links should be
        fetched via extract_document, 'page' links via inspect_html_page.

        No truncation here: the caller (LLM) performs relevance selection
        itself, based on the research topic; we deliver the complete
        titled/typed candidate list. (The structured payload path, in
        contrast, honors max_links.)
        """
        return build_follow_up_candidates(anchored_links)


    def _build_inspection_result(
        self,
        url: str,
        markdown: str,
        links_pairs,
        meta_summary: dict,
        fetch_method: Optional[str],
        markdown_truncated: bool = False,
        html_metadata: Optional[dict] = None,
        page_markdown: Optional[str] = None,
    ) -> InspectionResult:
        """Assemble an InspectionResult and enforce the output budget.

        Budget enforcement drops candidates from the END (halving) until the
        serialized JSON fits within max_markdown_chars / max_tokens. The
        markdown is expected to have been pre-truncated with a links reserve
        (``_content_budget``) so that the envelope has room to keep some
        links on content-rich pages (C1). The ``truncated`` flag records any
        loss — collection-cap hits, markdown truncation, AND budget-driven
        drops — so the model always knows whether it saw every link.
        ``delivered_links`` makes the flag actionable. The returned JSON is
        schema-valid by construction (Pydantic serializes it; we never
        string-cut the output).
        """
        result = InspectionResult(
            url=url,
            markdown=markdown,
            markdown_tokens=count_tokens(markdown, self._tb.model_name),
            follow_up_links=self._format_follow_ups(links_pairs),
            total_links=len(links_pairs),
            truncated=len(links_pairs) >= self._tb.link_cap,
            fetch_method=fetch_method,
            metadata=meta_summary or {},
        )
        # Tier 1.3: provenance is attached BEFORE the budget loop so the
        # M11 invariant holds on exactly what is delivered — the hash and
        # timestamps count against the budget, not just the rest.
        if html_metadata is not None:
            self._apply_provenance(
                result, html_metadata, page_markdown if page_markdown is not None else markdown
            )
        # §7: the prompt-injection guard runs after truncation, before the
        # budget loop, so the guard block counts against the M11 invariant.
        if self._scan_inspection_guard(result):
            return result

        candidates = result.follow_up_links
        n_total = len(candidates)
        # M11: the old loop re-tokenized the ENTIRE payload on every
        # halving pass (up to ~9 full tokenizations of a large JSON
        # string). Instead, tokenize exactly two payloads up front — the
        # envelope with no links and the full payload — and interpolate
        # the cost of the shrinking link list per pass. Any pass the
        # estimate says fits is verified with one exact tokenization
        # before being accepted, so the final payload always satisfies
        # the token budget.
        envelope_tokens = 0
        if self._tb.max_tokens > 0 and n_total:
            result.follow_up_links = []
            envelope_tokens = count_tokens(
                result.model_dump_json(), self._tb.model_name
            )
            result.follow_up_links = candidates

        full_tokens = 0
        while True:
            payload = result.model_dump_json()
            over_chars = len(payload) > self._tb.max_markdown_chars
            over_tokens = False
            if self._tb.max_tokens > 0:
                n_now = len(result.follow_up_links)
                if n_now == n_total:
                    full_tokens = count_tokens(payload, self._tb.model_name)
                    over_tokens = full_tokens > self._tb.max_tokens
                elif n_now == 0:
                    over_tokens = envelope_tokens > self._tb.max_tokens
                else:
                    est = envelope_tokens + (full_tokens - envelope_tokens) * n_now / n_total
                    if est > self._tb.max_tokens:
                        over_tokens = True
                    else:
                        # Boundary safety: the linear estimate can be off
                        # by a token or two; verify exactly before accepting.
                        over_tokens = count_tokens(payload, self._tb.model_name) > self._tb.max_tokens
            if not (over_chars or over_tokens):
                break
            if not result.follow_up_links:
                break  # nothing left to drop; markdown is already pre-truncated
            keep = len(result.follow_up_links) // 2
            result.follow_up_links = result.follow_up_links[:keep]
            result.truncated = True
        result.truncated = result.truncated or markdown_truncated
        result.delivered_links = len(result.follow_up_links)
        result.total_links = max(result.total_links, len(candidates))
        return result


    def _scan_inspection_guard(self, result: InspectionResult) -> bool:
        """§7: scan the inspection output for prompt injection.

        Runs the guard over the configured scopes (page_markdown /
        page_metadata / follow_up_titles) after truncation. Attaches
        ``result.guard`` and applies the mode: ``annotate`` wraps the
        delivered markdown in an untrusted-content marker, ``redact``
        replaces flagged chunks, ``block`` empties the content (the caller
        withholds the result). Returns True when the content was withheld.
        """
        meta_text = " ".join(
            str(v) for v in (result.metadata or {}).values() if v
        )
        title_text = " ".join(c.title for c in result.follow_up_links)
        block, redacted, withheld = evaluate(
            self._tb._guard,
            [
                ("page_markdown", result.markdown),
                ("page_metadata", meta_text),
                ("follow_up_titles", title_text),
            ],
            main_scope="page_markdown",
        )
        if block is None:
            return False
        result.guard = block
        if withheld:
            result.markdown = ""
            result.markdown_tokens = 0
            result.follow_up_links = []
            result.delivered_links = 0
            return True
        if block.get("action") == "redact" and redacted is not None:
            result.markdown = redacted
        elif (
            block.get("action") == "annotate"
            and "page_markdown" in block.get("scopes", [])
            and redacted
        ):
            result.markdown = wrap_untrusted(redacted, result.url)
        if result.markdown:
            result.markdown_tokens = count_tokens(result.markdown, self._tb.model_name)
        return False


    def _scan_structured_guard(self, payload, url: str) -> bool:
        """§7: scan a structured payload for prompt injection.

        Scans the page_markdown (pages[0].markdown), page_metadata, and
        follow_up_titles scopes. Attaches ``payload.guard`` and applies the
        mode to the main markdown. Returns True when the content was withheld.
        """
        main_md = payload.pages[0].markdown if payload.pages else ""
        meta = payload.metadata.model_dump() if payload.metadata else {}
        meta_text = " ".join(str(v) for v in meta.values() if v)
        title_text = " ".join(
            (getattr(cand, "title", None) or "") for cand in payload.links
        )
        block, redacted, withheld = evaluate(
            self._tb._guard,
            [
                ("page_markdown", main_md),
                ("page_metadata", meta_text),
                ("follow_up_titles", title_text),
            ],
            main_scope="page_markdown",
        )
        if block is None:
            return False
        payload.guard = block
        if withheld:
            for page in payload.pages:
                page.markdown = ""
            return True
        if payload.pages:
            if block.get("action") == "redact" and redacted is not None:
                payload.pages[0].markdown = redacted
            elif (
                block.get("action") == "annotate"
                and "page_markdown" in block.get("scopes", [])
                and redacted
            ):
                payload.pages[0].markdown = wrap_untrusted(redacted, url)
        return False


    def _static_fetch(self, url: str, keep_html: bool = False):
        """Plain HTTP fetch via the Rust core.

        C2: the Rust core returns the raw HTML alongside the markdown and
        links, so the static path runs the same meta-oxide metadata
        extraction as the browser path — no second network round-trip.

        Tier 3.11: ``keep_html=True`` also returns the raw HTML (5-tuple)
        so table extraction can run on it; the default keeps the M8-pinned
        4-tuple contract.
        """
        (
            _not_modified,
            html,
            md,
            links,
            removed,
            prov,
            etag,
            last_modified,
        ) = fetch_html_conditional(url, self._tb.link_cap, self._tb.max_response_bytes)
        metadata = meta_extractor.extract_all(html, url)
        if removed:
            metadata["hidden_blocks_removed"] = removed
        # Tier 1.3: provenance — status, final URL after redirects,
        # content type, and the fetch time.
        metadata["provenance"] = _provenance_from_fetch_meta(
            prov, url, etag=etag, last_modified=last_modified
        )
        if keep_html:
            return md, links, metadata, "static", html
        return md, links, metadata, "static"


    def _browser_fetch(self, url: str):
        """Stealth-browser fetch; failures propagate (strict)."""
        md, links, meta = _fetch_with_browser_oxide(url)
        meta.setdefault("provenance", _browser_provenance(url))
        return md, links, meta, "browser"


    def _fetch_html(self, url: str, use_smart: str = FetchMode.AUTO.value):
        """Fetch an HTML page honoring ``self._tb.fetch_mode`` / ``use_smart``.

        ``fetch_mode`` (config) sets the baseline:
            "browser": every fetch goes through the stealth browser;
                failures propagate (strict).
            "static": plain HTTP fetch via the Rust core only.
            "auto": static first; falls back to the stealth browser when
                the static fetch raises or returns non-text content.

        ``use_smart`` (per call, one of "auto"/"browser"/"static",
        default "auto") overrides it: "static" is static-only, "browser"
        tries the stealth browser first (falling back to static), and
        "auto" defers to ``fetch_mode``.

        M12: the returned markdown has relative hrefs rewritten to
        absolute URLs so the body is self-contained for the model.
        """
        md, links, meta, method = self._fetch_html_dispatch(url, use_smart)
        return _absolutize_markdown_links(md, url), links, meta, method


    def _fetch_html_with_html(
        self, url: str, use_smart: str = FetchMode.AUTO.value
    ) -> tuple:
        """Fetch for ``inspect_html_structured`` (Tier 3.11).

        Same dispatch and fetch instrumentation as ``_fetch_html``, but
        the static path also returns the raw HTML so tables can be
        extracted from it. Returns ``(markdown, links, meta, method,
        html)`` with markdown absolutized (M12); ``html`` is None when the
        browser path served the page (the renderer exposes no raw DOM).
        """
        domain = _domain_of(url)
        started = time.perf_counter()
        try:
            result = self._dispatch_fetch(url, use_smart, keep_html=True)
        except Exception as e:
            self._tb._fetch_stats.record_error(domain, time.perf_counter() - started, e)
            raise
        nbytes = (
            len(result[0].encode("utf-8"))
            if result and isinstance(result[0], str)
            else 0
        )
        self._tb._fetch_stats.record_success(
            domain, time.perf_counter() - started, nbytes
        )
        md, links, meta, method, html = result
        return _absolutize_markdown_links(md, url), links, meta, method, html


    def _fetch_html_dispatch(
        self, url: str, use_smart: str = FetchMode.AUTO.value
    ):
        """Instrumented dispatch wrapper (Tier 2.6).

        Records per-fetch latency, bytes, domain, and error class into
        ``self._tb._fetch_stats`` before delegating to :meth:`_dispatch_fetch`.
        """
        domain = _domain_of(url)
        started = time.perf_counter()
        try:
            result = self._dispatch_fetch(url, use_smart)
        except Exception as e:
            self._tb._fetch_stats.record_error(domain, time.perf_counter() - started, e)
            raise
        nbytes = (
            len(result[0].encode("utf-8"))
            if result and isinstance(result[0], str)
            else 0
        )
        self._tb._fetch_stats.record_success(
            domain, time.perf_counter() - started, nbytes
        )
        return result


    def _dispatch_fetch(
        self, url: str, use_smart: str = FetchMode.AUTO.value, keep_html: bool = False
    ):
        """Dispatch a fetch per ``self._tb.fetch_mode`` / ``use_smart`` (raw,
        with relative markdown hrefs). See ``_fetch_html``.

        Tier 3.11: ``keep_html=True`` extends the result with the raw HTML
        (5-tuple). Only the static path has it — browser renders do not
        expose the raw DOM, so that slot is None there.

        ``use_smart`` is coerced via :func:`_coerce_fetch_mode` and combined
        with ``fetch_mode`` by :func:`_resolve_fetch_strategy` into one of
        four strategies: ``static-only``, ``browser-only``, ``browser-first``
        (browser with static fallback) and ``auto`` (static-first with
        stealth-browser fallback on failure/non-text).
        """


        def _static():
            # Keep the default-path call shape (single positional arg, M8)
            # so existing _static_fetch test spies keep working; only the
            # keep_html=True path passes the extra argument.
            return (
                self._static_fetch(url, keep_html=True)
                if keep_html
                else self._static_fetch(url)
            )

        strategy = _resolve_fetch_strategy(
            self._tb.fetch_mode, _coerce_fetch_mode(use_smart)
        )

        if strategy == "static-only":
            return _static()

        if strategy == "browser-only":
            md, links, meta, method = self._browser_fetch(url)
            return (md, links, meta, method, None) if keep_html else (md, links, meta, method)

        if strategy == "browser-first":
            try:
                md, links, meta, method = self._browser_fetch(url)
                return (
                    (md, links, meta, method, None)
                    if keep_html
                    else (md, links, meta, method)
                )
            except Exception as e:
                logger.warning(
                    "Stealth fetch failed for %s: %s -- falling back to static", url, e
                )
            return _static()

        # strategy == "auto": static first, stealth fallback on failure or
        # non-text content
        try:
            result = _static()
            if self._looks_like_text(result[0]):
                return result
            logger.info("Static fetch returned non-text content for %s", url)
        except Exception as e:
            logger.warning("Static fetch failed for %s: %s -- trying stealth browser", url, e)

        if not _browser_oxide_available:
            raise RuntimeError(
                f"Fetch failed for {url} and browser_oxide is not installed"
            )
        md, links, meta = _fetch_with_browser_oxide(url)
        meta.setdefault("provenance", _browser_provenance(url))
        result = (md, links, meta, "stealth-fallback")
        return result + (None,) if keep_html else result


    @staticmethod
    def _looks_like_text(md: str) -> bool:
        """Heuristic: reject empty or binary-garbage payloads (e.g. undecoded
        compressed responses), which would otherwise poison LLM context.

        Uses Unicode categories rather than printability: legitimate text in
        any language (incl. CJK) contains almost no control/format/unassigned
        codepoints, while binary bytes mis-decoded as text are full of them.

        M14: samples head, middle and tail (not just the first 2000 chars),
        because a payload that starts clean and degenerates later (e.g. a
        partially decoded gzip) must also trip the gate. A page is rejected
        if any sampled window exceeds the 2% bad-codepoint ratio.
        """
        if not md or not md.strip():
            return False
        import unicodedata


        def _bad_ratio(chunk: str) -> float:
            if not chunk:
                return 0.0
            bad = sum(
                unicodedata.category(c) in ("Cc", "Cn", "Co", "Cs")
                and c not in "\n\r\t"
                for c in chunk
            )
            return bad / len(chunk)

        n = len(md)
        samples = (
            md[:2000],
            md[n // 2 - 1000 : n // 2 + 1000],
            md[-2000:],
        )
        return all(_bad_ratio(s) < 0.02 for s in samples)


    def _page_cache_get(self, url: str):
        """Return a cached (markdown, links, meta, method) tuple, or None.

        Entries store the *untruncated* fetch result so budget changes
        between calls are honored on every read. Keys are namespaced
        ("page:") so they can never collide with structured-payload or
        document entries for the same URL.
        """
        raw = self._tb.cache.get("page:" + self._tb._cache_key(url))
        if raw is None:
            return None
        try:
            entry = json.loads(raw)
            return (
                entry.get("markdown", ""),
                [tuple(pair) for pair in entry.get("links", [])],
                entry.get("meta") or {},
                entry.get("method"),
            )
        except (json.JSONDecodeError, TypeError, AttributeError):
            logger.warning("Corrupt page-cache entry for %s -- refetching", url)
            return None


    def _page_cache_put(
        self,
        url: str,
        markdown: str,
        links: list,
        metadata: dict,
        method: Optional[str],
    ) -> None:
        """Cache an untruncated fetch result under the canonical URL key."""
        self._tb.cache.put(
            "page:" + self._tb._cache_key(url),
            json.dumps(
                {
                    "markdown": markdown,
                    "links": links,
                    "meta": metadata,
                    "method": method,
                },
                ensure_ascii=False,
            ),
        )


    def _stale_page_entry(self, url: str) -> Optional[dict]:
        """Tier 1.4: read the raw page-cache entry ignoring TTL, without
        purging it, so an expired entry's ETag / Last-Modified can drive a
        cheap conditional revalidation. Returns the parsed entry dict or
        None when the key was never stored.

        Called *before* ``_page_cache_get`` (whose ``Cache.get`` purges
        expired entries), so the validators are still on disk.
        """
        raw = self._tb.cache.get_stale("page:" + self._tb._cache_key(url))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None


    def _revalidate_stale_entry(self, url: str, entry: dict):
        """Tier 1.4: conditionally revalidate a stale (expired) static page.

        ``entry`` is the parsed raw page-cache entry captured before the
        normal lookup purged it. Returns ``(was_304, (markdown, links,
        meta, method))`` on success, or None when there is nothing to
        revalidate (non-static entry, no validators, or the conditional
        request failed) - the caller then falls back to a full fetch.

        A 304 keeps the stored content and the original fetched_at /
        http_status / content_type / hash, and only adopts any rotated
        validators (and, if changed, the final URL). A 200 returns the
        freshly fetched content as a normal new entry.
        """
        if not self._tb.conditional_revalidation:
            return None
        method = entry.get("method")
        if method != "static":
            return None
        meta = entry.get("meta") or {}
        prov = meta.get("provenance") or {}
        etag = prov.get("etag")
        last_modified = prov.get("last_modified")
        if not etag and not last_modified:
            return None
        try:
            (
                not_modified,
                html,
                md,
                links,
                removed,
                prov_meta,
                new_etag,
                new_lm,
            ) = fetch_html_conditional(
                url,
                self._tb.link_cap,
                self._tb.max_response_bytes,
                etag=etag,
                last_modified=last_modified,
            )
        except Exception as e:
            logger.debug("Conditional revalidation error for %s: %s", url, e)
            return None
        if not_modified:
            # 304 Not Modified: content unchanged - keep the stored copy and
            # its original provenance; adopt any rotated validators and, if
            # the hop reported a different URL, the refined final_url.
            fresh_prov = dict(prov)
            if new_etag:
                fresh_prov["etag"] = new_etag
            if new_lm:
                fresh_prov["last_modified"] = new_lm
            final_url = prov_meta[1]
            if final_url and final_url != fresh_prov.get("final_url"):
                fresh_prov["final_url"] = final_url
            fresh_meta = dict(meta)
            fresh_meta["provenance"] = fresh_prov
            return True, (
                entry.get("markdown", ""),
                [tuple(p) for p in entry.get("links", [])],
                fresh_meta,
                method,
            )
        # 200: the server has new content - store it as a fresh fetch.
        metadata = meta_extractor.extract_all(html, url)
        if removed:
            metadata["hidden_blocks_removed"] = removed
        metadata["provenance"] = _provenance_from_fetch_meta(
            prov_meta, url, etag=new_etag, last_modified=new_lm
        )
        return False, (md, links, metadata, "static")


    def _apply_provenance(
        self, result: InspectionResult, metadata: dict, markdown: str
    ) -> None:
        """Tier 1.3: stamp provenance onto an inspection result.

        fetched_at / http_status / final_url / content_type come from the
        fetch metadata — the page cache stores that dict, so a cache hit
        reports the ORIGINAL fetch, not the read time. content_hash covers
        the full untruncated markdown, so every chunked read of one page
        shares the same hash (a citation can tie a slice to its source).
        """
        prov = metadata.get("provenance") or {}
        result.fetched_at = prov.get("fetched_at")
        result.http_status = prov.get("http_status")
        result.final_url = prov.get("final_url")
        result.content_type = prov.get("content_type")
        result.content_hash = _sha256_hex(markdown)


    def _inspect_html_page_impl(
        self,
        url: str,
        use_smart: str = FetchMode.AUTO.value,
        query: Optional[str] = None,
        offset: int = 0,
        max_chunks: int = 1,
        politeness_root: Optional[str] = None,
    ) -> str:
        """Shared implementation behind ``inspect_html_page`` (sync + async).

        Note on retries: the Rust core already retries transient HTTP
        failures 3× with exponential backoff; a Python-layer retry here
        would multiply attempts (3×3) and sleep redundantly, so none is
        applied at this layer.

        Visited-URL semantics (C3): a URL is marked visited only after a
        *successful* fetch, so a transient failure never blacklists it. A
        repeat visit to a URL whose result is still cached is served from
        the cache (``cache_hit: true``) instead of a content-free warning
        — data beats a warning.
        """
        url, url_error = self._tb._prepare_url(url)
        if url_error is not None:
            return json.dumps(url_error, indent=2)

        # Tier 1.4: capture the raw (possibly expired) page entry before
        # the normal cache lookup purges it - an expired entry's ETag /
        # Last-Modified let us revalidate cheaply (304) instead of
        # re-downloading the whole page.
        stale_entry = None
        if self._tb.conditional_revalidation:
            stale_entry = self._stale_page_entry(url)

        cached = self._page_cache_get(url)
        from_cache = cached is not None
        revalidated = False
        if cached is None:
            # S4: robots.txt compliance -- only on the fetch path; a cache
            # hit performs no network fetch, so it is unaffected.
            if self._tb._robots_disallows(url):
                logger.warning("URL disallowed by robots.txt: %s", url)
                return json.dumps(
                    {"warning": "URL disallowed by robots.txt", "url": url},
                    indent=2,
                )
            # S5: claim the URL so a concurrent call for the same page is
            # rejected here instead of double-fetching; C3 semantics are
            # kept because release on failure un-claims it.
            if not self._tb._claim_in_flight(url):
                logger.warning(
                    "URL already visited or in flight: %s", url
                )
                return json.dumps(
                    {"warning": "URL already visited", "url": url}, indent=2
                )
            try:
                # Politeness delay applies only when we will actually fetch
                # (a conditional revalidation counts as a fetch too).
                self._tb._rate_limit_domain(url, politeness_root)
                if stale_entry is not None:
                    outcome = self._revalidate_stale_entry(url, stale_entry)
                    if outcome is not None:
                        revalidated, cached = outcome
                if cached is None:
                    cached = self._fetch_html(url, use_smart)
            except Exception as e:
                self._tb._release_in_flight(url)
                logger.error("HTML inspection failed for %s: %s", url, e)
                return json.dumps(
                    {"error": f"HTML inspection failed: {str(e)}"}, indent=2
                )
            # Release the in-flight claim now that the fetch is done. Visited
            # marking and the page-cache write are deferred to after the §7
            # guard decision so withheld content is neither stored nor marked
            # visited (a 304 re-put still re-freshens the entry there).
            self._tb._release_in_flight(url)

        markdown, links, html_metadata, fetch_method = cached
        logger.info(
            "%s %s via %s (%d chars, %d links)",
            "Cached" if from_cache else "Fetched",
            url,
            fetch_method,
            len(markdown),
            len(links),
        )
        md_chars, md_tokens = self._tb._content_budget()
        # Tier 1.2: paging operates at read time on the full cached
        # markdown, so resuming never re-fetches. Explicit paging
        # (offset > 0 or max_chunks != 1) takes precedence over query-
        # based section selection — the caller asked for exact positions,
        # so honor them. Even the default read (no paging, no query) is
        # chunked, so every payload carries resume metadata
        # (next_offset / has_more / chars_total) and any long page stays
        # continuable.
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        try:
            max_chunks = max(1, int(max_chunks))
        except (TypeError, ValueError):
            max_chunks = 1
        explicit_paging = offset > 0 or max_chunks != 1
        query = (query or "").strip()
        selection = None
        next_offset = None
        has_more = False
        if query and not explicit_paging:
            # Tier 1.1: when a research query is supplied and the page
            # does not fit the budget, keep the query-relevant sections
            # instead of truncating head-first. Selection happens at read
            # time on the full cached markdown, so different queries over
            # the same URL select different sections without re-fetching.
            selection = select_relevant_sections(markdown, query, md_chars)
        if selection is not None:
            # The selection already fits the char budget; _truncate here
            # only enforces the token budget as a backstop (and is a no-op
            # when max_tokens is 0).
            truncated_md = self._tb._truncate(selection.markdown, md_chars, md_tokens)
            markdown_truncated = True
        else:
            # Tier 1.2: slice the full markdown at the requested offset
            # (default: the head-first chunk with resume metadata).
            truncated_md, next_offset, has_more = self._slice_markdown(
                markdown, offset, max_chunks, md_chars, md_tokens
            )
            markdown_truncated = (
                offset > 0 or has_more or truncated_md != markdown
            )

        # Build compact metadata summary for LLM output
        meta_summary = self._compact_metadata(html_metadata)

        # Tier 1.3: provenance is applied inside _build_inspection_result
        # (before the M11 budget loop); the hash covers the full page
        # markdown, not the delivered slice.
        result = self._build_inspection_result(
            url, truncated_md, links, meta_summary, fetch_method,
            markdown_truncated=markdown_truncated,
            html_metadata=html_metadata,
            page_markdown=markdown,
        )
        if from_cache or revalidated:
            result.cache_hit = True
        if revalidated:
            result.revalidated = True
        if selection is not None:
            result.query = query
            result.sections_available = selection.total_sections
            result.sections_selected = selection.selected_count
            result.section_anchors = list(selection.anchors)
        else:
            # Tier 1.2: chunked read — report the slice served so the
            # caller can resume where it stopped.
            result.offset = offset
            result.next_offset = next_offset
            result.has_more = has_more
            result.chars_total = len(markdown)
        if result.guard and result.guard.get("withheld"):
            # Withheld content is neither cached nor marked visited: the
            # request did not complete, so a retry can re-evaluate it.
            return json.dumps(
                {
                    "error": "content withheld by prompt-injection guard",
                    "url": url,
                    "guard": result.guard,
                },
                indent=2,
            )
        if not from_cache:
            # Only now that the guard has decided to deliver the content do
            # we mark it visited (C3) and store it (a 304 re-put re-freshens).
            self._tb._mark_visited(url)
            self._page_cache_put(url, *cached)
        return result.model_dump_json()


    def _slice_markdown(
        self,
        markdown: str,
        offset: int,
        max_chunks: int,
        md_chars: int,
        md_tokens: int,
    ) -> tuple[str, int, bool]:
        """Serve consecutive budget-sized chunks of the full markdown.

        Tier 1.2: each chunk is at most ``md_chars`` and breaks at a
        paragraph boundary (double newline) when one sits in the back
        half of the window, so resumes land between paragraphs rather
        than mid-sentence. Each chunk is token-backstopped by ``_truncate``
        (a no-op when ``max_tokens`` is 0).

        Returns ``(delivered_content, next_offset, has_more)``. ``next_offset``
        is the end of the raw slice — not the delivered length — so a
        token-backstop cut can never cause overlap or marker contamination
        on resume.
        """
        md_chars = max(1, md_chars)  # a zero budget must not stall the loop
        total = len(markdown)
        pos = max(0, min(offset, total))
        parts: list[str] = []
        for _ in range(max(1, max_chunks)):
            if pos >= total:
                break
            window = markdown[pos : pos + md_chars]
            if len(window) < md_chars:
                end = total
            else:
                cut = window.rfind("\n\n")
                # Only break at a paragraph boundary found in the back
                # half of the window; otherwise cut at full width. (The
                # strict comparison also keeps tiny budgets from producing
                # zero-length chunks and stalling the loop.)
                end = pos + (cut if cut > md_chars // 2 else md_chars)
            parts.append(self._tb._truncate(markdown[pos:end], md_chars, md_tokens))
            pos = end
        return "".join(parts), pos, pos < total


    def _compact_metadata(self, raw: dict) -> dict:
        """
        Compact raw meta-oxide output into a lean dict for LLM context.

        Keeps: title, description, canonical, og_title, og_type, og_image,
        twitter_card, twitter_title, jsonld (first 2 objects).
        """
        if not raw:
            return {}

        rel = raw.get("rel_links", {})
        compact: dict = {}

        # (section, source key, target key) triples — copied when truthy.
        field_copies = [
            ("meta", "title", "title"),
            ("meta", "description", "description"),
            ("meta", "canonical", "canonical"),
            ("meta", "language", "language"),
            ("opengraph", "title", "og_title"),
            ("opengraph", "type", "og_type"),
            ("opengraph", "image", "og_image"),
            ("twitter", "card", "twitter_card"),
            ("twitter", "title", "twitter_title"),
        ]
        for section, source_key, target_key in field_copies:
            value = raw.get(section, {}).get(source_key)
            if value:
                compact[target_key] = value

        jsonld = raw.get("jsonld") or []
        if jsonld:
            compact["jsonld"] = jsonld[:2]  # cap at 2 objects

        # A rel-link canonical overrides the plain meta canonical.
        if rel.get("canonical"):
            compact["canonical"] = rel["canonical"][0]
        if rel.get("alternate"):
            compact["alternates"] = rel["alternate"]

        # S2: surface how many hidden nodes were stripped from the
        # main-content fragment before markdown conversion.
        if raw.get("hidden_blocks_removed"):
            compact["hidden_blocks_removed"] = raw["hidden_blocks_removed"]

        return compact


    def batch_inspect_pages_impl(self, urls: list) -> str:
        """
        Fetch multiple pages concurrently using the Rust batch engine.

        Pages already in the page cache are served straight from it (C6);
        only genuinely new URLs reach the fetch engine, and fetched pages
        are stored back into the cache so later single-page inspections
        (and repeated batches) are nearly free. Output is merged back in
        the caller's input order, and every entry has the same shape as an
        ``inspect_html_page`` result (metadata, cache_hit, fetch_method).

        With ``fetch_mode="browser"`` every page is fetched sequentially
        through the stealth browser instead (per-domain rate limits apply).
        """
        # Normalize (C6: single-page inspection normalizes too, so the same
        # page can never occupy two cache/visited entries), then partition
        # into cached vs. uncached. Visited URLs are marked only after
        # success (C3), so failed batch entries remain retryable.
        pending: list[str] = []
        cached_entries: dict[str, tuple] = {}
        rejected: dict[str, dict] = {}
        seen = set()
        for raw in urls:
            # A URL the policy refuses (SSRF, bad scheme, local path) is one
            # bad *entry*, not a failed batch: record it and keep going, so a
            # single poisoned link in a scraped list cannot discard every
            # good result alongside it.
            url, url_error = self._tb._prepare_url(raw)
            if url_error is not None:
                rejected[raw] = url_error
                continue
            if url in seen:
                continue
            seen.add(url)
            cached = self._page_cache_get(url)
            if cached is not None:
                cached_entries[url] = cached
                continue
            if url in self._tb.visited_urls:
                logger.warning("Skipping already-visited URL in batch: %s", url)
                continue
            # S4: robots.txt says no -- skip without claiming so the URL
            # stays available if the site changes its rules.
            if self._tb._robots_disallows(url):
                logger.warning("Skipping robots-disallowed URL in batch: %s", url)
                continue
            if not self._tb._claim_in_flight(url):
                # A concurrent single-page call claimed this URL between
                # the check and the claim (S5); skip it rather than
                # double-fetch.
                logger.warning("URL already in flight: %s", url)
                continue
            pending.append(url)

        try:
            fetched: dict[str, dict] = {}
            if self._tb.fetch_mode == "browser":
                for url in pending:
                    try:
                        # Sequential stealth-browser fetches honor the same
                        # per-domain politeness gap as single fetches.
                        self._tb._rate_limit_domain(url)
                        entry = self._fetch_html(url)
                        self._tb._mark_visited(url)  # success only (C3)
                        self._page_cache_put(url, *entry)  # C6
                        self._tb._release_in_flight(url)  # S5
                        fetched[url] = self._batch_result(
                            url, *entry, cache_hit=False
                        )
                    except Exception as e:
                        self._tb._release_in_flight(url)  # S5: stays retryable
                        fetched[url] = {"url": url, "error": str(e)}
            else:
                results = []
                if pending:
                    try:
                        results = batch_research(
                            pending,
                            max_links=self._tb.link_cap,
                            max_concurrency=self._tb.max_concurrency,
                            # Same-domain staggering inside the batch engine (0 disables).
                            domain_gap_ms=int(self._tb._fetch_interval * 1000),
                            max_bytes=self._tb.max_response_bytes,
                        )
                    except Exception:
                        # The engine call failed wholesale (e.g. every URL
                        # rejected at validation); release every claim so
                        # the URLs remain retryable (S5).
                        for claimed in pending:
                            self._tb._release_in_flight(claimed)
                        raise
                for entry in _normalize_batch_results(results):
                    if entry.ok:
                        # M12: the Rust batch engine returns raw markdown
                        # with relative hrefs; make the body self-contained.
                        md = _absolutize_markdown_links(
                            entry.markdown or "", entry.url
                        )
                        method = "static"
                        links = entry.links
                        # Bugfix 5: run the same meta-oxide extraction the
                        # static single-page path runs, so a batch entry and
                        # a single read of the same URL carry identical
                        # metadata instead of the batch shipping {}.
                        meta = self._tb._extract_html_metadata(entry.html, entry.url)
                        # M17: batch "auto" mirrors inspect_html_page / crawl --
                        # the static Rust engine has no browser fallback, so a
                        # page it couldn't render (empty, binary-garbage, or a
                        # JS-rendered SPA body) is re-fetched through the Python
                        # stealth-browser path, exactly like single-page auto,
                        # and the entry reports which method actually served it.
                        # Only the non-text entries pay the browser cost; the
                        # whole batch stays static-only for fetch_mode="static".
                        if self._tb.fetch_mode == "auto" and not self._looks_like_text(md):
                            try:
                                f_md, f_links, f_meta, _ = self._browser_fetch(
                                    entry.url
                                )
                            except Exception as e:
                                logger.warning(
                                    "Batch browser fallback failed for %s: %s",
                                    entry.url, e,
                                )
                            else:
                                # Re-absolutize to match the static path (the
                                # browser seam returns links absolutized, but
                                # not through the same Python step), and label
                                # it a stealth fallback per the auto strategy.
                                md = _absolutize_markdown_links(f_md, entry.url)
                                links, meta = f_links, f_meta
                                method = "stealth-fallback"
                        self._tb._mark_visited(entry.url)  # success only (C3)
                        # C6: store back into the shared page cache, overwriting
                        # the static entry so a later single read of this URL
                        # returns the method that actually served it.
                        self._page_cache_put(entry.url, md, links, meta, method)
                        self._tb._release_in_flight(entry.url)  # S5
                        fetched[entry.url] = self._batch_result(
                            entry.url, md, links, meta, method, cache_hit=False
                        )
                    else:
                        self._tb._release_in_flight(entry.url)  # S5: stays retryable
                        fetched[entry.url] = {"url": entry.url, "error": entry.error}

            # Merge cached + fetched entries back in input order (C6).
            output = []
            emitted = set()
            for raw in urls:
                if raw in rejected:
                    if raw not in emitted:
                        emitted.add(raw)
                        output.append(rejected[raw])
                    continue
                url = normalize_url(raw)
                if url in emitted:
                    continue
                emitted.add(url)
                if url in cached_entries:
                    md, links, meta, method = cached_entries[url]
                    output.append(
                        self._batch_result(
                            url, md, links, meta, method, cache_hit=True
                        )
                    )
                elif url in fetched:
                    output.append(fetched[url])
            return json.dumps(output, ensure_ascii=False)
        except Exception as e:
            logger.error("Batch inspection failed: %s", e)
            return json.dumps({"error": f"Batch inspection failed: {str(e)}"}, indent=2)


    def _batch_result(
        self,
        url: str,
        md: str,
        links,
        meta: dict,
        method: str,
        cache_hit: bool = False,
    ) -> dict:
        """Build one batch output entry with the same shape as a
        single-page ``inspect_html_page`` result (C6)."""
        md_chars, md_tokens = self._tb._content_budget()
        truncated_md = self._tb._truncate(md, md_chars, md_tokens)
        # Tier 1.3: batch entries carry the same provenance as single
        # page reads (meta comes from the fresh fetch or the page cache),
        # attached before the budget loop.
        result = self._build_inspection_result(
            url, truncated_md, links, self._compact_metadata(meta), method,
            markdown_truncated=truncated_md != md,
            html_metadata=meta,
            page_markdown=md,
        )
        if cache_hit:
            result.cache_hit = True
        return json.loads(result.model_dump_json())
