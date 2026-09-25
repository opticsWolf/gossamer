"""DOI normalization and OpenAlex open-access location lookup."""

from __future__ import annotations

import json
import re
from urllib.parse import unquote, urlsplit

import httpx

from gossamer.research_providers import OpenAlexAdapter
from gossamer.search_providers import ProviderRateLimitError

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_DOI_HOSTS = {"doi.org", "www.doi.org", "dx.doi.org"}


def normalize_doi(value: str) -> str:
    """Normalize a bare DOI, ``doi:`` value, or DOI resolver URL.

    The returned DOI is lower-case because DOI matching is case-insensitive.
    This validates DOI shape only; it does not assert that a DOI is registered.
    """
    if not isinstance(value, str):
        raise ValueError("DOI must be a string")
    doi = value.strip()
    if doi.lower().startswith("doi:"):
        doi = doi[4:].strip()
    else:
        parsed = urlsplit(doi)
        if parsed.scheme.lower() in {"http", "https"} and (parsed.hostname or "").lower() in _DOI_HOSTS:
            if parsed.query or parsed.fragment:
                raise ValueError("DOI resolver URLs must not include a query or fragment")
            doi = unquote(parsed.path.lstrip("/"))
    if not _DOI_RE.fullmatch(doi):
        raise ValueError("expected a DOI such as 10.1234/example")
    return doi.lower()


def _oa_candidate(location: dict, *, is_best: bool, work_is_oa: bool) -> dict | None:
    if not isinstance(location, dict):
        return None
    location_is_oa = location.get("is_oa")
    if location_is_oa is False or (location_is_oa is None and not work_is_oa):
        return None

    pdf_url = location.get("pdf_url")
    landing_page_url = location.get("landing_page_url")
    url = pdf_url or landing_page_url
    if not isinstance(url, str) or not url.strip():
        return None
    source = location.get("source")
    if isinstance(source, dict):
        source_name = source.get("display_name")
        source_id = source.get("id")
    else:
        source_name = source if isinstance(source, str) else None
        source_id = None
    return {
        "url": url,
        "kind": "pdf" if pdf_url else "landing_page",
        "pdf_url": pdf_url,
        "landing_page_url": landing_page_url,
        "source": source_name,
        "source_id": source_id,
        "is_oa": location_is_oa if location_is_oa is not None else work_is_oa,
        "license": location.get("license"),
        "version": location.get("version"),
        "is_best": is_best,
    }


class OpenAccessLocator:
    """Resolve a DOI to inspectable OpenAlex OA locations; never downloads."""

    def __init__(self, adapter_factory=OpenAlexAdapter):
        self._adapter_factory = adapter_factory

    def locate(self, doi: str) -> dict:
        try:
            normalized = normalize_doi(doi)
        except ValueError as exc:
            return {
                "doi": doi,
                "status": "error",
                "error": {"code": "invalid_doi", "message": str(exc)},
                "candidates": [],
            }

        try:
            records = self._adapter_factory().fetch_by_doi(normalized)
        except ProviderRateLimitError as exc:
            return {
                "doi": normalized,
                "provider": "openalex",
                "status": "error",
                "error": {"code": "rate_limited", "message": str(exc)},
                "candidates": [],
            }
        except httpx.HTTPStatusError as exc:
            code = "rate_limited" if exc.response.status_code == 429 else "provider_error"
            return {
                "doi": normalized,
                "provider": "openalex",
                "status": "error",
                "error": {"code": code, "message": str(exc)},
                "candidates": [],
            }
        except Exception as exc:  # noqa: BLE001 - surface provider failures as data
            return {
                "doi": normalized,
                "provider": "openalex",
                "status": "error",
                "error": {"code": "provider_error", "message": str(exc)},
                "candidates": [],
            }

        if not records:
            return {
                "doi": normalized,
                "provider": "openalex",
                "status": "not_found",
                "candidates": [],
            }

        record = records[0]
        try:
            raw = json.loads(record.get("raw") or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            return {
                "doi": normalized,
                "provider": "openalex",
                "status": "error",
                "error": {"code": "invalid_provider_record", "message": str(exc)},
                "candidates": [],
            }

        open_access = raw.get("open_access") or {}
        work_is_oa = bool(open_access.get("is_oa"))
        best = raw.get("best_oa_location")
        locations = raw.get("locations") or []
        ordered = []
        if isinstance(best, dict):
            ordered.append((best, True))
        if isinstance(locations, list):
            ordered.extend((location, False) for location in locations)

        candidates = []
        seen_urls = set()
        for location, is_best in ordered:
            candidate = _oa_candidate(location, is_best=is_best, work_is_oa=work_is_oa)
            if candidate is None or candidate["url"] in seen_urls:
                continue
            seen_urls.add(candidate["url"])
            candidates.append(candidate)

        if candidates:
            status = "open_access"
        elif work_is_oa:
            status = "open_access_no_location"
        else:
            status = "closed_access"
        return {
            "doi": normalized,
            "provider": "openalex",
            "status": status,
            "work": {
                "id": record.get("id"),
                "title": record.get("title"),
                "url": record.get("url"),
                "published": record.get("published"),
                "is_oa": work_is_oa,
                "oa_status": open_access.get("oa_status"),
            },
            "candidates": candidates,
        }
