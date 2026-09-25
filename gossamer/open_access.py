"""DOI normalization and open-access location lookup (OpenAlex + Unpaywall)."""

from __future__ import annotations

import json
import re
from urllib.parse import quote, unquote, urlsplit

import httpx

from gossamer.env import getenv as _env_get
from gossamer.research_providers import OpenAlexAdapter
from gossamer.search_providers import ProviderRateLimitError, retry_after_seconds

UNPAYWALL_BASE = "https://api.unpaywall.org/v2"

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


def _unpaywall_candidate(location: dict, *, is_best: bool) -> dict | None:
    """Map one Unpaywall v2 location to the shared OA-candidate shape.

    Live-verified contract (2026-09-26): ``GET /v2/{doi}?email=`` enforces a
    real contact address (example.com yields HTTP 422). Field mapping is
    defensive across documented v2 names (``url_for_pdf``/``url_for_landing_page``)
    and OpenAlex-style fallbacks (``pdf_url``/``landing_page_url``); unknown
    shapes are skipped rather than guessed.
    """
    if not isinstance(location, dict):
        return None
    pdf_url = location.get("url_for_pdf") or location.get("pdf_url")
    landing = (
        location.get("url_for_landing_page")
        or location.get("landing_page_url")
        or location.get("url")
    )
    url = pdf_url or landing
    if not isinstance(url, str) or not url.strip():
        return None
    source = location.get("source")
    if isinstance(source, dict):
        source_name = source.get("display_name")
    else:
        source_name = source if isinstance(source, str) else None
    if not source_name:
        source_name = location.get("endpoint_label") or location.get("host_type")
    return {
        "url": url,
        "kind": "pdf" if pdf_url else "landing_page",
        "pdf_url": pdf_url,
        "landing_page_url": landing,
        "source": source_name,
        "source_id": None,
        "is_oa": True,
        "license": location.get("license"),
        "version": location.get("version"),
        "is_best": is_best,
    }


def fetch_unpaywall(doi: str, email: str, timeout: float = 20.0) -> dict:
    """Fetch one Unpaywall v2 record; maps HTTP errors to typed failures."""
    url = f"{UNPAYWALL_BASE}/{quote(doi, safe='')}"
    try:
        response = httpx.get(url, params={"email": email}, timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status == 429:
            raise ProviderRateLimitError(
                "unpaywall", 429,
                retry_after=retry_after_seconds(exc),
                message="Unpaywall rate limit (HTTP 429); back off before retrying.",
            ) from exc
        raise
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Unpaywall API returned a non-object JSON response")
    return payload


def unpaywall_candidates(payload: dict) -> tuple[list[dict], dict]:
    """Extract deduplicated OA candidates and work metadata from Unpaywall."""
    best = payload.get("best_oa_location")
    locations = payload.get("oa_locations") or []
    ordered = []
    if isinstance(best, dict):
        ordered.append((best, True))
    if isinstance(locations, list):
        ordered.extend((item, False) for item in locations)
    candidates: list[dict] = []
    seen: set[str] = set()
    for location, is_best in ordered:
        candidate = _unpaywall_candidate(location, is_best=is_best)
        if candidate is None or candidate["url"] in seen:
            continue
        seen.add(candidate["url"])
        candidates.append(candidate)
    work = {
        "id": None,
        "title": payload.get("title"),
        "url": payload.get("doi_url"),
        "published": payload.get("published_date"),
        "is_oa": bool(payload.get("is_oa", bool(candidates))),
        "oa_status": payload.get("oa_status"),
    }
    return candidates, work


class OpenAccessLocator:
    """Resolve a DOI to inspectable OA locations; never downloads.

    OpenAlex is queried first. When it yields no usable candidate (or fails)
    and ``GOSSAMER_UNPAYWALL_EMAIL`` is configured, Unpaywall v2 is queried as
    an opt-in fallback; without an email it is skipped without a request.
    """

    def __init__(self, adapter_factory=OpenAlexAdapter, unpaywall_email=None, unpaywall_fetcher=None):
        self._adapter_factory = adapter_factory
        email = unpaywall_email if unpaywall_email is not None else _env_get("GOSSAMER_UNPAYWALL_EMAIL", "")
        self._unpaywall_email = email.strip() if isinstance(email, str) else ""
        self._unpaywall_fetcher = unpaywall_fetcher or fetch_unpaywall

    def _query_unpaywall(self, normalized: str) -> tuple[list[dict], dict, dict | None]:
        """Query Unpaywall when an email is configured; never raises."""
        if not self._unpaywall_email:
            return [], {}, None
        try:
            payload = self._unpaywall_fetcher(normalized, self._unpaywall_email)
            candidates, work = unpaywall_candidates(payload)
            return candidates, work, None
        except ProviderRateLimitError as exc:
            return [], {}, {"code": "rate_limited", "message": str(exc)}
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            code = "rate_limited" if status == 429 else "provider_error"
            return [], {}, {"code": code, "message": str(exc)}
        except Exception as exc:  # noqa: BLE001 - fallback failures stay advisory
            return [], {}, {"code": "provider_error", "message": str(exc)}

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
            fallback, fallback_work, fallback_error = self._query_unpaywall(normalized)
            if fallback:
                return {
                    "doi": normalized,
                    "provider": "unpaywall",
                    "sources": ["openalex", "unpaywall"],
                    "status": "open_access",
                    "work": fallback_work,
                    "candidates": fallback,
                    "provider_errors": [{"provider": "openalex", "message": str(exc)}],
                }
            error: dict = {"code": "rate_limited", "message": str(exc)}
            if fallback_error:
                error["unpaywall"] = fallback_error
            return {
                "doi": normalized,
                "provider": "openalex",
                "sources": ["openalex", "unpaywall"] if self._unpaywall_email else ["openalex"],
                "status": "error",
                "error": error,
                "candidates": [],
            }
        except httpx.HTTPStatusError as exc:
            code = "rate_limited" if exc.response.status_code == 429 else "provider_error"
            fallback, fallback_work, _ = self._query_unpaywall(normalized)
            if fallback:
                return {
                    "doi": normalized,
                    "provider": "unpaywall",
                    "sources": ["openalex", "unpaywall"],
                    "status": "open_access",
                    "work": fallback_work,
                    "candidates": fallback,
                    "provider_errors": [{"provider": "openalex", "message": str(exc)}],
                }
            return {
                "doi": normalized,
                "provider": "openalex",
                "sources": ["openalex", "unpaywall"] if self._unpaywall_email else ["openalex"],
                "status": "error",
                "error": {"code": code, "message": str(exc)},
                "candidates": [],
            }
        except Exception as exc:  # noqa: BLE001 - surface provider failures as data
            fallback, fallback_work, _ = self._query_unpaywall(normalized)
            if fallback:
                return {
                    "doi": normalized,
                    "provider": "unpaywall",
                    "sources": ["openalex", "unpaywall"],
                    "status": "open_access",
                    "work": fallback_work,
                    "candidates": fallback,
                    "provider_errors": [{"provider": "openalex", "message": str(exc)}],
                }
            return {
                "doi": normalized,
                "provider": "openalex",
                "sources": ["openalex", "unpaywall"] if self._unpaywall_email else ["openalex"],
                "status": "error",
                "error": {"code": "provider_error", "message": str(exc)},
                "candidates": [],
            }

        if not records:
            fallback, fallback_work, _ = self._query_unpaywall(normalized)
            if fallback:
                return {
                    "doi": normalized,
                    "provider": "unpaywall",
                    "sources": ["openalex", "unpaywall"],
                    "status": "open_access",
                    "work": fallback_work,
                    "candidates": fallback,
                }
            return {
                "doi": normalized,
                "provider": "openalex",
                "sources": ["openalex", "unpaywall"] if self._unpaywall_email else ["openalex"],
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
            return {
                "doi": normalized,
                "provider": "openalex",
                "sources": ["openalex"],
                "status": "open_access",
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
        fallback, fallback_work, _ = self._query_unpaywall(normalized)
        if fallback:
            merged = list(candidates)
            seen = set(seen_urls)
            for item in fallback:
                if item["url"] in seen:
                    continue
                seen.add(item["url"])
                merged.append(item)
            return {
                "doi": normalized,
                "provider": "unpaywall",
                "sources": ["openalex", "unpaywall"],
                "status": "open_access",
                "work": {
                    "id": record.get("id"),
                    "title": record.get("title") or fallback_work.get("title"),
                    "url": record.get("url"),
                    "published": record.get("published") or fallback_work.get("published"),
                    "is_oa": True,
                    "oa_status": open_access.get("oa_status") or fallback_work.get("oa_status"),
                },
                "candidates": merged or fallback,
            }
        status = "open_access_no_location" if work_is_oa else "closed_access"
        return {
            "doi": normalized,
            "provider": "openalex",
            "sources": ["openalex", "unpaywall"] if self._unpaywall_email else ["openalex"],
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
