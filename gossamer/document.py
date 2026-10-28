"""Document extraction + structured HTML inspection for the toolbox.

Phase 4 of the agent_tools.py composition split (god-class
reduction). The document-extraction and HTML-structured-inspection
concern was moved out of the ``WebResearcherToolbox`` facade into
this collaborator. The facade keeps thin delegations
(``extract_document``, ``extract_document_structured``,
``inspect_html_structured``) so the public import surface and tool
dispatch are unchanged.

``DocumentExtractor`` reads all shared toolbox state through
``self._tb`` (the toolbox), mirroring ``SearchService`` /
``FetchService``: a caller that reassigns toolbox attributes is seen
live instead of via a stale captured copy.
"""

import json
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from gossamer._core import extract_tables_from_html
from gossamer.config import FetchMode, normalize_url
from gossamer.guard import evaluate, wrap_untrusted
from gossamer.models import ExtractionResult, _sha256_hex, _utc_now_iso
from gossamer.ssrf import SsrfBlockedError
from gossamer.structured_parser import (
    StructuredOxideParser,
    build_follow_up_candidates,
    office_ir_tables,
    require_office_oxide,
    require_pdf_oxide,
)
from gossamer.resource_store import ResourceStore
from gossamer.token_budget import count_tokens
from gossamer.text_links import extract_links

logger = logging.getLogger(__name__)


def _sanitize_filename(name: str, fallback: str = "document") -> str:
    """Collapse ``name`` to a single safe basename (no separators, no ``..``)."""
    # Split on both separators explicitly: `Path.stem` only honors the
    # platform separator, so backslash names would leak through on POSIX
    # (and vice versa) — stored filenames must not depend on the OS.
    stem = Path(str(name).replace("\\", "/").rsplit("/", 1)[-1]).stem or ""
    stem = stem.replace("/", "_").replace("\\", "_").replace("..", "_").strip()
    stem = "".join(ch if (ch.isalnum() or ch in "-_ ") else "_" for ch in stem)
    stem = stem.strip("._-")
    return stem or fallback


class DocumentExtractor:
    """Document extraction + structured HTML inspection."""

    def __init__(self, tb):
        self._tb = tb

    # ───────────────────────────────
    # Document Extraction
    # ───────────────────────────────

    def _finish_document(self, result: ExtractionResult) -> str:
        """§7: run the guard over document content and serialize the result.

        Applies the guard mode (annotate wrap / redact / block withhold) to
        the delivered content, attaches ``result.guard``, and returns the
        JSON string.
        """
        block, redacted, withheld = evaluate(
            self._tb._guard,
            [("document_text", result.content)],
            main_scope="document_text",
        )
        if block is None:
            return result.model_dump_json()
        result.guard = block
        if withheld:
            return json.dumps(
                {
                    "error": "content withheld by prompt-injection guard",
                    "source": result.source,
                    "guard": block,
                },
                indent=2,
            )
        if block.get("action") == "redact" and redacted is not None:
            result.content = redacted
        elif block.get("action") == "annotate" and redacted:
            result.content = wrap_untrusted(redacted, result.source)
        result.content_tokens = count_tokens(result.content, self._tb.model_name)
        return result.model_dump_json()

    def extract_document(
        self,
        source: str,
        pages: Optional[str] = None,
        structured: bool = False,
        *,
        store: bool = False,
        store_dir: Optional[str] = None,
        include_images: bool = False,
        tables_as: str = "json",
        prefer_jats: bool = True,
    ) -> str:
        """Extract text content from documents.

        Structured: PDF, DOCX, XLSX, PPTX. Text (Tier 3.10): TXT, MD, CSV,
        JSON (pretty-printed), XML, and RSS/Atom feeds (surfaced as readable
        entry lists). Extension-less URLs whose Content-Type is text-like
        (e.g. text/plain) are also delivered as text.

        Parameters
        ----------
        source : str
            URL or local file path.
        pages : str, optional
            1-based inclusive page range: ``"10"``, ``"10-20"``, ``"10-"``
            or ``"-20"``. For PDFs this selects pages; for XLSX it
            selects sheets (the parser's page blocks). Supported for
            formats the structured parser yields per-page data for
            (PDF, XLSX); for other formats the call errors with an
            actionable message. The full document stays cached under its
            own key, so range reads do not evict whole-document reads.
        store : bool
            When true, write two files — the original document bytes
            verbatim (``<stem><ext>``) and the full extracted text as
            markdown (``<stem>.md``) — under ``store_dir`` (default
            ``stored_documents/`` next to the working directory). The full
            (untruncated) extracted text is stored even though the returned
            body is budget-truncated. ``store`` cannot be combined with
            ``pages``.
        store_dir : str, optional
            Directory to write the stored files into. Created if missing.
        include_images : bool
            PDF only; requires ``store=True``. Extract embedded raster
            images (``extract_image_bytes`` per page) into
            ``<stem>.files/`` and append a ``## Figures`` section to the
            stored markdown; the ``stored.resources`` field reports them
            under ``embedded``/``files``. Vector-only figures have no
            raster bytes and are skipped. Tables need no flag: detected
            tables are rendered as markdown tables by default.
        tables_as : str
            Rendering of spreadsheet tables in the returned content:
            ``"json"`` (default for XLSX: one JSON array of
            ``{sheet, headers, rows}`` per table), ``"markdown"`` (pipe
            tables), or ``"csv"`` (comma-separated, one ``## <sheet>``
            block per sheet). Applies to the flat whole-document path for
            XLSX; other formats keep their markdown rendering.
        prefer_jats : bool
            JATS-first scholarly route (default True): DOI-resolvable
            sources attempt publisher full-text XML (EuropePMC / PMC OA /
            PLOS) via ``bobine.convert_jats`` before the PDF path.
            ``structured=True`` or ``tables_as != 'markdown'`` skips the
            route; ``pages=`` with a JATS-resolvable source errors
            (sections, not pages). ``store=True`` writes ``<stem>.xml``
            + ``<stem>.md``; ``include_images=True`` is a documented
            no-op for JATS (remote hrefs, not saved).
        """
        if tables_as not in ("markdown", "csv", "json"):
            return json.dumps(
                {"error": "tables_as must be 'markdown', 'csv' or 'json'"},
                indent=2,
            )
        if include_images and not store:
            return json.dumps(
                {
                    "error": (
                        "include_images=True requires store=True; images "
                        "need a directory to live in"
                    )
                },
                indent=2,
            )
        if include_images and structured:
            return json.dumps(
                {
                    "error": (
                        "include_images cannot be combined with "
                        "structured=True; use store=True instead"
                    )
                },
                indent=2,
            )
        if structured:
            return self._extract_document_structured_impl(source)
        # JATS-first: pages= with a JATS-resolvable source errors (§3.5).
        # Offline check only (no network) so non-DOI page reads stay cheap.
        if prefer_jats and self._jats_tables_allows(source, tables_as) and pages is not None and str(pages).strip():
            if self._jats_resolvable_offline(source):
                from gossamer.jats import PAGES_JATS_ERROR

                return json.dumps(
                    {"error": PAGES_JATS_ERROR.format(pages=str(pages).strip())},
                    indent=2,
                )
        # Bare-DOI fast path: not a URL/local file, so skip URL gating
        # (normalize_url would mangle it; 10.x looks like RFC1918 to SSRF).
        if prefer_jats and self._jats_tables_allows(source, tables_as) and (pages is None or not str(pages).strip()):
            try:
                _is_file = Path(source).is_file()
            except (OSError, ValueError):
                _is_file = False
            if not _is_file:
                from gossamer import jats as _jats_mod

                if _jats_mod.doi_from_source_string(source) and not source.strip().lower().startswith("http"):
                    hit = self._try_jats_route(
                        source, store=store, store_dir=store_dir,
                        include_images=include_images,
                    )
                    if hit is not None:
                        return hit
        try:
            source = normalize_url(source)  # may still be a local path
            is_url = True
        except ValueError:
            is_url = urlparse(source).scheme in ("http", "https")

        if is_url:
            try:
                self._tb._validate_url(source)
            except (ValueError, SsrfBlockedError) as e:
                return json.dumps(self._tb._url_error(source, e), indent=2)
            # S4: robots gate before rate-limiting, so a disallowed fetch
            # does not burn a politeness delay.
            if self._tb._robots_disallows(source):
                return json.dumps(
                    {
                        "error": (
                            f"Document fetch disallowed by robots.txt: {source}"
                        )
                    },
                    indent=2,
                )
            self._tb._rate_limit_domain(source)

        # JATS-first route (§3.1): after URL gating, before pages/cache.
        if prefer_jats and self._jats_tables_allows(source, tables_as) and (pages is None or not str(pages).strip()):
            hit = self._try_jats_route(
                source, store=store, store_dir=store_dir,
                include_images=include_images,
            )
            if hit is not None:
                return hit

        # Tier 1.2: explicit page-range reads go through the structured
        # parser (the only path with per-page structure) and are cached
        # under a range-specific key.
        if pages is not None and str(pages).strip():
            if store:
                return json.dumps(
                    {
                        "error": (
                            "store=True cannot be combined with pages=...; "
                            "store the full document instead"
                        )
                    },
                    indent=2,
                )
            return self._extract_document_pages(source, str(pages).strip(), is_url)

        cache_key = self._tb._cache_key(source) if is_url else source
        if tables_as != "markdown":
            # The cache stores the rendered content; non-legacy renderings
            # must not collide with a cached markdown read of the source.
            cache_key = f"{cache_key}#tables={tables_as}"
        raw_bytes: Optional[bytes] = None
        prov: dict = {}
        cached = self._tb.cache.get(cache_key)

        if cached is not None:
            # Re-apply the budget on every read (C4): the cache holds
            # untruncated content and the budget may have changed since the
            # entry was stored — same read-time truncation as the page cache.
            full_content = cached
            cache_hit = True
            # Tier 1.3: the stored content carries no fetch timestamp, so
            # fetched_at stays None; the hash still ties the read back to
            # the stored bytes.
        else:
            try:
                if is_url:
                    if store:
                        content, prov, raw_bytes = self._download_and_extract(
                            source, tables_as=tables_as, with_bytes=True
                        )
                    else:
                        content, prov = self._download_and_extract(
                            source, tables_as=tables_as
                        )
                    full_content = content
                else:
                    full_content = self._extract_local(source, tables_as=tables_as)
                    prov = {"fetched_at": _utc_now_iso()}
            except Exception as e:
                logger.error("Document extraction failed for %s: %s", source, e)
                return json.dumps(
                    {"error": f"Document extraction failed: {str(e)}"}, indent=2
                )
            self._tb.cache.put(cache_key, full_content)
            cache_hit = False

        # Store the original document + full extracted markdown when asked.
        stored = None
        if store:
            if raw_bytes is None:
                # Cache hit: refetch fresh bytes for storage.
                if is_url:
                    raw_bytes, store_prov = self._fetch_document_url(source)
                else:
                    raw_bytes = Path(source).read_bytes()
                    store_prov = {"fetched_at": _utc_now_iso()}
            else:
                store_prov = prov
            try:
                stored = self._store_document(
                    source,
                    raw_bytes,
                    full_content,
                    is_url,
                    store_dir,
                    store_prov,
                    include_images=include_images,
                )
            except ValueError as e:
                return json.dumps({"error": str(e)}, indent=2)

        truncated = self._tb._budget._truncate(
            full_content, self._tb.max_markdown_chars, self._tb.max_tokens
        )
        result = ExtractionResult(
            source=source,
            content=truncated,
            content_tokens=count_tokens(truncated, self._tb.model_name),
            cache_hit=cache_hit,
            # Hash ties the read back to the full untruncated content.
            content_hash=_sha256_hex(full_content),
            # Link detection runs on the full content, not the truncated
            # delivery — a budget cut must never lose links.
            links=extract_links(full_content),
            **prov,
        )
        if stored is not None:
            result.stored = stored
        return self._finish_document(result)

    # ───────────────────────────────
    # JATS-first scholarly route (bobine proposal §5)
    # ───────────────────────────────

    _JATS_XML_SUFFIXES = (".xml", ".jats", ".nxml")
    # Spreadsheet suffixes for the tables_as gate (§3.5): JATS tables are
    # already GFM pipes, so an explicit tables_as='json'/'csv' only skips
    # the route when the source looks like a spreadsheet (the request the
    # PDF path can actually serve as JSON/CSV). For scholarly sources
    # (DOI/PDF/XML) tables_as is ignored by the PDF path anyway, so the
    # JATS route still applies with default flags (default-on).
    _JATS_SPREADSHEET_SUFFIXES = (".xlsx", ".xls", ".xlsb", ".csv")

    def _jats_tables_allows(self, source: str, tables_as: str) -> bool:
        if tables_as == "markdown":
            return True
        try:
            suffix = Path(source).suffix.lower()
        except (OSError, ValueError):
            suffix = ""
        if not suffix:
            try:
                suffix = Path(urlparse(source).path).suffix.lower()
            except Exception:  # noqa: BLE001 - seatbelt
                suffix = ""
        return suffix not in self._JATS_SPREADSHEET_SUFFIXES

    def _jats_resolvable_offline(self, source: str) -> bool:
        """Offline resolvability check (no network, no file read beyond DOI scan)."""
        from gossamer import jats as _jats_mod

        try:
            if Path(source).is_file():
                suffix = Path(source).suffix.lower()
                if suffix in self._JATS_XML_SUFFIXES:
                    return True
                if suffix == ".pdf":
                    try:
                        doi, _ = _jats_mod.doi_from_pdf_path(source)
                    except Exception:  # noqa: BLE001 - best effort
                        return False
                    return bool(doi)
                return False
        except (OSError, ValueError):
            pass
        return _jats_mod.doi_from_source_string(source) is not None

    def _fetch_jats_url(self, url: str):
        """Fetch a resolver/JATS URL through the toolbox machinery.

        Returns ``(bytes, prov)``. Raises ``ValueError`` for SSRF/robots
        rejections (callers treat those as tier-skips, never hard errors).
        Network/fetch failures propagate to the caller, which decides
        loud (JSON error) vs tier-skip per §3.1/§3.3/§3.5.
        """
        try:
            self._tb._validate_url(url)
        except (ValueError, SsrfBlockedError) as e:
            raise ValueError(f"URL rejected: {e}") from e
        if self._tb._robots_disallows(url):
            raise ValueError(f"disallowed by robots.txt: {url}")
        self._tb._rate_limit_domain(url)
        return self._fetch_document_url(url)

    def _jats_cache_key(self, source: str) -> str:
        try:
            if urlparse(source).scheme in ("http", "https"):
                base = self._tb._cache_key(source)
            else:
                base = source
        except Exception:  # noqa: BLE001 - seatbelt
            base = source
        return f"{base}#jats"

    def _try_jats_route(
        self, source: str, *, store: bool = False,
        store_dir: Optional[str] = None, include_images: bool = False,
    ) -> Optional[str]:
        """JATS-first attempt; ``None`` = not JATS-resolvable (fall through).

        Returns the finished JSON string on success *or* hard error
        (post-resolution network failure, XML parse); ``None`` only when
        the source is not JATS-resolvable and the caller must fall
        through silently to the PDF path.
        """
        from gossamer import jats as J

        # Local .xml/.jats/.nxml → direct convert (feeds keep working:
        # "not a JATS article" falls through, never errors).
        try:
            _is_file = Path(source).is_file()
        except (OSError, ValueError):
            _is_file = False
        if _is_file and Path(source).suffix.lower() in self._JATS_XML_SUFFIXES:
            try:
                xml_bytes = Path(source).read_bytes()
            except OSError as e:
                return json.dumps(
                    {"error": f"Document extraction failed: {e}"}, indent=2
                )
            try:
                conv = J.convert_jats_bytes(xml_bytes, Path(source).name)
            except (ImportError, RuntimeError):
                # Local .xml/.jats/.nxml keeps its existing contract (feed
                # entries / raw-text fallback): any conversion failure —
                # missing bobine extra, not-a-JATS feed, malformed XML —
                # falls through to the normal path, never errors.
                return None
            return self._finish_jats_result(
                source, conv, None, None, None, None,
                store=store, store_dir=store_dir,
                include_images=include_images, xml_bytes=xml_bytes,
            )

        # DOI acquisition (reuse, don't reinvent).
        resolved_doi: Optional[str] = None
        if _is_file:
            if Path(source).suffix.lower() == ".pdf":
                try:
                    doi, _ = J.doi_from_pdf_path(source)
                except Exception:  # noqa: BLE001 - best effort
                    doi = None
                if not doi:
                    return None
                resolved_doi = doi
            else:
                return None
        else:
            resolved_doi = J.doi_from_source_string(source)
            if not resolved_doi:
                return None

        # Cache: untruncated markdown + provenance envelope (JATS needs
        # jats_source on hits; the PDF path's provenance loss would read
        # as "PDF served it").
        cache_key = self._jats_cache_key(source)
        cached = self._tb.cache.get(cache_key)
        if cached is not None:
            try:
                import json as _json

                envelope = _json.loads(cached)
                full_content = envelope["markdown"]
                prov = dict(envelope.get("prov") or {})
            except Exception:  # noqa: BLE001 - legacy raw-markdown entry
                full_content = cached
                prov = {"resolved_doi": resolved_doi, "jats_source": "cached"}
            truncated = self._tb._budget._truncate(
                full_content, self._tb.max_markdown_chars, self._tb.max_tokens
            )
            result = ExtractionResult(
                source=source,
                content=truncated,
                content_tokens=count_tokens(truncated, self._tb.model_name),
                cache_hit=True,
                content_hash=_sha256_hex(full_content),
                links=extract_links(full_content),
                **{k: v for k, v in prov.items() if k in ExtractionResult.model_fields},
            )
            return self._finish_document(result)

        # Tier 1: EuropePMC fullTextXML (primary).
        pmcid: Optional[str] = None
        try:
            email = self._openalex_email()
        except Exception:  # noqa: BLE001 - defensive
            email = ""
        import json as _json
        from urllib.parse import quote as _quote

        search_url = (
            f"{J.EUROPEPMC_SEARCH_URL}?query=DOI:{_quote(resolved_doi, safe='')}"
            f"&format=json" + (f"&email={_quote(email)}" if email else "")
        )
        try:
            search_bytes, _ = self._fetch_jats_url(search_url)
            payload = _json.loads(search_bytes.decode("utf-8", errors="replace"))
            picked = J.parse_europepmc_search(payload)
        except ValueError:
            picked = None  # SSRF/robots on the resolver → tier-skip
        except Exception as e:  # noqa: BLE001 - search failure → PLOS/None
            logger.debug("EuropePMC search failed for %s: %s", resolved_doi, e)
            picked = None
        if picked is not None:
            pmcid = picked["pmcid"]
            ft_url = J.europepmc_fulltext_url(picked["source"], picked["id"])
            try:
                xml_bytes, ft_prov = self._fetch_jats_url(ft_url)
            except ValueError:
                xml_bytes, ft_prov = None, None  # SSRF/robots → tier-skip
            except Exception as e:
                logger.error("JATS fetch failed for %s: %s", ft_url, e)
                return json.dumps(
                    {"error": f"Document extraction failed: {e}"}, indent=2
                )
            if xml_bytes is not None and ft_prov is not None:
                if J.is_xml_body(ft_prov.get("content_type"), xml_bytes):
                    try:
                        conv = J.convert_jats_bytes(xml_bytes, f"{pmcid}.xml")
                    except ImportError as e:
                        return json.dumps({"error": str(e)}, indent=2)
                    except RuntimeError as e:
                        if "not a JATS article" in str(e):
                            pass  # try next tier
                        else:
                            logger.error("JATS conversion failed for %s: %s", ft_url, e)
                            return json.dumps(
                                {"error": f"Document extraction failed: {e}"}, indent=2
                            )
                    else:
                        return self._finish_jats_result(
                            source, conv, resolved_doi, "europepmc", ft_url, ft_prov,
                            store=store, store_dir=store_dir,
                            include_images=include_images, xml_bytes=xml_bytes,
                            cache_key=cache_key,
                        )
                # Fetched but not XML → "no full text": fall to PMC OA.
                if pmcid is not None:
                    oa_hit = self._try_pmc_oa(
                        source, resolved_doi, pmcid, store=store,
                        store_dir=store_dir, include_images=include_images,
                        cache_key=cache_key,
                    )
                    if oa_hit is not None:
                        return oa_hit
                plos_hit = self._try_plos(
                    source, resolved_doi, store=store, store_dir=store_dir,
                    include_images=include_images, cache_key=cache_key,
                )
                if plos_hit is not None:
                    return plos_hit
                return None
            # SSRF/robots skip on the fullTextXML URL → try OA, then PLOS.
            if pmcid is not None:
                oa_hit = self._try_pmc_oa(
                    source, resolved_doi, pmcid, store=store,
                    store_dir=store_dir, include_images=include_images,
                    cache_key=cache_key,
                )
                if oa_hit is not None:
                    return oa_hit
            plos_hit = self._try_plos(
                source, resolved_doi, store=store, store_dir=store_dir,
                include_images=include_images, cache_key=cache_key,
            )
            if plos_hit is not None:
                return plos_hit
            return None
        # No EuropePMC hit (or search failed): PLOS last resort, else fall through.
        plos_hit = self._try_plos(
            source, resolved_doi, store=store, store_dir=store_dir,
            include_images=include_images, cache_key=cache_key,
        )
        if plos_hit is not None:
            return plos_hit
        return None

    def _try_pmc_oa(
        self, source: str, doi: str, pmcid: str, *, store: bool = False,
        store_dir: Optional[str] = None, include_images: bool = False,
        cache_key: Optional[str] = None,
    ) -> Optional[str]:
        """PMC OA fallback; ``None`` = no OA full text (try PLOS/fall through)."""
        from gossamer import jats as J
        from urllib.parse import quote as _quote

        oa_url = f"{J.PMC_OA_URL}?id={_quote(pmcid, safe='')}"
        try:
            oa_bytes, _ = self._fetch_jats_url(oa_url)
        except ValueError:
            return None  # SSRF/robots → tier-skip
        except Exception as e:  # noqa: BLE001 - OA index failure → tier-skip
            logger.debug("PMC OA lookup failed for %s: %s", pmcid, e)
            return None
        tgz_url = J.parse_oa_tgz_link(oa_bytes)
        if not tgz_url:
            return None
        try:
            tgz_bytes, tgz_prov = self._fetch_jats_url(tgz_url)
        except ValueError:
            return None
        except Exception as e:
            logger.error("JATS fetch failed for %s: %s", tgz_url, e)
            return json.dumps(
                {"error": f"Document extraction failed: {e}"}, indent=2
            )
        try:
            _member, xml_bytes = J.extract_nxml_from_tgz(tgz_bytes, pmcid)
        except ValueError as e:
            logger.debug("PMC OA tarball unusable for %s: %s", pmcid, e)
            return None
        if not J.is_xml_body("application/xml", xml_bytes):
            return None
        try:
            conv = J.convert_jats_bytes(xml_bytes, f"{pmcid}.nxml")
        except ImportError as e:
            return json.dumps({"error": str(e)}, indent=2)
        except RuntimeError as e:
            if "not a JATS article" in str(e):
                return None
            logger.error("JATS conversion failed for %s: %s", tgz_url, e)
            return json.dumps(
                {"error": f"Document extraction failed: {e}"}, indent=2
            )
        return self._finish_jats_result(
            source, conv, doi, "pmc-oa", tgz_url, tgz_prov,
            store=store, store_dir=store_dir,
            include_images=include_images, xml_bytes=xml_bytes,
            cache_key=cache_key,
        )

    def _try_plos(
        self, source: str, doi: str, *, store: bool = False,
        store_dir: Optional[str] = None, include_images: bool = False,
        cache_key: Optional[str] = None,
    ) -> Optional[str]:
        """PLOS manuscript fallback; ``None`` on any non-200 (never an error)."""
        from gossamer import jats as J
        from urllib.parse import quote as _quote

        journal: Optional[str] = None
        try:
            located = self._tb._oa_locator.locate(doi)
            for cand in located.get("candidates") or []:
                for key in ("landing_page_url", "url"):
                    val = cand.get(key)
                    if isinstance(val, str):
                        journal = J.plos_journal_from_url(val)
                        if journal:
                            break
                    if journal:
                        break
                if journal:
                    break
        except Exception:  # noqa: BLE001 - locator failure → skip PLOS
            return None
        if not journal:
            return None
        plos_url = J.PLOS_FILE_TMPL.format(journal=journal, doi=_quote(doi, safe=""))
        try:
            xml_bytes, plos_prov = self._fetch_jats_url(plos_url)
        except Exception:  # noqa: BLE001 - any non-200 → None, never error
            return None
        if not J.is_xml_body(plos_prov.get("content_type"), xml_bytes):
            return None
        try:
            conv = J.convert_jats_bytes(xml_bytes, f"{doi.split('/')[-1]}.xml")
        except (ImportError, RuntimeError):
            return None  # PLOS branch is best-effort only
        return self._finish_jats_result(
            source, conv, doi, "plos", plos_url, plos_prov,
            store=store, store_dir=store_dir,
            include_images=include_images, xml_bytes=xml_bytes,
            cache_key=cache_key,
        )

    def _openalex_email(self) -> str:
        """Operator contact for the EuropePMC politeness param."""
        try:
            from gossamer.env import getenv as _env_get

            email = _env_get("GOSSAMER_OPENALEX_EMAIL", "")
            return email.strip() if isinstance(email, str) else ""
        except Exception:  # noqa: BLE001 - defensive
            return ""

    def _finish_jats_result(
        self, source: str, conv: dict, doi: Optional[str],
        jats_source: Optional[str], jats_url: Optional[str], prov: Optional[dict],
        *, store: bool = False, store_dir: Optional[str] = None,
        include_images: bool = False, xml_bytes: Optional[bytes] = None,
        cache_key: Optional[str] = None,
    ) -> str:
        """Build, optionally store + cache, and guard-finish a JATS hit."""
        full_content = conv.get("markdown") or ""
        if not full_content.strip():
            return json.dumps(
                {"error": "Document extraction failed: empty JATS conversion"},
                indent=2,
            )
        full_prov: dict = dict(prov or {})
        if jats_url:
            full_prov.setdefault("final_url", jats_url)
        if not full_prov.get("fetched_at"):
            full_prov["fetched_at"] = _utc_now_iso()
        stored = None
        if store:
            try:
                stored = self._store_jats_document(
                    source, doi, xml_bytes or b"", full_content,
                    store_dir, full_prov, include_images=include_images,
                )
            except ValueError as e:
                return json.dumps({"error": str(e)}, indent=2)
        if cache_key is None:
            cache_key = self._jats_cache_key(source)
        try:
            import json as _json

            self._tb.cache.put(cache_key, _json.dumps({
                "markdown": full_content,
                "prov": {
                    "resolved_doi": doi,
                    "jats_source": jats_source or "local",
                    "jats_title": conv.get("title"),
                    "n_formulas": conv.get("n_formulas"),
                    "n_figures": conv.get("n_figures"),
                    "n_tables": conv.get("n_tables"),
                    "final_url": full_prov.get("final_url"),
                    "content_type": full_prov.get("content_type"),
                    "http_status": full_prov.get("http_status"),
                    "fetched_at": full_prov.get("fetched_at"),
                },
            })) 
        except Exception:  # noqa: BLE001 - cache failures never fail reads
            logger.debug("JATS cache write failed", exc_info=True)
        truncated = self._tb._budget._truncate(
            full_content, self._tb.max_markdown_chars, self._tb.max_tokens
        )
        result = ExtractionResult(
            source=source,
            content=truncated,
            content_tokens=count_tokens(truncated, self._tb.model_name),
            cache_hit=False,
            content_hash=_sha256_hex(full_content),
            links=extract_links(full_content),
            resolved_doi=doi,
            jats_source=jats_source or "local",
            jats_title=conv.get("title") or None,
            n_formulas=conv.get("n_formulas"),
            n_figures=conv.get("n_figures"),
            n_tables=conv.get("n_tables"),
            **{k: v for k, v in full_prov.items() if k in ExtractionResult.model_fields},
        )
        if stored is not None:
            result.stored = stored
        return self._finish_document(result)

    def _store_jats_document(
        self, source: str, doi: Optional[str], xml_bytes: bytes,
        full_text: str, store_dir: Optional[str], prov: Optional[dict],
        include_images: bool = False,
    ) -> dict:
        """Store fetched JATS XML verbatim + converted markdown.

        ``include_images`` is a documented no-op (figures are remote
        hrefs, nothing embedded to save) reported in the manifest.
        Remote figures are never downloaded (license + size + SSRF).
        """
        from gossamer.jats import jats_cache_stem

        out_dir = Path(store_dir) if store_dir else Path.cwd() / "stored_documents"
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = jats_cache_stem(doi or source)
        orig_path = out_dir / f"{stem}.xml"
        md_path = out_dir / f"{stem}.md"
        orig_path.write_bytes(xml_bytes)
        md_path.write_text(full_text, encoding="utf-8")
        resources: dict = {"dir": str(out_dir), "files": [], "referenced": 0, "embedded": 0}
        if include_images:
            resources["note"] = "JATS figures are remote hrefs, not saved"
        return {
            "directory": str(out_dir),
            "original": str(orig_path),
            "markdown": str(md_path),
            "original_bytes": len(xml_bytes),
            "markdown_chars": len(full_text),
            "content_type": (prov or {}).get("content_type"),
            "resources": resources,
        }

    # Cap on embedded PDF images per store call (disk-fill guard;
    # reported via the manifest when hit).
    _MAX_EMBEDDED_IMAGES = 200

    def _store_document(
        self,
        source,
        raw_bytes,
        full_text,
        is_url,
        store_dir,
        prov=None,
        include_images=False,
    ):
        """Write the original document bytes + extracted markdown to disk.

        ``raw_bytes`` is the untouched document (stored verbatim); ``full_text``
        is the full, untruncated extracted text written as markdown. Returns a
        dict with the written paths and sizes.
        """
        out_dir = Path(store_dir) if store_dir else Path.cwd() / "stored_documents"
        out_dir.mkdir(parents=True, exist_ok=True)

        prov = prov or {}
        served = prov.get("final_url") or source
        orig_name = (
            Path(urlparse(served).path).name if is_url else Path(source).name
        )
        suffix = Path(orig_name).suffix
        if suffix.lower() in self._DOC_TEXT_SUFFIXES:
            # A real document/text extension is present; Path.stem already
            # stripped exactly it, so "1707.06376v1.pdf" -> stem
            # "1707.06376v1" (the id is preserved).
            stem = Path(orig_name).stem or _sanitize_filename(source)
        elif is_url:
            # A URL with no real extension: either extensionless, or an
            # arXiv-style version fragment ("/pdf/1707.06376v1" ->
            # ".06376v1", which Python wrongly treats as an extension).
            # Keep the whole name as the stem and derive the extension from
            # the Content-Type, so the id is preserved verbatim ->
            # "1707.06376v1.pdf". "pdf" is checked first because some
            # servers still emit ``application/pdf; charset=binary``.
            stem = orig_name or _sanitize_filename(source)
            ct = (prov.get("content_type") or "").lower()
            fmt = self._CONTENT_TYPE_FORMAT.get(ct.split(";", 1)[0].strip().lower())
            suffix = f".{fmt}" if fmt else ".bin"
        else:
            # A local file with a non-document extension (e.g. "image.png")
            # or extensionless: honor the original suffix; extensionless
            # falls back to ".bin".
            stem = Path(orig_name).stem or _sanitize_filename(source)
            suffix = suffix or ".bin"

        if include_images and suffix.lower() != ".pdf":
            raise ValueError(
                f"include_images is only supported for PDF documents "
                f"(got {suffix or 'no extension'}) — store without "
                f"the flag instead"
            )

        orig_path = out_dir / f"{stem}{suffix}"
        md_path = out_dir / f"{stem}.md"

        if raw_bytes is not None:
            orig_path.write_bytes(raw_bytes)
        md_path.write_text(full_text, encoding="utf-8")

        embedded = (
            self._pdf_embedded_images(raw_bytes)
            if include_images and raw_bytes is not None
            else []
        )
        resources = self._store_resources(
            served,
            out_dir,
            stem,
            full_text,
            raw_bytes,
            suffix,
            embedded_images=embedded,
        )

        return {
            "directory": str(out_dir),
            "original": str(orig_path),
            "markdown": str(md_path),
            "original_bytes": len(raw_bytes) if raw_bytes is not None else 0,
            "markdown_chars": len(full_text),
            "content_type": prov.get("content_type"),
            "resources": resources,
        }

    def _pdf_embedded_images(self, raw_bytes: bytes) -> list:
        """Collect embedded raster images from PDF bytes for storage.

        One ``extract_image_bytes`` call per page; each hit becomes an
        ``extract_embedded``-shaped dict (``data``/``filename``/``ext``)
        with page-aware names (``page{i}_{j}.png``). Vector-only
        figures have no raster bytes and are skipped. Best-effort per
        page; capped at ``_MAX_EMBEDDED_IMAGES``.
        """
        images: list = []
        try:
            doc = require_pdf_oxide().from_bytes(raw_bytes)
            n_pages = len(doc)
        except Exception as e:  # noqa: BLE001 - best-effort
            logger.warning("PDF image scan failed: %s", e)
            return []
        for i in range(n_pages):
            try:
                blobs = doc.extract_image_bytes(i) or []
            except Exception:  # noqa: BLE001 - best-effort per page
                continue
            for j, blob in enumerate(blobs, start=1):
                if not isinstance(blob, dict) or not blob.get("data"):
                    continue
                fmt = str(blob.get("format") or "").lower()
                ext = {"png": "png", "jpeg": "jpg", "jpg": "jpg"}.get(
                    fmt, fmt or "img"
                )
                images.append(
                    {
                        "data": blob["data"],
                        "filename": f"page{i + 1}_{j}.{ext}",
                        "ext": ext,
                    }
                )
                if len(images) >= self._MAX_EMBEDDED_IMAGES:
                    logger.warning(
                        "PDF image cap reached (%d); rest skipped",
                        self._MAX_EMBEDDED_IMAGES,
                    )
                    return images
        return images

    def _store_resources(
        self, served, out_dir, stem, full_text, raw_bytes, suffix,
        embedded_images=None,
    ) -> dict:
        """Extract images referenced in the stored content into
        ``<stem>.files/`` and rewrite ``<stem>.md`` to point at them.

        HTML pages keep ``![alt](url)`` image refs (already absolute after
        inspection), so they download here and the markdown is rewritten to
        local paths. Text/office documents whose converter kept image refs
        are handled the same way.

        ``embedded_images`` (``[{data, filename?, ext?}]``) covers the
        other case: converters that dropped the images (PDF raster
        figures via ``_pdf_embedded_images``). They are written alongside
        and linked from an appended ``## Figures`` section.

        Best-effort: never raises, so a resource download failure cannot
        fail the whole store.
        """
        try:
            store = ResourceStore(headers=self._tb._next_headers())
            manifest = store.extract(
                markdown=full_text, base_url=served, out_dir=out_dir, stem=stem,
            )
            md_text = manifest["markdown"]
            files = list(manifest["files"])
            embedded = 0
            if embedded_images:
                emb = store.extract_embedded(
                    markdown=md_text,
                    out_dir=out_dir,
                    stem=stem,
                    images=embedded_images,
                )
                md_text = emb["markdown"]
                files = sorted(set(files) | set(emb["files"]))
                embedded = emb["embedded"]
            if manifest["referenced"] or embedded:
                (Path(out_dir) / f"{stem}.md").write_text(
                    md_text, encoding="utf-8"
                )
            return {
                "dir": manifest["dir"],
                "files": files,
                "referenced": manifest["referenced"],
                "embedded": embedded,
            }
        except Exception as e:  # noqa: BLE001 - resources are best-effort
            logger.warning("ResourceStore failed for %s: %s", served, e)
            return {"dir": None, "files": [], "referenced": 0,
                    "embedded": 0}

    def _fetch_document_url(self, url: str) -> tuple[bytes, dict]:
        """Tier 1.3: download a document URL; return (bytes, provenance).

        Redirects are followed so provenance.final_url is the URL that
        actually served the bytes — final_url != url tells the model the
        content moved.

        S3: the body is streamed under ``max_response_bytes`` (Content-Length
        early-reject + running chunk cap, mirroring the Rust page-fetch
        path) — an LLM-supplied URL must not be able to exhaust process
        memory (review A.2).
        """
        cap = max(1, int(self._tb.max_response_bytes))
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            with client.stream(
                "GET", url, headers=self._tb._next_headers()
            ) as response:
                response.raise_for_status()
                declared_n = None
                try:
                    if response.headers.get("content-length") is not None:
                        declared_n = int(response.headers["content-length"])
                except (KeyError, TypeError, ValueError):
                    declared_n = None  # unparseable: fall through to chunk cap
                if declared_n is not None and declared_n > cap:
                    raise ValueError(
                        f"Document too large: declared {declared_n} bytes "
                        f"(cap {cap})"
                    )
                prov = {
                    "fetched_at": _utc_now_iso(),
                    "http_status": response.status_code,
                    "final_url": str(response.url),
                    "content_type": response.headers.get("content-type"),
                }
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > cap:
                        raise ValueError(
                            f"Document too large: exceeds {cap} bytes"
                        )
                    chunks.append(chunk)
        return b"".join(chunks), prov

    def _download_and_extract(
        self, url: str, *, with_bytes: bool = False, tables_as: str = "json"
    ) -> tuple:
        """Download a document from URL; return (content, prov[, raw_bytes]).

        Tier 3.10: when the URL carries no usable extension (or an
        unrecognized one) but the server says the body is text-like
        (Content-Type: text/plain, application/json, ...), the bytes are
        extracted as text instead of raising. When ``with_bytes`` is true
        the original document bytes are returned alongside the extracted
        text so a ``store`` call can write the verbatim original.
        """
        data, prov = self._fetch_document_url(url)
        try:
            content = self._extract_from_bytes(data, url, tables_as=tables_as)
        except ValueError:
            ct = (prov.get("content_type") or "").split(";", 1)[0].strip().lower()
            # Binary document (PDF/OOXML) at an extensionless URL -- arXiv's
            # /pdf/<id> is the common case -- routes by Content-Type.
            doc_fmt = self._CONTENT_TYPE_FORMAT.get(ct)
            if doc_fmt is not None:
                content = self._parse_document_bytes(data, doc_fmt, tables_as=tables_as)
            else:
                kind = self._TEXT_LIKE_CONTENT_TYPES.get(ct)
                if kind is None:
                    raise
                if kind == "json":
                    content = self._extract_json_text(data)
                elif kind == "xml":
                    content = self._extract_xml_feed(data)
                else:
                    content = data.decode("utf-8-sig", errors="replace")
        if with_bytes:
            return content, prov, data
        return content, prov

    def _parse_document_bytes(self, data: bytes, fmt: str, tables_as: str = "json") -> str:
        """Parse document bytes dispatched by canonical format name.

        Used when a URL gives no usable extension and the response
        Content-Type decides the format (e.g. arXiv's ``application/pdf``),
        so ``extract_document`` works on extensionless document URLs. This
        is the binary-document complement to the text-like fallback in
        ``_download_and_extract``.
        """
        if fmt == "pdf":
            return require_pdf_oxide().from_bytes(data).to_markdown_all()
        if fmt in ("docx", "xlsx", "pptx"):
            if fmt == "xlsx" and tables_as in ("csv", "json"):
                return (
                    self._spreadsheet_csv(data)
                    if tables_as == "csv"
                    else self._spreadsheet_json(data)
                )
            return require_office_oxide().from_bytes(data, fmt).to_markdown()
        # Known-but-unsupported legacy office formats: actionable error.
        suffix = {
            "doc": ".doc", "xls": ".xls", "ppt": ".ppt", "xlsb": ".xlsb",
        }.get(fmt, f".{fmt}")
        hint = self._UNSUPPORTED_FORMAT_HINTS.get(suffix)
        if hint:
            raise ValueError(f"Unsupported document format: {suffix} ({hint}).")
        raise ValueError(f"Unsupported document format: {suffix}")

    # Tier 1.2: page-range reads. The flat extractor joins all pages into
    # one string, so a range can only be served by re-deriving the
    # per-page structure via StructuredOxideParser (Rust, fast). Formats
    # without per-page structure (DOCX/PPTX parse as one page, text
    # formats have no pages) are refused with an actionable message.
    _PAGED_EXTRACT_SUFFIXES = (".pdf", ".xlsx")

    # Extensions authoritative for a stored original's name. An extension
    # outside this set (e.g. arXiv's /pdf/<id> -> ".06954v2") is treated as
    # a version fragment and resolved from the Content-Type instead.
    _DOC_TEXT_SUFFIXES = (
        ".pdf", ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt",
        ".xlsb", ".csv", ".txt", ".md", ".json", ".xml", ".rss", ".atom",
    )

    # Content-Type (sans parameters) -> canonical document format. Lets an
    # extensionless document URL -- arXiv serves /pdf/<id> as
    # application/pdf with no extension -- resolve to the right parser.
    _CONTENT_TYPE_FORMAT = {
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.msword": "doc",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/vnd.ms-excel": "xls",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
        "application/vnd.ms-powerpoint": "ppt",
    }
    def _extract_document_pages(
        self, source: str, pages_spec: str, is_url: bool
    ) -> str:
        """Serve one page range of a document (see extract_document)."""
        from gossamer.structured_parser import parse_page_range

        try:
            start, end = parse_page_range(pages_spec)
        except ValueError as e:
            return json.dumps({"error": str(e)}, indent=2)

        suffix = (
            Path(urlparse(source).path).suffix if is_url else Path(source).suffix
        ).lower()
        if suffix not in self._PAGED_EXTRACT_SUFFIXES:
            return json.dumps(
                {
                    "error": (
                        f"Page selection is supported for PDF (pages) and "
                        f"XLSX (sheets); this source is "
                        f"{suffix or 'extensionless'}. Call extract_document "
                        f"without pages, or convert the file to PDF."
                    )
                },
                indent=2,
            )

        base_key = self._tb._cache_key(source) if is_url else source
        cache_key = f"{base_key}#pages={pages_spec}"
        cached = self._tb.cache.get(cache_key)
        if cached is not None:
            truncated = self._tb._budget._truncate(
                cached, self._tb.max_markdown_chars, self._tb.max_tokens
            )
            return self._finish_document(
                ExtractionResult(
                    source=source,
                    content=truncated,
                    content_tokens=count_tokens(truncated, self._tb.model_name),
                    cache_hit=True,
                    page_range=pages_spec,
                    # Tier 1.3: hash of the stored range content (the store
                    # keeps no fetch timestamp, so fetched_at stays None).
                    content_hash=_sha256_hex(cached),
                )
            )

        try:
            payload, prov = self._parse_document_pages(source, is_url)
            total = len(payload.pages)
            if total == 0:
                return json.dumps(
                    {"error": f"Document has no pages: {source}"}, indent=2
                )
            start = max(start, 1)
            if start > total:
                return json.dumps(
                    {
                        "error": (
                            f"Page range {pages_spec!r} out of bounds: "
                            f"document has {total} page(s)."
                        )
                    },
                    indent=2,
                )
            end = min(end if end is not None else total, total)
            end = max(end, start)
            selected = payload.pages[start - 1 : end]
            content = "\n\n".join(p.markdown for p in selected).strip()
            if not content:
                return json.dumps(
                    {
                        "error": (
                            f"Page range {pages_spec!r} produced no text."
                        )
                    },
                    indent=2,
                )

            self._tb.cache.put(cache_key, content)
            truncated = self._tb._budget._truncate(
                content, self._tb.max_markdown_chars, self._tb.max_tokens
            )
            return self._finish_document(
                ExtractionResult(
                    source=source,
                    content=truncated,
                    content_tokens=count_tokens(truncated, self._tb.model_name),
                    cache_hit=False,
                    page_range=pages_spec,
                    page_start=start,
                    page_end=end,
                    total_pages=total,
                    # Tier 1.3: hash of the delivered range plus download
                    # provenance (URL reads) or parse time (local reads).
                    content_hash=_sha256_hex(content),
                    **prov,
                )
            )
        except Exception as e:
            logger.error("Page-range extraction failed for %s: %s", source, e)
            return json.dumps(
                {"error": f"Page-range extraction failed: {str(e)}"}, indent=2
            )

    def _parse_document_pages(self, source: str, is_url: bool):
        """Parse a document into a ParsedDocumentPayload (URL or local).

        Returns (payload, provenance); for local files the provenance
        carries only the parse time.
        """
        import os
        import tempfile as tf

        parser = StructuredOxideParser()
        if not is_url:
            return parser.parse_file(source), {"fetched_at": _utc_now_iso()}
        data, prov = self._fetch_document_url(source)
        suffix = Path(urlparse(source).path).suffix.lower() or ".pdf"
        tmp_path = None
        try:
            with tf.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            return parser.parse_file(tmp_path), prov
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _extract_local(self, path: str, tables_as: str = "json") -> str:
        """Extract content from a local document file."""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        # S3: same byte cap as URL downloads (review A.2) — checked via
        # stat() before reading so a huge file fails fast.
        cap = max(1, int(self._tb.max_response_bytes))
        try:
            if file_path.stat().st_size > cap:
                raise ValueError(
                    f"Document too large: {file_path.stat().st_size} bytes "
                    f"(cap {cap})"
                )
        except OSError:
            pass
        content = file_path.read_bytes()
        if len(content) > cap:
            raise ValueError(f"Document too large: exceeds {cap} bytes")
        return self._extract_from_bytes(content, str(file_path), tables_as=tables_as)

    # M16: plain-text formats the extractor can really deliver — these are
    # exactly what DOCUMENT_EXTENSIONS (structured_parser) may advertise in
    # addition to pdf/OOXML. classify_link and _extract_from_bytes must stay
    # in sync, or the model is promised an extraction that would raise.
    # Tier 3.10 (item 10): JSON joins the text family (pretty-printed when
    # valid); XML/RSS/Atom get feed-aware extraction with a raw-text
    # fallback, so every advertised text format always delivers content.
    _TEXT_SUFFIXES = (".csv", ".txt", ".md", ".json")
    _XML_FEED_SUFFIXES = (".xml", ".rss", ".atom")
    # Content-types that mark a body as extractable text when the URL gives
    # no usable extension (extension-less API/feed/plain-text URLs).
    _TEXT_LIKE_CONTENT_TYPES = {
        "text/plain": "text",
        "text/markdown": "text",
        "text/csv": "text",
        "application/json": "json",
        "text/xml": "xml",
        "application/xml": "xml",
        "application/rss+xml": "xml",
        "application/atom+xml": "xml",
    }
    # Cap on entries surfaced from a feed; the output budget truncation
    # still applies on top.
    _FEED_MAX_ENTRIES = 50
    _UNSUPPORTED_FORMAT_HINTS = {
        ".doc": "convert the file to .docx",
        ".xls": "convert the file to .xlsx",
        ".ppt": "convert the file to .pptx",
        ".odt": "convert the file to .docx",
        ".ods": "convert the file to .xlsx",
        ".odp": "convert the file to .pptx",
        ".rtf": "convert the file to .docx or PDF",
        ".epub": "convert the file to PDF",
    }

    def _spreadsheet_csv(self, data: bytes) -> str:
        """Render spreadsheet bytes as per-sheet CSV blocks.

        Uses the office IR (via ``office_ir_tables``) so the output keeps
        sheet headings and the original row order. Sheet titles become
        ``## <title>`` headings; every table is written with the csv
        module (proper quoting/escaping).
        """
        import csv
        import io

        doc = require_office_oxide().from_bytes(data, "xlsx")
        try:
            ir = json.loads(doc.to_ir_json())
        except Exception:
            ir = {}
        blocks: list = []
        if isinstance(ir, dict):
            for title, rows in office_ir_tables(ir):
                buf = io.StringIO()
                writer = csv.writer(buf, lineterminator="\n")
                writer.writerows(rows)
                blocks.append(f"## {title}\n\n{buf.getvalue().rstrip(chr(10))}")
        return "\n\n".join(blocks)

    def _spreadsheet_json(self, data: bytes) -> str:
        """Render spreadsheet bytes as a JSON array of table objects.

        Shape per table: ``{"sheet": <title>, "headers": [...],
        "rows": [[...], ...]}`` — headers are the sheet's first row.
        Built from the office IR via ``office_ir_tables``.
        """
        doc = require_office_oxide().from_bytes(data, "xlsx")
        try:
            ir = json.loads(doc.to_ir_json())
        except Exception:
            ir = {}
        tables: list = []
        if isinstance(ir, dict):
            for title, rows in office_ir_tables(ir):
                tables.append(
                    {"sheet": title, "headers": rows[0], "rows": rows[1:]}
                )
        return json.dumps(tables, indent=2)

    def _extract_from_bytes(
        self, data: bytes, source: str, tables_as: str = "json"
    ) -> str:
        """Extract text from document bytes based on file type."""
        suffix = Path(source).suffix.lower()

        if suffix == ".pdf":
            doc = require_pdf_oxide().from_bytes(data)
            return doc.to_markdown_all()
        elif suffix in (".docx", ".xlsx", ".pptx"):
            if suffix == ".xlsx" and tables_as in ("csv", "json"):
                return (
                    self._spreadsheet_csv(data)
                    if tables_as == "csv"
                    else self._spreadsheet_json(data)
                )
            # office-oxide >= 0.1.10 requires the format explicitly.
            doc = require_office_oxide().from_bytes(data, suffix.lstrip("."))
            return doc.to_markdown()
        elif suffix in self._TEXT_SUFFIXES:
            # M16: plain text is trivial to support and covers the
            # CSV/TXT/MD/JSON links that classify_link advertises.
            if suffix == ".json":
                return self._extract_json_text(data)
            return data.decode("utf-8-sig", errors="replace")
        elif suffix in self._XML_FEED_SUFFIXES:
            # Tier 3.10: RSS/Atom feeds become readable entry lists; any
            # other (or malformed) XML falls back to the raw text.
            return self._extract_xml_feed(data)
        elif suffix in self._UNSUPPORTED_FORMAT_HINTS:
            # M16: honest, actionable failure for formats we cannot parse.
            hint = self._UNSUPPORTED_FORMAT_HINTS[suffix]
            raise ValueError(
                f"Unsupported document format: {suffix} ({hint})."
            )
        else:
            raise ValueError(f"Unsupported document format: {suffix}")

    @staticmethod
    def _extract_json_text(data: bytes) -> str:
        """Tier 3.10: JSON as text — pretty-printed when valid, raw otherwise."""
        import json as _json

        raw = data.decode("utf-8-sig", errors="replace")
        try:
            obj = _json.loads(raw)
        except ValueError:
            return raw
        return _json.dumps(obj, indent=2, ensure_ascii=False)

    @staticmethod
    def _extract_xml_feed(data: bytes) -> str:
        """Tier 3.10: RSS/Atom/RDF feeds as readable entries.

        Uses the stdlib ElementTree (no new dependency). Feeds are detected
        by local tag name so namespaces are irrelevant. Any parse failure —
        or an XML document with no item/entry elements (e.g. a sitemap) —
        falls back to the raw text so a .xml/.rss/.atom source always
        delivers *something* readable.
        """
        import xml.etree.ElementTree as ET

        raw = data.decode("utf-8-sig", errors="replace")
        try:
            root = ET.fromstring(data)
        except (ET.ParseError, UnicodeDecodeError, ValueError):
            return raw

        def _local(tag) -> str:
            return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""

        def _text(el, names) -> str:
            for child in el.iter():
                if _local(child.tag) in names and child.text and child.text.strip():
                    return child.text.strip()
            return ""

        entries = []
        for el in root.iter():
            if _local(el.tag) not in ("item", "entry"):
                continue
            title = _text(el, {"title"})
            link = ""
            for child in el.iter():
                if _local(child.tag) == "link":
                    href = child.get("href")
                    if href:
                        link = href
                    elif child.text and child.text.strip():
                        link = child.text.strip()
                    break
            desc = _text(el, {"description", "summary", "content"})
            date = _text(el, {"pubDate", "published", "updated", "date"})
            entries.append((title, link, date, desc))

        if not entries:
            return raw

        feed_title = ""
        for child in root:  # direct children: channel / feed
            feed_title = _text(child, {"title"})
            if feed_title:
                break

        lines = []
        if feed_title:
            lines.append(f"# {feed_title}")
            lines.append("")
        lines.append(f"Feed entries: {min(len(entries), 50)}")
        lines.append("")
        shown = 0
        for title, link, date, desc in entries:
            if shown >= 50:
                break
            shown += 1
            lines.append(f"- **{title or '(untitled)'}**")
            if link:
                lines.append(f"  Link: {link}")
            if date:
                lines.append(f"  Date: {date}")
            if desc:
                lines.append(f"  {desc}")
        if len(entries) > 50:
            lines.append("")
            lines.append(
                f"… {len(entries) - 50} more entries not shown "
                f"(capped at 50)."
            )
        return "\n".join(lines)

    # ───────────────────────────────
    # ───────────────────────────────
    # Structured Document Extraction
    # ───────────────────────────────

    def extract_document_structured(self, source: str) -> str:
        """Backwards-compatible wrapper: structured extraction.

        Kept so existing callers keep working; the P8 tool surface now
        exposes this through ``extract_document(structured=True)``.
        """
        return self._extract_document_structured_impl(source)

    def _extract_document_structured_impl(self, source: str) -> str:
        """
        Download (if URL) and parse a document into a structured
        ParsedDocumentPayload with metadata, pages, and tables.
        """
        import os
        import tempfile as tf

        parsed = urlparse(source)
        is_url = parsed.scheme in ("http", "https")

        if is_url:
            try:
                self._tb._validate_url(source)
            except (ValueError, SsrfBlockedError) as e:
                return json.dumps(self._tb._url_error(source, e), indent=2)
            # S4: robots gate before rate-limiting.
            if self._tb._robots_disallows(source):
                return json.dumps(
                    {
                        "error": (
                            f"Document fetch disallowed by robots.txt: {source}"
                        )
                    },
                    indent=2,
                )
            self._tb._rate_limit_domain(source)

        tmp_path = None
        try:
            if is_url:
                with httpx.Client(timeout=30) as client:
                    response = client.get(source, headers=self._tb._next_headers())
                    response.raise_for_status()

                suffix = Path(source).suffix.lower() or ".pdf"
                with tf.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(response.content)
                    tmp_path = tmp.name
            else:
                tmp_path = source

            parser = StructuredOxideParser()
            payload = parser.parse_file(tmp_path)

            # Files lose <a href> structure: detect URLs written into the
            # extracted text so documents get the same follow-up signal
            # HTML pages get. (Anchored links, when the parser provides
            # them, take precedence.)
            if not payload.links:
                full_text = "\n".join(p.raw_text for p in payload.pages)
                text_urls = extract_links(full_text)
                if text_urls:
                    payload.links = build_follow_up_candidates(
                        [(u, "(text)") for u in text_urls]
                    )

            if is_url and tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            # Budget the page text, never the serialized JSON: a string
            # cut here would hand the model unparseable output.
            payload_json = payload.to_json()
            return self._tb._budget._fit_json(
                lambda b: self._tb._budget._shrink_parsed_payload(payload_json, b),
                self._tb.max_markdown_chars,
                self._tb.max_tokens,
                {
                    "source": source,
                    "error": "document too large for the output budget",
                    "hint": "narrow the read with the pages parameter",
                },
            )

        except Exception as e:
            logger.error("Structured extraction failed for %s: %s", source, e)
            if is_url and tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            return json.dumps(
                {"error": f"Structured document extraction failed: {str(e)}"}, indent=2
            )

    # ───────────────────────────────
    # HTML Structured Inspection
    # ───────────────────────────────

    # Tier 3.11: caps for HTML table extraction (a page with a 10,000-row
    # table must not drown the token budget in table JSON).
    _HTML_MAX_TABLES = 20
    _HTML_MAX_TABLE_ROWS = 500

    def _extract_html_tables(self, html: str):
        """Tier 3.11: extract HTML tables into ExtractedTable objects.

        Best effort: extraction errors are logged and yield no tables; the
        page content itself is never affected. Returns a list of
        ExtractedTable (name, headers, rows) in document order.
        """
        from gossamer.structured_parser import ExtractedTable

        try:
            raw = extract_tables_from_html(
                html, self._HTML_MAX_TABLES, self._HTML_MAX_TABLE_ROWS
            )
        except Exception as e:
            logger.warning("HTML table extraction failed: %s", e)
            return []
        return [
            ExtractedTable(name=name, headers=headers, rows=rows)
            for name, headers, rows in raw
        ]

    def inspect_html_structured(self, url: str, use_smart: str = FetchMode.AUTO.value) -> str:
        """Backwards-compatible wrapper: structured output from ``inspect_html_page``.

        Kept so existing callers keep working; the P8 tool surface now
        exposes this through ``inspect_html_page(..., structured=True)``.
        """
        return self._inspect_html_structured_impl(url, use_smart)

    def _inspect_html_structured_impl(
        self, url: str, use_smart: str = FetchMode.AUTO.value
    ) -> str:
        """
        Fetch a web page and return it as a structured ParsedDocumentPayload
        with metadata (OG, Twitter, JSON-LD), markdown content, tables,
        and links.

        Unifies the HTML fetching pipeline with the structured document
        pipeline so that web pages and file documents produce the same
        ParsedDocumentPayload output.

        Parameters
        ----------
        url : str
            The URL to inspect.
        use_smart : {"auto", "browser", "static"}
            Per-call render strategy. "auto" (default) follows fetch_mode
            (static first, stealth browser on failure/non-text); "browser"
            renders with the headless browser first (static on failure);
            "static" is static-only.

        Returns
        -------
        str
            JSON-serialised ParsedDocumentPayload (token-truncated).
        """
        url, url_error = self._tb._prepare_url(url)
        if url_error is not None:
            return json.dumps(url_error, indent=2)

        # Cache stores the untruncated payload JSON; budgets are re-applied
        # on every read so changed limits are honored. A cached result is
        # served on repeat visits too (C3: data beats a warning).
        cached_json = self._tb.cache.get("structured:" + self._tb._cache_key(url))

        try:
            if cached_json is not None:
                logger.info("Cache hit (structured) for %s", url)
                return self._tb._budget._fit_json(
                    lambda b: self._tb._budget._shrink_parsed_payload(cached_json, b),
                    self._tb.max_markdown_chars,
                    self._tb.max_tokens,
                    {
                        "url": url,
                        "error": "page too large for the output budget",
                        "cache_hit": True,
                    },
                )
            # S4: robots.txt compliance -- only on the fetch path.
            if self._tb._robots_disallows(url):
                logger.warning("URL disallowed by robots.txt: %s", url)
                return json.dumps(
                    {"warning": "URL disallowed by robots.txt", "url": url}, indent=2
                )
            if not self._tb._claim_in_flight(url):
                logger.warning("URL already visited or in flight: %s", url)
                return json.dumps(
                    {"warning": "URL already visited", "url": url}, indent=2
                )
            self._tb._rate_limit_domain(url)

            markdown, links, html_metadata, fetch_method, html = (
                self._tb._fetch._fetch_html_with_html(url, use_smart)
            )

            # Tier 3.11: extract tables from the raw HTML (static path
            # only; browser renders expose no raw DOM).
            tables = self._extract_html_tables(html) if html else []

            # Build structured payload via unified parser
            parser = StructuredOxideParser()
            payload = parser.parse_html(
                markdown=markdown,
                links=links,
                html_metadata=html_metadata,
                url=url,
                max_links=self._tb.max_links,
                tables=tables,
            )
            # §7: guard the payload before caching so repeat reads carry
            # the already-scanned result.
            if self._tb._fetch._scan_structured_guard(payload, url):
                self._tb._release_in_flight(url)  # S5: stays retryable
                return json.dumps(
                    {
                        "error": "content withheld by prompt-injection guard",
                        "url": url,
                        "guard": payload.guard,
                    },
                    indent=2,
                )

            payload_json = payload.to_json()
            self._tb.cache.put("structured:" + self._tb._cache_key(url), payload_json)
            self._tb._mark_visited(url)  # success only (C3)
            self._tb._release_in_flight(url)  # S5
            return self._tb._budget._fit_json(
                lambda b: self._tb._budget._shrink_parsed_payload(payload_json, b),
                self._tb.max_markdown_chars,
                self._tb.max_tokens,
                {"url": url, "error": "page too large for the output budget"},
            )
        except Exception as e:
            self._tb._release_in_flight(url)  # S5: stays retryable
            logger.error("Structured HTML inspection failed for %s: %s", url, e)
            return json.dumps(
                {"error": f"Structured HTML inspection failed: {str(e)}"}, indent=2
            )
