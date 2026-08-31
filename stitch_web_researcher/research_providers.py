"""Domain data-source adapters built on the unified :class:`ResourceAdapter`.

These demonstrate the ``ResourceAdapter`` contract for the scholarly / geo
resources called out in ``docs/research_access_layer_plan.md``. Each adapter
owns only its request + parse logic; politeness, quota, auth injection, live
header retuning and retry/backoff come free from the base class.

Built so far (Phase 2 — robust, low-risk, no / cheap keys):
  * :class:`OpenAlexAdapter`  — scholarly works search / lookup (scholarly)
  * :class:`OpenMeteoAdapter` — weather/climate + place lookup (geo)
  * :class:`CrossrefAdapter`  — works / DOI lookup (scholarly)
  * :class:`ArxivAdapter`     — preprint search (scholarly)
  * :class:`PubmedAdapter`    — biomedical literature search / fetch (scholarly)
  * :class:`DoajAdapter`      — open-access journals search (scholarly)
  * :class:`OpenLibraryAdapter` — book search / lookup (library)
  * :class:`WorldBankAdapter` — country / series data (financial)
  * :class:`FredAdapter`      — macro time-series data (financial)
  * :class:`GitHubAdapter`    — code / repository search (tech)

Built so far (Phase 3 — domain waves):
  * :class:`NASAAdapter`      — Near-Earth objects via NeoWs (geo)
  * :class:`NvdAdapter`       — NIST vulnerability DB v2 JSON (tech)
  * :class:`SoftwareHeritageAdapter` — source-archive search (tech)
  * :class:`ZenodoAdapter`    — research-records search / lookup (scholarly)
  * :class:`CongressAdapter`  — Congress.gov legislative data (legal, key)
  * :class:`YahooFinanceAdapter` — unofficial quote / chart data (financial)
  * :class:`OverpassAdapter`  — OSM geo queries (geo)
  * :class:`CensusAdapter`    — US Census data API (geo, key)

Built so far (Phase 3 wave 2 — legal / scholarly / financial):
  * :class:`CourtListenerAdapter` — court-opinion search (legal, keyless)
  * :class:`EcfrAdapter`          — US Code of Federal Regulations lookup (legal, keyless)
  * :class:`FederalRegisterAdapter` — US Federal Register notices (legal, key)
  * :class:`EurlexAdapter`        — EU law search (legal, keyless)
  * :class:`GermanGovAdapter`     — German Federal Gazette / gov data (legal, keyless)
  * :class:`BioRxivAdapter`       — bioRxiv / medRxiv preprints (scholarly, keyless)
  * :class:`ChemRxivAdapter`      — ChemRxiv preprints (scholarly, token)
  * :class:`AlphaVantageAdapter`  — market data: company search + daily OHLC (financial, key)
"""

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import date as _date
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs

import httpx

from stitch_web_researcher.search_providers import RateLimit, RateState, ResourceAdapter

_UA = "stitch-web-researcher/0.5.3"


def _parse_lat_lon(lat_lon: Union[str, Tuple[float, float], List[float]]) -> Tuple[float, float]:
    """Accept ``"lat,lon"`` or ``(lat, lon)`` and return ``(float, float)``."""
    if isinstance(lat_lon, (tuple, list)):
        return float(lat_lon[0]), float(lat_lon[1])
    parts = str(lat_lon).split(",")
    return float(parts[0]), float(parts[1])


class OpenAlexAdapter(ResourceAdapter):
    """OpenAlex scholarly-works search (https://docs.openalex.org).

    Keyless, but always send a polite ``Contact-Agent`` / ``User-Agent``
    carrying an email so the pool reserves you a slot; a free API key gives
    ~10x the daily budget. The documented safe ceiling is <100 rps.
    """

    name = "openalex"
    domain = "scholarly"
    requires_key = False
    BASE = "https://api.openalex.org"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        email: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        import os

        self.email = email or os.environ.get("STITCH_OPENALEX_EMAIL") or "research@example.org"
        self.api_key = api_key or os.environ.get("STITCH_OPENALEX_KEY", "")
        self._last_search = 0.0
        self._last_fetch = 0.0
        # <100 rps ceiling; keep a conservative gap with light jitter.
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.5, jitter=0.25),
            fetch_delay,
        )

    def inject_auth(self, url, params=None, headers=None):
        ua = f"{_UA}?email={self.email}"
        h = dict(headers or {})
        h.setdefault("User-Agent", ua)
        h.setdefault("Contact-Agent", ua)
        p = dict(params or {})
        if self.api_key:
            p.setdefault("api_key", self.api_key)
        return url, p, h

    def parse_headers(self, status, headers):
        # OpenAlex exposes no X-RateLimit headers; report the documented ceiling.
        return RateState(rps=100.0)

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        url = f"{self.BASE}/works"
        url, params, headers = self.inject_auth(
            url, {"search": query, "per_page": min(max_results, 200)}, {}
        )
        resp = httpx.get(url, params=params, headers=headers, timeout=15.0)
        resp.raise_for_status()
        out: List[Dict[str, str]] = []
        for w in resp.json().get("results", [])[:max_results]:
            authors = [
                a.get("author", {}).get("display_name", "")
                for a in w.get("authorships", [])
            ]
            authors = [a for a in authors if a]
            out.append(
                {
                    "source": "openalex",
                    "id": w.get("id", ""),
                    "title": w.get("title") or "",
                    "url": w.get("doi") or w.get("id"),
                    "doi": w.get("doi", ""),
                    "published": w.get("publication_date", ""),
                    "authors": ", ".join(authors),
                    "citations": w.get("cited_by_count", 0),
                    "snippet": ", ".join(authors),
                    "raw": json.dumps(w),
                }
            )
        return out

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url = f"{self.BASE}/works/{record_id}"
        url, params, headers = self.inject_auth(url, params, {})
        resp = httpx.get(url, params=params, headers=headers, timeout=15.0)
        resp.raise_for_status()
        w = resp.json()
        return [
            {
                "source": "openalex",
                "id": w.get("id", ""),
                "title": w.get("title") or "",
                "url": w.get("doi") or w.get("id"),
                "doi": w.get("doi", ""),
                "published": w.get("publication_date", ""),
                "raw": json.dumps(w),
            }
        ]


class OpenMeteoAdapter(ResourceAdapter):
    """Open-Meteo weather/climate + place lookup (https://open-meteo.com).

    Keyless free tier: 10,000 calls/day with no per-minute limit (burst
    throttled). ``search`` resolves a place name via the sibling geocoding
    service; ``fetch`` returns the forecast for coordinates.
    """

    name = "open-meteo"
    domain = "geo"
    requires_key = False
    BASE = "https://api.open-meteo.com/v1/forecast"
    GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
    ):
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay
            if delay is not None
            else RateLimit(
                search_interval=1.0, jitter=0.5, quota=10000, quota_window="day"
            ),
            fetch_delay,
        )

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        resp = httpx.get(
            self.GEOCODE,
            params={"name": query, "count": max_results, "language": "en"},
            timeout=15.0,
        )
        resp.raise_for_status()
        hits = resp.json().get("results", [])
        out: List[Dict[str, str]] = []
        for h in hits[:max_results]:
            title = ", ".join(
                part for part in (h.get("name"), h.get("admin1"), h.get("country")) if part
            )
            out.append(
                {
                    "source": "open-meteo",
                    "id": f"{h.get('latitude', 0)},{h.get('longitude', 0)}",
                    "title": title,
                    "url": h.get("url")
                    or f"{self.BASE}?latitude={h.get('latitude')}&longitude={h.get('longitude')}",
                    "snippet": h.get("country", ""),
                    "raw": json.dumps(h),
                }
            )
        return out

    def fetch(self, lat_lon, params=None):
        self._enforce_delay()
        lat, lon = _parse_lat_lon(lat_lon)
        p = {"latitude": lat, "longitude": lon}
        if params:
            p.update({k: v for k, v in params.items() if v is not None})
        resp = httpx.get(self.BASE, params=p, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        current = data.get("current", {})
        return [
            {
                "source": "open-meteo",
                "id": f"{lat},{lon}",
                "title": "Open-Meteo forecast",
                "url": f"{self.BASE}?latitude={lat}&longitude={lon}",
                "snippet": ", ".join(f"{k}={v}" for k, v in current.items()),
                "raw": json.dumps(data),
            }
        ]


# ────────────────────────────────────────────────────────────────
# Phase 2 adapters (scholarly / library / financial / tech)
# ────────────────────────────────────────────────────────────────


def _join(*parts):
    """Join path parts, dropping empties."""
    return "/".join(str(p).strip("/") for p in parts if p not in (None, ""))


def _rate_state_from_headers(headers, default_rps=None):
    """Build a :class:`RateState` from ``X-RateLimit-*`` style headers.

    Handles both the ``X-RateLimit-*`` (GitHub/Crossref) family. Missing
    fields fall back to ``default_rps`` / ``None`` so the budget report
    stays honest rather than guessing.
    """

    def _get(*names):
        for n in names:
            v = headers.get(n)
            if v is not None:
                return v
        return None

    remaining = _get("X-RateLimit-Remaining", "X-Rate-Limit-Remaining")
    limit = _get("X-RateLimit-Limit", "X-Rate-Limit-Limit")
    reset = _get("X-RateLimit-Reset", "X-Rate-Limit-Reset")
    retry_after = _get("Retry-After")
    state = RateState(retry_after=float(retry_after) if retry_after else None)
    if limit is not None:
        try:
            state.rps = float(limit)
        except ValueError:
            state.rps = default_rps
    elif default_rps is not None:
        state.rps = default_rps
    if remaining is not None:
        try:
            state.remaining = int(remaining)
        except ValueError:
            pass
    if reset is not None:
        try:
            # Header may be "remaining seconds" (Crossref) or an epoch unix
            # timestamp (GitHub); the coordinator only reads it for reports.
            state.reset_seconds = int(reset)
        except ValueError:
            pass
    return state


class CrossrefAdapter(ResourceAdapter):
    """Crossref works search / DOI lookup (https://api.crossref.org).

    Keyless polite pool — always send a ``User-Agent`` / ``Contact-Agent``
    carrying an email. Crossref exposes per-pool ``X-Rate-Limit-*`` headers
    and a concurrency limit; :meth:`parse_headers` retunes from them.
    """

    name = "crossref"
    domain = "scholarly"
    requires_key = False
    BASE = "https://api.crossref.org"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        email: Optional[str] = None,
    ):
        self.email = email or "research@example.org"
        self._last_search = 0.0
        self._last_fetch = 0.0
        # No published hard rps; polite base + jitter, retuned live.
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.2, jitter=0.1),
            fetch_delay,
        )

    def inject_auth(self, url, params=None, headers=None):
        h = dict(headers or {})
        h.setdefault("User-Agent", f"{_UA}?email={self.email}")
        h.setdefault("Contact-Agent", f"{_UA}?email={self.email}")
        h.setdefault("Accept", "application/json")
        return url, dict(params or {}), h

    def parse_headers(self, status, headers):
        return _rate_state_from_headers(headers, default_rps=10.0)

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        # Crossref /works uses cursor pagination; ``per_page``/``offset`` are
        # rejected (400). Default page size (20) is plenty for a search.
        url = f"{self.BASE}/works"
        url, params, headers = self.inject_auth(url, {"query": query}, {})
        resp = httpx.get(url, params=params, headers=headers, timeout=20.0)
        resp.raise_for_status()
        msg = resp.json().get("message", {})
        out: List[Dict[str, str]] = []
        for w in msg.get("items", [])[:max_results]:
            title = (w.get("title") or [""])[0]
            authors = [a.get("family", a.get("name", "")) for a in w.get("author", [])]
            out.append(
                {
                    "source": "crossref",
                    "id": w.get("DOI", ""),
                    "title": title,
                    "url": w.get("URL"),
                    "doi": w.get("DOI", ""),
                    "published": (w.get("published", {}) or {}).get("date-parts", [[""]])[0],
                    "authors": ", ".join(a for a in authors if a),
                    "snippet": (w.get("abstract") or "")[:240],
                    "raw": json.dumps(w),
                }
            )
        return out

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url = f"{self.BASE}/works/{record_id}"
        url, params, headers = self.inject_auth(url, params, {})
        resp = httpx.get(url, params=params, headers=headers, timeout=20.0)
        resp.raise_for_status()
        w = resp.json()["message"]
        authors = [a.get("family", a.get("name", "")) for a in w.get("author", [])]
        return [
            {
                "source": "crossref",
                "id": w.get("DOI", record_id),
                "title": (w.get("title") or [""])[0],
                "url": w.get("URL"),
                "doi": w.get("DOI", ""),
                "published": (w.get("published", {}) or {}).get("date-parts", [[""]])[0],
                "authors": ", ".join(a for a in authors if a),
                "snippet": (w.get("abstract") or "")[:240],
                "raw": json.dumps(w),
            }
        ]
# ────────────────────────────────────────────────────────────────
# Phase 2 adapters continued (scholarly / library / financial / tech)
# ────────────────────────────────────────────────────────────────


def _rate_state_from_headers(headers, default_rps=None):
    """Build a :class:`RateState` from ``X-RateLimit-*`` style headers.

    Handles both the ``X-RateLimit-*`` (GitHub) and ``X-Rate-Limit-*``
    (Crossref) families. Missing fields fall back to ``default_rps`` /
    ``None`` so the budget report stays honest rather than guessing.
    """

    def _get(*names):
        for n in names:
            v = headers.get(n)
            if v is not None:
                return v
        return None

    remaining = _get("X-RateLimit-Remaining", "X-Rate-Limit-Remaining")
    limit = _get("X-RateLimit-Limit", "X-Rate-Limit-Limit")
    reset = _get("X-RateLimit-Reset", "X-Rate-Limit-Reset")
    retry_after = _get("Retry-After")
    state = RateState(retry_after=float(retry_after) if retry_after else None)
    if limit is not None:
        try:
            state.rps = float(limit)
        except ValueError:
            state.rps = default_rps
    elif default_rps is not None:
        state.rps = default_rps
    if remaining is not None:
        try:
            state.remaining = int(remaining)
        except ValueError:
            pass
    if reset is not None:
        try:
            # Header may be "remaining seconds" (Crossref) or an epoch
            # unix timestamp (GitHub); the coordinator only reads it for
            # budget reports.
            state.reset_seconds = int(reset)
        except ValueError:
            pass
    return state


# ────────────────────────────────────────────────────────────────
# arXiv Atom (1.0) helpers
# ────────────────────────────────────────────────────────────────
# arXiv's API (https://info.arxiv.org/help/api/user-manual.html) answers
# http://export.arxiv.org/api/query with an Atom 1.0 feed -- not JSON. The
# default namespace is Atom; arXiv-specific metadata lives in the "arxiv"
# namespace (http://arxiv.org/schemas/atom). ElementTree tags carry the
# namespace as a {uri}localname prefix, so helpers match on the local name.
_ARXIV_ATOM_NS_SUFFIX = "schemas/atom"


def _local(tag: str) -> str:
    """Strip the ``{namespace}`` prefix from an ElementTree tag name."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _clean_ws(text) -> str:
    """Collapse runs of whitespace (incl. the feed's line breaks) to one space."""
    return " ".join((text or "").split())


def _entry_field(entry, name: str) -> str:
    """Text of the first direct child of ``entry`` whose local name is ``name``."""
    for child in entry:
        if _local(child.tag) == name:
            return _clean_ws(child.text)
    return ""


def _entry_children(entry, name: str):
    return [c for c in entry if _local(c.tag) == name]


def _bare_arxiv_id(value: str) -> str:
    """Reduce an arXiv id or abs URL to the bare identifier (e.g. 1234.5678v2)."""
    ident = str(value or "")
    for sep in ("arxiv.org/abs/", "arxiv.org/abs", "arxiv.org/"):
        if sep in ident:
            ident = ident.split(sep, 1)[1]
    return ident.strip("/")


def _parse_arxiv_entry(entry) -> Dict[str, str]:
    """Map one Atom ``<entry>`` to the unified record shape."""
    entry_id = _entry_field(entry, "id")
    bare_id = _bare_arxiv_id(entry_id)
    # DOI: prefer the <arxiv:doi> extension, else the rel=related title=doi link.
    doi = _entry_field(entry, "doi")
    if not doi:
        for link in _entry_children(entry, "link"):
            if link.get("title") == "doi":
                href = link.get("href", "") or ""
                doi = href.split("doi.org/", 1)[-1].strip() if "doi.org/" in href else href.strip()
                break
    # Primary category: <arxiv:primary_category>, else the arxiv-scheme category.
    primary = ""
    for pc in _entry_children(entry, "primary_category"):
        primary = pc.get("term", "")
        if primary:
            break
    if not primary:
        for cat in _entry_children(entry, "category"):
            if (cat.get("scheme", "") or "").endswith(_ARXIV_ATOM_NS_SUFFIX):
                primary = cat.get("term", "")
                break
    authors = ", ".join(
        a
        for a in (
            _entry_field(child, "name") for child in _entry_children(entry, "author")
        )
        if a
    )
    return {
        "source": "arxiv",
        "id": bare_id,
        "title": _entry_field(entry, "title"),
        "url": entry_id or bare_id,
        "doi": doi,
        "published": _entry_field(entry, "published"),
        "authors": authors,
        "snippet": _clean_ws(_entry_field(entry, "summary"))[:240],
        "fields": {"arxiv": {"primary_category": primary}},
        "raw": ET.tostring(entry, encoding="unicode"),
    }


class ArxivAdapter(ResourceAdapter):
    """arXiv preprint search via the documented Atom API.

    Keyless. Answers at ``http://export.arxiv.org/api/query`` with an Atom
    1.0 feed (not JSON). Responsible-use ceiling is 1 request / 3 s on a
    single connection; the documented hard cap is 30k results/query, sliced
    in <=2k. arXiv asks callers to identify themselves with a contact-bearing
    User-Agent (part of their acceptable-use expectation), so every request
    carries one.
    """

    name = "arxiv"
    domain = "scholarly"
    requires_key = False
    BASE = "http://export.arxiv.org/api/query"
    _ARXIV_UA = "stitch-web-researcher/0.5.1 (mailto:researcher@example.org)"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
    ):
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=3.0, jitter=0.5)
        )

    def _get(self, params: Dict[str, object]) -> str:
        """Enforce politeness and GET the Atom feed text."""
        self._enforce_delay()
        resp = httpx.get(
            self.BASE,
            params=params,
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": self._ARXIV_UA},
        )
        resp.raise_for_status()
        return resp.text

    def _search_impl(self, query, max_results=5):
        resp_xml = self._get(
            {"search_query": query, "start": 0, "max_results": min(max_results, 100)}
        )
        root = ET.fromstring(resp_xml)
        out: List[Dict[str, str]] = []
        for entry in _entry_children(root, "entry"):
            out.append(_parse_arxiv_entry(entry))
            if len(out) >= max_results:
                break
        return out

    def fetch(self, record_id, params=None):
        # id_list is the version-safe documented way to fetch specific papers.
        ident = _bare_arxiv_id(record_id)
        resp_xml = self._get({"id_list": ident, "start": 0, "max_results": 1})
        root = ET.fromstring(resp_xml)
        entries = _entry_children(root, "entry")
        # A valid entry's <id> contains 'abs/'; a bad id yields an error feed
        # (a single entry with a query id and an 'Error' summary).
        if not entries or "abs/" not in _entry_field(entries[0], "id"):
            summary = _entry_field(entries[0], "summary") if entries else ""
            return [
                {
                    "source": "arxiv",
                    "id": ident,
                    "title": "",
                    "url": record_id,
                    "doi": "",
                    "published": "",
                    "authors": "",
                    "snippet": summary,
                    "fields": {"arxiv": {"primary_category": ""}},
                    "raw": "",
                }
            ]
        return [_parse_arxiv_entry(entries[0])]


class WorldBankAdapter(ResourceAdapter):
    """World Bank indicators (time-series) data (https://api.worldbank.org/v2).

    Keyless and generous (no published hard limit). The canonical data
    endpoint is ``/v2/country/{country}/indicator/{code}`` (country
    ``all`` for world totals). The old keyword ``/v2/search`` endpoint was
    retired by the World Bank, so ``search`` returns a single structured
    note; use ``fetch(series_code)`` for data.
    """

    name = "worldbank"
    domain = "financial"
    requires_key = False
    BASE = "https://api.worldbank.org/v2"
    # Country-all data endpoint: returns [pagination, [data points, ...]].
    DATA = "https://api.worldbank.org/v2/country/all/indicator"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
    ):
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.5, jitter=0.25)
        )

    def _search_impl(self, query, max_results=5):
        # /v2/search was retired (2026); keyword search is unavailable.
        return [
            {
                "source": "worldbank",
                "id": "",
                "title": "World Bank keyword search unavailable",
                "url": "",
                "snippet": (
                    "The World Bank /v2/search endpoint was retired. Use "
                    "fetch(series_code) for time-series data (e.g. SP.POP.TOTL)."
                ),
                "raw": json.dumps({"search_unavailable": True, "query": query}),
            }
        ]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        p = {"format": "json", "per_page": 20}
        if params:
            p.update({k: v for k, v in params.items() if v is not None})
        resp = httpx.get(f"{self.DATA}/{record_id}", params=p, timeout=20.0)
        resp.raise_for_status()
        payload = resp.json()
        # Data endpoint returns [pagination, [data points, ...]].
        points = (
            payload[1]
            if isinstance(payload, list) and len(payload) > 1 and isinstance(payload[1], list)
            else []
        )
        first = points[0] if points else {}
        indicator = (first or {}).get("indicator", {}) if isinstance(first, dict) else {}
        series_id = indicator.get("id") or record_id
        title = indicator.get("value") or record_id
        recent = ", ".join(
            f"{pt.get('date', '')}:{pt.get('value', '')}"
            for pt in points
            if isinstance(pt, dict) and pt.get("value") is not None
        )[:200]
        pagination = payload[0] if isinstance(payload, list) and payload else {}
        return [
            {
                "source": "worldbank",
                "id": series_id,
                "title": title,
                "url": f"{self.DATA}/{record_id}",
                "snippet": f"{len(points)} observations; recent: {recent}",
                "fields": {
                    "worldbank": {"observations": points, "pagination": pagination}
                },
                "raw": json.dumps(payload),
            }
        ]


class FredAdapter(ResourceAdapter):
    """FRED macro time-series data (https://fred.stlouisfed.org/docs/api/).

    Keyless (a free key is recommended). Series data is served as CSV-ish
    JSON; ``search`` treats the query as a series id (FRED has no public
    series-search REST endpoint).
    """

    name = "fred"
    domain = "financial"
    requires_key = False
    BASE = "https://api.fred.stlouisfed.org/graph/series_data"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        api_key: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("STITCH_FRED_KEY", "")
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.5, jitter=0.25)
        )

    def inject_auth(self, url, params=None, headers=None):
        p = dict(params or {})
        if self.api_key:
            p.setdefault("api_key", self.api_key)
        return url, p, dict(headers or {})

    def _search_impl(self, query, max_results=5):
        # No public search: resolve the query as a series id and return its
        # data points (best effort).
        return self.fetch(query)

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}?series_id={record_id}", params, {}
        )
        resp = httpx.get(url, params=params, headers=headers, timeout=20.0)
        resp.raise_for_status()
        data = resp.json()
        obs = data.get("observation", [])
        points = [f"{o.get('date', '')}={o.get('value', '')}" for o in obs[-10:]]
        return [
            {
                "source": "fred",
                "id": record_id,
                "title": f"FRED series {record_id}",
                "url": f"https://fred.stlouisfed.org/series/{record_id}",
                "snippet": f"{len(obs)} observations; last: {points[-1] if points else 'n/a'}",
                "fields": {"fred": {"observations": obs[-50:]}},
                "raw": json.dumps(data),
            }
        ]


class GitHubAdapter(ResourceAdapter):
    """GitHub code / repository search (https://docs.github.com/rest).

    Keyless (60 requests/hr) or with ``STITCH_GITHUB_TOKEN`` (5,000/hr).
    Exposes ``X-RateLimit-*`` headers; :meth:`parse_headers` retunes.
    """

    name = "github"
    domain = "tech"
    requires_key = False
    BASE = "https://api.github.com"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        api_key: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("STITCH_GITHUB_TOKEN", "")
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.1, jitter=0.05)
        )

    def inject_auth(self, url, params=None, headers=None):
        h = dict(headers or {})
        h.setdefault("Accept", "application/vnd.github+json")
        if self.api_key:
            h.setdefault("Authorization", f"Bearer {self.api_key}")
        return url, dict(params or {}), h

    def parse_headers(self, status, headers):
        return _rate_state_from_headers(headers, default_rps=50.0)

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/search/repositories",
            {"q": query, "per_page": min(max_results, 60)},
            {},
        )
        resp = httpx.get(url, params=params, headers=headers, timeout=20.0)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        out: List[Dict[str, str]] = []
        for r in items[:max_results]:
            out.append(
                {
                    "source": "github",
                    "id": str(r.get("id", "")),
                    "title": r.get("full_name", ""),
                    "url": r.get("html_url") or r.get("url"),
                    "snippet": (r.get("description") or "")[:240],
                    "fields": {
                        "github": {
                            "language": r.get("language"),
                            "stars": r.get("stargazers_count"),
                        }
                    },
                    "raw": json.dumps(r),
                }
            )
        return out

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        # record_id is "owner/repo".
        url, params, headers = self.inject_auth(
            f"{self.BASE}/repos/{record_id}", params, {}
        )
        resp = httpx.get(url, params=params, headers=headers, timeout=20.0)
        resp.raise_for_status()
        repo = resp.json()
        return [
            {
                "source": "github",
                "id": str(repo.get("id", record_id)),
                "title": repo.get("full_name", record_id),
                "url": repo.get("html_url") or "",
                "snippet": (repo.get("description") or "")[:240],
                "fields": {
                    "github": {
                        "language": repo.get("language"),
                        "stars": repo.get("stargazers_count"),
                    }
                },
                "raw": json.dumps(repo),
            }
        ]


class OpenLibraryAdapter(ResourceAdapter):
    """Open Library book search / lookup (https://openlibrary.org).

    Keyless, 1 rps default (x3 with a descriptive UA + email).
    """

    name = "openlibrary"
    domain = "library"
    requires_key = False
    BASE = "https://openlibrary.org"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        email: Optional[str] = None,
    ):
        self.email = email or "research@example.org"
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=1.0, jitter=0.5)
        )

    def inject_auth(self, url, params=None, headers=None):
        h = dict(headers or {})
        h.setdefault("User-Agent", f"{_UA}?email={self.email}")
        return url, dict(params or {}), h

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        resp = httpx.get(
            f"{self.BASE}/search.json",
            params={
                "q": query,
                "limit": min(max_results, 20),
                "fields": "title,author,publicyear,isbn,key,first_publish_year",
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        docs = resp.json().get("docs", [])
        out: List[Dict[str, str]] = []
        for d in docs[:max_results]:
            key = d.get("key", "")
            out.append(
                {
                    "source": "openlibrary",
                    "id": key,
                    "title": d.get("title", ""),
                    "url": f"{self.BASE}{key}" if key else "",
                    "published": d.get("first_publish_year", ""),
                    "authors": ", ".join(d.get("author", []) or []),
                    "snippet": ", ".join(d.get("isbn", []) or [])[:120],
                    "raw": json.dumps(d),
                }
            )
        return out

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        # Search may hand back a /works/ key; keep whichever prefix we were given.
        key = record_id
        if not key.startswith(("/books/", "/works/")):
            key = f"/books/{record_id}"
        resp = httpx.get(f"{self.BASE}{key}.json", timeout=20.0)
        resp.raise_for_status()
        book = resp.json()
        key = book.get("key", key)
        # ``authors`` shape differs between the edition (/books) and work (/works)
        # endpoints: [{name}], [{author:{key}}], or plain strings.
        authors = []
        for a in book.get("authors", []) or []:
            if isinstance(a, str):
                authors.append(a)
            elif isinstance(a, dict):
                if "name" in a:
                    authors.append(a["name"])
                elif isinstance(a.get("author"), dict):
                    authors.append(a["author"].get("key", ""))
        return [
            {
                "source": "openlibrary",
                "id": key,
                "title": book.get("title", ""),
                "url": f"{self.BASE}{key}",
                "published": (
                    book.get("first_publish_year")
                    or book.get("first_publish_date")
                    or book.get("publish_date")
                    or ""
                ),
                "authors": ", ".join(a for a in authors if a),
                "raw": json.dumps(book),
            }
        ]


class DoajAdapter(ResourceAdapter):
    """DOAJ open-access journals / articles search (https://doaj.org/api).

    Keyless, 2 rps (burst up to 5 queued). Search-only.
    """

    name = "doaj"
    domain = "scholarly"
    requires_key = False
    BASE = "https://doaj.org/api"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
    ):
        self._last_search = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.5, jitter=0.25)
        )

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        # v1 search takes the query as a path segment; results nest metadata
        # under ``bibjson`` (title, author, identifier[]) rather than top-level.
        resp = httpx.get(
            f"{self.BASE}/search/articles/{query}",
            params={"size": min(max_results, 100), "page": 1},
            timeout=20.0,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        out: List[Dict[str, str]] = []
        for r in results[:max_results]:
            bib = r.get("bibjson", {}) or {}
            dois = [
                i.get("id")
                for i in bib.get("identifier", [])
                if isinstance(i, dict) and i.get("type") == "doi"
            ]
            authors = bib.get("author", []) if isinstance(bib.get("author"), list) else []
            out.append(
                {
                    "source": "doaj",
                    "id": r.get("id", ""),
                    "title": (bib.get("title") or ""),
                    "url": f"https://doaj.org/article/{r.get('id', '')}",
                    "doi": dois[0] if dois else "",
                    "authors": ", ".join(a.get("name", "") if isinstance(a, dict) else str(a) for a in authors),
                    "snippet": (bib.get("abstract") or "")[:240],
                    "raw": json.dumps(r),
                }
            )
        return out


class PubmedAdapter(ResourceAdapter):
    """PubMed / NCBI E-utilities search + fetch (eutils.ncbi.nlm.nih.gov).

    Keyless (3 rps) or with ``STITCH_NCBC_KEY`` (10 rps). Send ``email`` —
    NCBI requests it for abuse tracking. Keyless abuse triggers IP blocks.
    """

    name = "pubmed"
    domain = "scholarly"
    requires_key = False
    SEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    FETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        email: Optional[str] = None,
        api_key: Optional[str] = None,
        db: str = "pubmed",
    ):
        self.email = email or "research@example.org"
        self.api_key = api_key or os.environ.get("STITCH_NCBC_KEY", "")
        self.db = db
        self._last_search = 0.0
        self._last_fetch = 0.0
        # 3 rps keyless (with a generous daily safety), 10 rps with key.
        self._init_rate_limit(
            delay
            if delay is not None
            else RateLimit(
                search_interval=0.33,
                jitter=0.1,
                quota=None if self.api_key else 600,
                quota_window="day",
            )
        )

    def inject_auth(self, url, params=None, headers=None):
        p = dict(params or {})
        p.setdefault("email", self.email)
        p.setdefault("tool", "stitch-web-researcher")
        if self.api_key:
            p.setdefault("api_key", self.api_key)
        return url, p, dict(headers or {})

    def parse_headers(self, status, headers):
        return _rate_state_from_headers(headers, default_rps=10.0)

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            self.SEARCH,
            {"db": self.db, "term": query, "retmax": max_results, "retmode": "json"},
            {},
        )
        resp = httpx.get(url, params=params, headers=headers, timeout=20.0)
        resp.raise_for_status()
        ids = resp.json()["esearchresult"].get("idlist", [])
        return [
            {
                "source": "pubmed",
                "id": uid,
                "title": "",
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
                "snippet": f"PMID {uid}",
                "raw": json.dumps({"uid": uid}),
            }
            for uid in ids[:max_results]
        ]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        ids = record_id if isinstance(record_id, str) and "," in record_id else str(record_id)
        # efetch's JSON mode is unreliable for retrieval, so parse the stable
        # Atom-free XML form: PubmedArticleSet -> PubmedArticle -> MedlineCitation.
        url, params, headers = self.inject_auth(
            self.FETCH, {"db": self.db, "id": ids, "retmode": "xml"}, {}
        )
        resp = httpx.get(url, params=params, headers=headers, timeout=20.0)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        entry = root.find("PubmedArticle")
        if entry is None:
            return [{"source": "pubmed", "id": str(record_id), "title": "", "raw": resp.text}]
        mc = entry.find("MedlineCitation")
        uid = mc.findtext("PMID") or str(record_id)
        article = mc.find("Article")
        title = " ".join((article.findtext("ArticleTitle") or "").split())
        authors: List[str] = []
        alist = article.find("AuthorList") if article is not None else None
        if alist is not None:
            for a in alist.findall("Author"):
                name = " ".join(x for x in (a.findtext("LastName"), a.findtext("ForeName")) if x).strip()
                if name:
                    authors.append(name)
        return [
            {
                "source": "pubmed",
                "id": uid,
                "title": title,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
                "snippet": title[:240],
                "authors": ", ".join(authors),
                "raw": resp.text,
            }
        ]


# ── Phase 3 adapters ──────────────────────────────────────────────────────
# Domain waves (legal / science / financial / geo / tech) from the §4 matrix.
# Each owns only request + parse; politeness/quota/auth/retry come from the
# base class. Endpoints verified against the plan's §4 matrix (2026-08-31).


def _today_iso() -> str:
    """YYYY-MM-DD for date-indexed endpoints (e.g. NASA NeoWs)."""
    return _date.today().isoformat()


def _first_desc(cve: dict, limit: int = 240) -> str:
    """First English description string of a CVE doc, collapsed + truncated."""
    for d in cve.get("descriptions", []) or []:
        if d.get("lang") == "en" or not d.get("lang"):
            return " ".join((d.get("value") or "").split())[:limit]
    return ""


def _parse_census_query(query) -> Tuple[str, dict]:
    """Split a Census spec into ``(dataset, extra_params)``.

    Accepts a dict (``{"dataset": "2019/acs/acs1", "get": ..., "for": ...}``) or
    a string (``"2019/acs/acs1?get=B01003_001E&for=state:*"``).
    """
    if isinstance(query, dict):
        dataset = query.get("dataset", "")
        extra = {k: v for k, v in query.items() if k != "dataset"}
        return dataset, extra
    qs = str(query or "").strip()
    dataset = ""
    extra = {}
    if "?" in qs:
        dataset, _, qstr = qs.partition("?")
        extra = {k: v[0] for k, v in parse_qs(qstr).items()}
    return dataset, extra


class NASAAdapter(ResourceAdapter):
    """NASA Near-Earth Object Web Service (NeoWs) — https://api.nasa.gov.

    Keyless with ``DEMO_KEY`` (30 req/hr, 50 req/day) or a real key via
    ``STITCH_NASA_KEY``. NeoWs is date-indexed, so :meth:`search` treats the
    query as a date (``YYYY-MM-DD``) and returns the near-Earth objects for that
    date (defaulting to today when empty); :meth:`fetch` looks up a single
    object by its NASA JPL ``neo_reference_id``.
    """

    name = "nasa"
    domain = "geo"
    requires_key = False
    BASE = "https://api.nasa.gov"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        api_key: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("STITCH_NASA_KEY", "DEMO_KEY")
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=2.0, jitter=0.5)
        )

    def inject_auth(self, url, params=None, headers=None):
        p = dict(params or {})
        p.setdefault("api_key", self.api_key)
        return url, p, dict(headers or {})

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        start = (query or "").strip() or _today_iso()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/neo/ws/near_earth_date",
            {"start_date": start, "end_date": start},
            {},
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        objects = resp.json().get("near_earth_objects", {}).get(start, [])
        out: List[Dict[str, str]] = []
        for neo in objects[:max_results]:
            ca = (neo.get("close_approach_data") or [{}])[0]
            diam = (neo.get("estimated_diameter", {}) or {}).get("meters", {}) or {}
            out.append(
                {
                    "source": "nasa",
                    "id": neo.get("neo_reference_id", ""),
                    "title": neo.get("object_name", ""),
                    "url": neo.get("nasa_jpl_url", ""),
                    "published": ca.get("close_approach_date", ""),
                    "snippet": (
                        f"~{diam.get('estimated_diameter_max', '')} m diameter; "
                        f"{ca.get('closest_approach_distance', {}).get('kilometers', '')} km closest"
                    ),
                    "fields": {
                        "nasa": {
                            "object_type": neo.get("object_type", ""),
                            "is_hazardous": neo.get("is_hazardous", ""),
                            "absolute_magnitude": neo.get("absolute_magnitude", ""),
                        }
                    },
                    "raw": json.dumps(neo),
                }
            )
        return out

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/neo/ws/neo/{record_id}", params, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        neo = (
            resp.json().get("near_earth_objects", {}).get("_single", [{}])[0]
        )
        diam = (neo.get("estimated_diameter", {}) or {}).get("meters", {}) or {}
        return [
            {
                "source": "nasa",
                "id": neo.get("neo_reference_id", record_id),
                "title": neo.get("object_name", record_id),
                "url": neo.get("nasa_jpl_url", ""),
                "snippet": (
                    f"~{diam.get('estimated_diameter_max', '')} m diameter"
                ),
                "fields": {
                    "nasa": {
                        "object_type": neo.get("object_type", ""),
                        "is_hazardous": neo.get("is_hazardous", ""),
                    }
                },
                "raw": json.dumps(neo),
            }
        ]


class NvdAdapter(ResourceAdapter):
    """NIST National Vulnerability Database (v2 JSON API) — https://nvd.nist.gov.

    Keyless (5 req / 30 s) or with ``STITCH_NVD_API_KEY`` (50 req / 30 s). The
    v2 JSON endpoints require a ``jsonp=`` callback to return JSON; the key is
    sent as ``apikey=``. A CVE-id shaped query hits the indexed ``cveId`` field,
    otherwise it is a full-text ``searchQuery``.
    """

    name = "nvd"
    domain = "tech"
    requires_key = False
    BASE = "https://nvd.nist.gov/api/v2"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        api_key: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("STITCH_NVD_API_KEY", "")
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=6.0, jitter=1.0)
        )

    def inject_auth(self, url, params=None, headers=None):
        p = dict(params or {})
        # v2 JSON endpoints need a JSONP callback name to return JSON.
        p.setdefault("jsonp", "jsonCallback")
        if self.api_key:
            p["apikey"] = self.api_key
        return url, p, dict(headers or {})

    def parse_headers(self, status, headers):
        return _rate_state_from_headers(headers, default_rps=50.0)

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        q = (query or "").strip()
        if re.match(r"^[A-Za-z]{3,4}-\d{4,4}-\d+$", q, re.IGNORECASE):
            key, val = "cveId", q
        else:
            key, val = "searchQuery", q
        url, params, headers = self.inject_auth(
            f"{self.BASE}/vulnerabilities/search", {key: val, "pageSize": min(max_results, 100)}, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        items = resp.json().get("vulnerabilityJsonDocument", [])
        out: List[Dict[str, str]] = []
        for item in items[:max_results]:
            cve = item.get("cve", {})
            meta = cve.get("cveMetadata", {}) or {}
            cvss = meta.get("cvssData", {}) or {}
            out.append(
                {
                    "source": "nvd",
                    "id": cve.get("id", ""),
                    "title": cve.get("id", ""),
                    "url": f"https://nvd.nist.gov/vuln/detail/{cve.get('id', '')}",
                    "snippet": _first_desc(cve),
                    "fields": {
                        "nvd": {
                            "severity": cvss.get("severity", cve.get("severity", "")),
                            "base_score": cvss.get("baseScore", ""),
                            "vector": cvss.get("vectorString", ""),
                        }
                    },
                    "raw": json.dumps(cve),
                }
            )
        return out

    def fetch(self, record_id, params=None):
        # record_id is a CVE id, e.g. CVE-2021-44228.
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/vulnerabilities/search", {"cveId": record_id}, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        items = resp.json().get("vulnerabilityJsonDocument", [])
        if not items:
            return []
        cve = items[0].get("cve", {})
        meta = cve.get("cveMetadata", {}) or {}
        cvss = meta.get("cvssData", {}) or {}
        return [
            {
                "source": "nvd",
                "id": cve.get("id", record_id),
                "title": cve.get("id", record_id),
                "url": f"https://nvd.nist.gov/vuln/detail/{cve.get('id', record_id)}",
                "snippet": _first_desc(cve),
                "fields": {
                    "nvd": {
                        "severity": cvss.get("severity", cve.get("severity", "")),
                        "base_score": cvss.get("baseScore", ""),
                        "vector": cvss.get("vectorString", ""),
                    }
                },
                "raw": json.dumps(cve),
            }
        ]


class ZenodoAdapter(ResourceAdapter):
    """Zenodo research-records search / lookup — https://zenodo.org/api.

    Keyless, or ``STITCH_ZENODO_TOKEN`` for a higher rate. Records are searched
    via ``/records/search`` and fetched via ``/record/<id>``.
    """

    name = "zenodo"
    domain = "scholarly"
    requires_key = False
    BASE = "https://zenodo.org/api"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        api_key: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("STITCH_ZENODO_TOKEN", "")
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=1.0, jitter=0.5)
        )

    def inject_auth(self, url, params=None, headers=None):
        p = dict(params or {})
        if self.api_key:
            p["access_token"] = self.api_key
        return url, p, dict(headers or {})

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/records/search",
            {"q": query, "pagesize": min(max_results, 100), "page": 1},
            {},
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
        out: List[Dict[str, str]] = []
        for h in hits[:max_results]:
            m = h.get("metadata", {})
            out.append(
                {
                    "source": "zenodo",
                    "id": h.get("_id", ""),
                    "title": m.get("title", ""),
                    "url": f"{self.BASE}/record/{h.get('_id', '')}",
                    "published": m.get("publication_date", ""),
                    "authors": ", ".join(
                        a.get("name", "") if isinstance(a, dict) else str(a)
                        for a in (m.get("authors", []) or [])
                    ),
                    "snippet": _strip_tags(m.get("description", ""))[:240],
                    "fields": {
                        "zenodo": {
                            "resource_type": m.get("resource_type", ""),
                            "open_access": bool(
                                (m.get("open_access") or {}).get("status") == "t"
                            ),
                        }
                    },
                    "raw": json.dumps(h),
                }
            )
        return out

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/record/{record_id}", params, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        rec = resp.json()
        m = rec.get("metadata", {})
        authors = []
        for a in m.get("authors", []) or []:
            if isinstance(a, dict):
                authors.append(a.get("name", ""))
            elif a:
                authors.append(str(a))
        return [
            {
                "source": "zenodo",
                "id": rec.get("_id", record_id),
                "title": m.get("title", record_id),
                "url": f"{self.BASE}/record/{rec.get('_id', record_id)}",
                "published": m.get("publication_date", ""),
                "authors": ", ".join(authors),
                "raw": json.dumps(rec),
            }
        ]


class SoftwareHeritageAdapter(ResourceAdapter):
    """Software Heritage source-archive search — https://archive.softwareheritage.org.

    Keyless (client auto-paces to server hints). Search hits are project /
    repository / release / commit descriptors keyed by SWEET ids.
    """

    name = "softwareheritage"
    domain = "tech"
    requires_key = False
    BASE = "https://archive.softwareheritage.org/api/v1"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
    ):
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=1.0, jitter=0.5)
        )

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        resp = httpx.get(
            f"{self.BASE}/search/", params={"q": query}, timeout=20.0
        )
        resp.raise_for_status()
        matches = resp.json().get("matches", [])
        out: List[Dict[str, str]] = []
        for m in matches[:max_results]:
            out.append(
                {
                    "source": "softwareheritage",
                    "id": m.get("id", ""),
                    "title": m.get("label", m.get("id", "")),
                    "url": m.get("url", ""),
                    "snippet": m.get("label", ""),
                    "fields": {
                        "softwareheritage": {"type": m.get("type", "")}
                    },
                    "raw": json.dumps(m),
                }
            )
        return out

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        # Accept a SWEET id (s:/d:/p:/r:...) or a full SWEET URL.
        sid = record_id
        if sid.startswith("https://archive.softwareheritage.org/"):
            m = re.search(r"/(s|d|p|r):([^/?]+)", sid)
            if m:
                sid = f"{m.group(1)}:{m.group(2)}"
        url = f"{self.BASE}/source/sid/{sid}"
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        src = resp.json()
        meta = src.get("meta", {}) or {}
        return [
            {
                "source": "softwareheritage",
                "id": src.get("id", record_id),
                "title": meta.get("name", src.get("id", record_id)),
                "url": f"https://archive.softwareheritage.org/{src.get('id', record_id)}",
                "published": meta.get("date", ""),
                "snippet": meta.get("description", "")[:240],
                "fields": {"softwareheritage": {"type": src.get("type", "")}},
                "raw": json.dumps(src),
            }
        ]


class CongressAdapter(ResourceAdapter):
    """Congress.gov legislative data via api.data.gov — https://api.data.gov/congress.

    Requires ``STITCH_CONGRESS_KEY`` (data.gov key; 5,000 calls/hr). Members are
    searched via ``/members/search`` and fetched via ``/members/<cgi_id>``.
    """

    name = "congress"
    domain = "legal"
    requires_key = True
    BASE = "https://api.data.gov/congress/v1"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        api_key: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("STITCH_CONGRESS_KEY", "")
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.1, jitter=0.02)
        )

    def inject_auth(self, url, params=None, headers=None):
        p = dict(params or {})
        p["api_key"] = self.api_key
        return url, p, dict(headers or {})

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/members/search",
            {"q": query, "api_key": self.api_key, "limit": min(max_results, 50)},
            {},
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        out: List[Dict[str, str]] = []
        for r in results[:max_results]:
            loc = r.get("state", "") or ""
            if r.get("chamber") == "Senate" and not loc:
                loc = r.get("state", "")
            out.append(
                {
                    "source": "congress",
                    "id": r.get("cgi_id", ""),
                    "title": r.get("display_name", ""),
                    "url": r.get("url", ""),
                    "snippet": (
                        f"{r.get('title', '')} {r.get('party', '')} — "
                        f"{r.get('state', '')}{r.get('district', '')}"
                    ),
                    "fields": {
                        "congress": {
                            "chamber": r.get("chamber", ""),
                            "party": r.get("party", ""),
                            "state": r.get("state", ""),
                        }
                    },
                    "raw": json.dumps(r),
                }
            )
        return out

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/members/{record_id}", {"api_key": self.api_key}, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        r = resp.json()
        return [
            {
                "source": "congress",
                "id": r.get("cgi_id", record_id),
                "title": r.get("display_name", record_id),
                "url": r.get("url", ""),
                "snippet": (
                    f"{r.get('title', '')} {r.get('party', '')} — "
                    f"{r.get('state', '')}{r.get('district', '')}"
                ),
                "fields": {
                    "congress": {
                        "chamber": r.get("chamber", ""),
                        "party": r.get("party", ""),
                        "state": r.get("state", ""),
                    }
                },
                "raw": json.dumps(r),
            }
        ]


class YahooFinanceAdapter(ResourceAdapter):
    """Yahoo Finance quote / chart data via the unofficial v1 / v8 endpoints.

    Unofficial and ToS-gray (no public docs, throttled) — surface with care.
    ``search`` looks up quotes by symbol/name via ``/v1/finance/search``;
    ``fetch`` pulls chart metadata by symbol via ``/v8/finance/chart``.
    """

    name = "yahoo"
    domain = "financial"
    requires_key = False
    BASE = "https://query2.finance.yahoo.com"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
    ):
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.5, jitter=0.25)
        )

    def inject_auth(self, url, params=None, headers=None):
        h = dict(headers or {})
        h.setdefault("User-Agent", _UA)
        return url, dict(params or {}), h

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/v1/finance/search",
            {"q": query, "quotesCount": min(max_results, 20)},
            {},
        )
        resp = httpx.get(url, headers=headers, params=params, timeout=20.0)
        resp.raise_for_status()
        quotes = (
            (resp.json().get("quoteCollection", {}) or {}).get("quotes", []) or []
        )
        out: List[Dict[str, str]] = []
        for q in quotes[:max_results]:
            out.append(
                {
                    "source": "yahoo",
                    "id": q.get("symbol", ""),
                    "title": q.get("shortName") or q.get("symbol", ""),
                    "url": f"https://finance.yahoo.com/quote/{q.get('symbol', '')}",
                    "snippet": (
                        f"{q.get('shortName', '')} — {q.get('exchange', '')} "
                        f"{q.get('quoteType', '')}"
                    ),
                    "fields": {
                        "yahoo": {
                            "exchange": q.get("exchange", ""),
                            "quote_type": q.get("quoteType", ""),
                            "market_cap": q.get("marketCap", ""),
                        }
                    },
                    "raw": json.dumps(q),
                }
            )
        return out

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/v8/finance/chart/{record_id}", params, {}
        )
        resp = httpx.get(url, headers=headers, params=params, timeout=20.0)
        resp.raise_for_status()
        meta = (
            (resp.json().get("chart", {}) or {}).get("result", [{}])[0]
            .get("meta", {})
        )
        return [
            {
                "source": "yahoo",
                "id": meta.get("symbol", record_id),
                "title": meta.get("longName") or meta.get("shortName") or record_id,
                "url": f"https://finance.yahoo.com/quote/{meta.get('symbol', record_id)}",
                "snippet": (
                    f"{meta.get('regularMarketPrice', '')} {meta.get('currency', '')} "
                    f"({meta.get('fullExchangeName', '')})"
                ),
                "fields": {
                    "yahoo": {
                        "currency": meta.get("currency", ""),
                        "exchange": meta.get("fullExchangeName", ""),
                        "previous_close": meta.get("previousClose", ""),
                    }
                },
                "raw": json.dumps(meta),
            }
        ]


class OverpassAdapter(ResourceAdapter):
    """Overpass API geo queries for OpenStreetMap data — https://overpass-api.org.

    Keyless; no published hard limit (small requests are prioritised, so be
    polite). The query is an Overpass QL string sent URL-encoded to the
    interpreter endpoint with ``format=json``; results are OSM nodes / ways /
    relations with their tags.
    """

    name = "overpass"
    domain = "geo"
    requires_key = False
    BASE = "https://overpass-api.org/api/interpreter"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
    ):
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=2.0, jitter=1.0)
        )

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        q = (query or "").strip()
        if not q:
            raise ValueError("OverpassAdapter requires an Overpass QL query string")
        data = q if q.startswith("[out:") else f"[out:json]{q}"
        resp = httpx.get(
            self.BASE, params={"data": data, "format": "json"}, timeout=60.0
        )
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
        out: List[Dict[str, str]] = []
        for el in elements[:max_results]:
            tags = el.get("tags", {}) or {}
            name = tags.get("name", "")
            out.append(
                {
                    "source": "overpass",
                    "id": str(el.get("id", "")),
                    "title": name or f"{el.get('type', '')}:{el.get('id', '')}",
                    "url": (
                        f"https://www.openstreetmap.org/{el.get('type', '')}/"
                        f"{el.get('id', '')}"
                    ),
                    "snippet": ", ".join(
                        f"{k}={v}" for k, v in list(tags.items())[:6]
                    ),
                    "fields": {
                        "overpass": {
                            "type": el.get("type", ""),
                            "lat": el.get("lat", ""),
                            "lon": el.get("lon", ""),
                        }
                    },
                    "raw": json.dumps(el),
                }
            )
        return out


class CensusAdapter(ResourceAdapter):
    """US Census Bureau data API — https://api.census.gov.

    Requires ``STITCH_CENSUS_KEY`` (~5,000 req/day). The Census API is not a
    text search; :meth:`search` accepts a spec dict
    (``{"dataset": "2019/acs/acs1", "get": "B01003_001E", "for": "state:*"}``) or
    a ``"dataset?get=...&for=..."`` string and returns the decoded rows. The
    first response row is the variable-name header.
    """

    name = "census"
    domain = "geo"
    requires_key = True
    BASE = "https://api.census.gov/data"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        api_key: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("STITCH_CENSUS_KEY", "")
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.5, jitter=0.25)
        )

    def inject_auth(self, url, params=None, headers=None):
        p = dict(params or {})
        p["key"] = self.api_key
        return url, p, dict(headers or {})

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        dataset, extra = _parse_census_query(query)
        if not dataset:
            raise ValueError("CensusAdapter spec requires a 'dataset' (e.g. 2019/acs/acs1)")
        url, params, headers = self.inject_auth(
            f"{self.BASE}/{dataset}", {**extra, "limit": max_results}, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return []
        header = [h.lower().replace(" ", "_") for h in rows[0]]
        out: List[Dict[str, str]] = []
        for row in rows[1:][:max_results]:
            rec = {header[i]: row[i] for i in range(len(header)) if i < len(row)}
            key = rec.get("state", rec.get("geographic_unit", ""))
            out.append(
                {
                    "source": "census",
                    "id": str(key),
                    "title": ", ".join(f"{k}={v}" for k, v in list(rec.items())[:3]),
                    "url": f"https://data.census.gov/?g={dataset}",
                    "snippet": ", ".join(
                        f"{header[i]}={row[i]}" for i in range(1, len(header)) if i < len(row)
                    ),
                    "fields": {"census": {"dataset": dataset}},
                    "raw": json.dumps(rec),
                }
            )
        return out

    def fetch(self, record_id, params=None):
        # Lookup one geography row by id within a dataset spec.
        self._enforce_delay()
        dataset, extra = _parse_census_query(record_id)
        url, params, headers = self.inject_auth(
            f"{self.BASE}/{dataset}", {**extra, **dict(params or {})}, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return []
        header = [h.lower().replace(" ", "_") for h in rows[0]]
        for row in rows[1:]:
            rec = {header[i]: row[i] for i in range(len(header)) if i < len(row)}
            if str(rec.get("state", rec.get("geographic_unit", ""))) == str(record_id):
                return [
                    {
                        "source": "census",
                        "id": str(record_id),
                        "title": ", ".join(
                            f"{header[i]}={row[i]}" for i in range(1, len(header)) if i < len(row)
                        ),
                        "url": f"https://data.census.gov/?g={dataset}",
                        "snippet": rec.get("state", ""),
                        "fields": {"census": {"dataset": dataset}},
                        "raw": json.dumps(rec),
                    }
                ]
        return []


def _strip_tags(text: str) -> str:
    """Strip HTML tags from a description string (Zenodo descriptions are HTML)."""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", " ", text)


# ── Phase 3 (second wave): legal, science, financial ────────────────────

class CourtListenerAdapter(ResourceAdapter):
    """CourtListener court-opinion search — https://www.courtlistener.com.

    Keyless (1,000 req / hr, no auth) via the REST v4 API. ``search`` runs a
    CourtListener query-language string (free text, or ``caseName:"..."``,
    ``court:"scotus"``, ``dateFiled:>=2024-01-01``) against
    ``/api/rest/v4/search/``; ``fetch`` pulls one cluster by its ``cluster_id``.
    """

    name = "courtlistener"
    domain = "legal"
    requires_key = False
    BASE = "https://www.courtlistener.com/api/rest/v4"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
    ):
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.1, jitter=0.02)
        )

    def inject_auth(self, url, params=None, headers=None):
        h = dict(headers or {})
        h.setdefault("User-Agent", _UA)
        return url, dict(params or {}), h

    def _row(self, r):
        return {
            "source": "courtlistener",
            "id": str(r.get("cluster_id", "")),
            "title": r.get("caseName") or r.get("caseNameFull", ""),
            "url": f"https://www.courtlistener.com{r.get('absolute_url', '')}",
            "published": r.get("dateFiled", ""),
            "snippet": _strip_tags(
                (r.get("caseNameFull") or r.get("caseName") or ""))[:240],
            "fields": {
                "court": r.get("court", ""),
                "court_citation": r.get("court_citation_string", ""),
                "docket_number": r.get("docketNumber", ""),
                "neutral_cite": r.get("neutralCite", ""),
                "cite_count": r.get("citeCount", ""),
            },
            "raw": json.dumps(r),
        }

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        q = (query or "").strip()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/search/",
            {"q": q or "*", "per_page": min(max_results, 100), "format": "json"},
            {},
        )
        resp = httpx.get(url, headers=headers, params=params, timeout=20.0)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [self._row(r) for r in results[:max_results]]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/cluster/{record_id}/", params, {}
        )
        resp = httpx.get(url, headers=headers, params=params, timeout=20.0)
        resp.raise_for_status()
        return [self._row(resp.json())]


class EcfrAdapter(ResourceAdapter):
    """US Code of Federal Regulations (eCFR) lookup via GovInfo — https://www.govinfo.gov.

    Keyless. The eCFR REST API is citation-addressed rather than full-text:
    ``search`` and ``fetch`` both parse a citation (``"21 CFR 113"``,
    ``"21/113"``, ``"21.113"``) and return the corresponding CFR part / section
    body. ``record_id`` / ``query`` accepts ``"title"`` alone (whole title) or
    ``"title/part"``.
    """

    name = "ecfr"
    domain = "legal"
    requires_key = False
    BASE = "https://www.govinfo.gov/ecfr/rest/ecfr/json"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
    ):
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.5, jitter=0.1)
        )

    def _parse_citation(self, citation):
        # "21 CFR 113" / "21/113" / "21.113" / "113" -> (title, part)
        s = str(citation or "").strip()
        if "/" in s:
            title, _, part = s.partition("/")
        elif "CFR" in s.upper():
            parts = re.split(r"[,\s/]+", s)
            title = next((p for p in parts if p.isdigit()), "")
            part = next((p for p in parts if p.isdigit() and p != title), "")
        else:
            title, _, part = re.split(r"[\s./]+", s, 1) if ("." in s or " " in s) else (s, "", "")
        return title.strip(), part.strip()

    def _part(self, title, part):
        url = f"{self.BASE}/{title}/{part}" if part else f"{self.BASE}/{title}"
        resp = httpx.get(url, timeout=20.0)
        resp.raise_for_status()
        doc = resp.json()
        title_txt = doc.get("title_title", f"Title {title}")
        part_txt = doc.get("part_title", "")
        sections = doc.get("sections", {})
        if sections:
            first = next(iter(sections.values()))
            body = _strip_tags(first.get("content", ""))[:240]
            label = first.get("label", part)
        else:
            body = _strip_tags(doc.get("content", ""))[:240]
            label = doc.get("section_number", part)
        return {
            "source": "ecfr",
            "id": f"{title}/{part}",
            "title": f"{title_txt}" + (f": {part_txt}" if part_txt else ""),
            "url": f"https://www.ecfr.gov/public/current/title/{title}/part/{part}",
            "snippet": body,
            "fields": {
                "title_no": title,
                "part": part,
                "part_title": part_txt,
                "section_count": len(sections) if isinstance(sections, dict) else 0,
            },
            "raw": json.dumps(doc),
        }

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        title, part = self._parse_citation(query)
        if not part:
            # Whole title: return the first part as a representative row.
            url = f"{self.BASE}/{title}"
            resp = httpx.get(url, timeout=20.0)
            resp.raise_for_status()
            doc = resp.json()
            sections = doc.get("sections", {})
            first = next(iter(sections.values())) if isinstance(sections, dict) else {}
            return [{
                "source": "ecfr",
                "id": str(title),
                "title": doc.get("title_title", f"Title {title}"),
                "url": f"https://www.ecfr.gov/public/current/title/{title}",
                "snippet": _strip_tags(first.get("content", ""))[:240],
                "fields": {
                    "title_no": str(title),
                    "section_count": len(sections) if isinstance(sections, dict) else 0,
                },
                "raw": json.dumps(doc),
            }]
        return [self._part(title, part)]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        title, part = self._parse_citation(record_id)
        if not part:
            return self._search_impl(title)
        return [self._part(title, part)]


class FederalRegisterAdapter(ResourceAdapter):
    """US Federal Register documents via api.federalregister.gov.

    Requires ``STITCH_FEDREG_KEY`` (data.gov key; 5,000 calls / hr). Search is
    full-text over documents / notices via ``/v1/documents.json``; fetch pulls
    one document by its ``document_number``.
    """

    name = "federalregister"
    domain = "legal"
    requires_key = True
    BASE = "https://api.federalregister.gov/v1"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        api_key: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("STITCH_FEDREG_KEY", "")
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.25, jitter=0.05)
        )

    def inject_auth(self, url, params=None, headers=None):
        p = dict(params or {})
        p["api_key"] = self.api_key
        return url, p, dict(headers or {})

    def _doc(self, d):
        agency = d.get("agency", {}) or {}
        return {
            "source": "federalregister",
            "id": d.get("document_number", ""),
            "title": d.get("title", ""),
            "url": d.get("html_url", d.get("text_url", "")),
            "published": d.get("doc_date", ""),
            "snippet": _strip_tags(d.get("abstract", d.get("excerpt", "")))[:240],
            "fields": {
                "document_type": d.get("document_type", ""),
                "type": d.get("type", ""),
                "agency": agency.get("name", ""),
                "document_number": d.get("document_number", ""),
            },
            "raw": json.dumps(d),
        }

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/documents.json",
            {"q": query, "per_page": min(max_results, 100)},
            {},
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        docs = resp.json().get("documents", [])
        return [self._doc(d) for d in docs[:max_results]]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/documents/{record_id}.json", params, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        return [self._doc(resp.json())]


class EurlexAdapter(ResourceAdapter):
    """EUR-Lex (EU law) search — https://eur-lex.europa.eu.

    Keyless via the v3 JSON search endpoint (append ``format=json``). ``search``
    runs an EUR-Lex query string (free text, or ``COLLECTION="legis_prim"``);
    ``fetch`` looks up one document by its ``docid`` / CELEX / ELI.
    """

    name = "eurlex"
    domain = "legal"
    requires_key = False
    BASE = "https://eur-lex.europa.eu/search/api/v3"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
    ):
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.5, jitter=0.1)
        )

    def inject_auth(self, url, params=None, headers=None):
        h = dict(headers or {})
        h.setdefault("User-Agent", _UA)
        return url, dict(params or {}), h

    def _hit(self, h):
        return {
            "source": "eurlex",
            "id": h.get("docid", h.get("documentIdentifier", "")),
            "title": h.get("title", h.get("docresoltitle", "")),
            "url": h.get("uri", f"https://eur-lex.europa.eu/legal-content/EN/TXT/?docid={h.get('docid', '')}"),
            "published": h.get("publicationDate", ""),
            "snippet": _strip_tags(h.get("abstract", ""))[:240],
            "fields": {
                "language": h.get("language", ""),
                "document_type": h.get("documentType", ""),
                "legal_status": h.get("legalStatus", ""),
            },
            "raw": json.dumps(h),
        }

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/search",
            {
                "q": query,
                "type": "all",
                "field": "all",
                "scope": "all",
                "lang": "en",
                "format": "json",
                "qid": str(int(time.time() * 1000)),
            },
            {},
        )
        resp = httpx.get(url, headers=headers, params=params, timeout=20.0)
        resp.raise_for_status()
        body = resp.json()
        response = body.get("response", {}) if isinstance(body, dict) else {}
        results = response.get("results", body if isinstance(body, list) else [])
        return [self._hit(h) for h in results[:max_results]]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        # Single-document lookup via the same endpoint, scoping to the docid.
        url, params, headers = self.inject_auth(
            f"{self.BASE}/search",
            {
                "q": f"docid:{record_id}",
                "type": "all",
                "field": "all",
                "scope": "all",
                "lang": "en",
                "format": "json",
                "qid": str(int(time.time() * 1000)),
            },
            {},
        )
        resp = httpx.get(url, headers=headers, params=params, timeout=20.0)
        resp.raise_for_status()
        body = resp.json()
        response = body.get("response", {}) if isinstance(body, dict) else {}
        results = response.get("results", [])
        return [self._hit(results[0])] if results else []


class GermanGovAdapter(ResourceAdapter):
    """German Federal Government data portal (Deutsches Gesetzblatt) — https://api.de.gov.de.

    Keyless. Searches the official gazette (``Amtlicher Teil``) via
    ``/v1/aktenseiten`` with ``suchbegriff`` (search term); fetches one gazette
    entry by its ``id``.
    """

    name = "german"
    domain = "legal"
    requires_key = False
    BASE = "https://api.de.gov.de/v1"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
    ):
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.5, jitter=0.1)
        )

    def inject_auth(self, url, params=None, headers=None):
        h = dict(headers or {})
        h.setdefault("User-Agent", _UA)
        return url, dict(params or {}), h

    def _entry(self, e):
        return {
            "source": "german",
            "id": str(e.get("id", "")),
            "title": e.get("titel", e.get("title", "")),
            "url": e.get("url", e.get("url_pdf", "")),
            "published": e.get("datum", e.get("date", "")),
            "snippet": _strip_tags(e.get("kurztitel", e.get("abstract", "")))[:240],
            "fields": {
                "art": e.get("art", ""),
                "dokumentart": e.get("dokumentart", ""),
                "suchbegriffe": ", ".join(e.get("suchbegriffe", []) or []),
            },
            "raw": json.dumps(e),
        }

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/aktenseiten",
            {"suchbegriff": query, "page_size": min(max_results, 100)},
            {},
        )
        resp = httpx.get(url, headers=headers, params=params, timeout=20.0)
        resp.raise_for_status()
        entries = resp.json()
        if isinstance(entries, dict):
            entries = entries.get("results", entries.get("aktenseiten", []))
        return [self._entry(e) for e in entries[:max_results]]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/aktenseiten/{record_id}", params, {}
        )
        resp = httpx.get(url, headers=headers, params=params, timeout=20.0)
        resp.raise_for_status()
        body = resp.json()
        entry = body.get("aktenseite", body) if isinstance(body, dict) else body
        return [self._entry(entry)]


class BioRxivAdapter(ResourceAdapter):
    """bioRxiv / medRxiv preprint lookup — https://api.biorxiv.org.

    Keyless. The official API is not full-text: it serves preprint metadata by
    date interval, by "N most recent", or by DOI. ``search`` therefore accepts
    a DOI (single lookup), a ``YYYY-MM-DD`` / ``YYYY-MM-DD/YYYY-MM-DD`` interval
    (date range), or any other string (returns the N most recent preprints,
    N = ``max_results``). ``fetch`` looks up one preprint by DOI.
    """

    name = "biorxiv"
    domain = "scholarly"
    requires_key = False
    BASE = "https://api.biorxiv.org/details"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        server: str = "biorxiv",
    ):
        self.server = server if server in ("biorxiv", "medrxiv") else "biorxiv"
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.5, jitter=0.1)
        )

    def _paper(self, p):
        doi = p.get("doi", "")
        return {
            "source": "biorxiv",
            "id": doi,
            "title": p.get("title", ""),
            "url": f"https://www.biorxiv.org/content/{doi}" if doi else "",
            "published": p.get("date", ""),
            "snippet": _strip_tags(p.get("abstract", ""))[:240],
            "authors": p.get("authors", ""),
            "fields": {
                "server": self.server,
                "category": p.get("category", ""),
                "version": p.get("version", ""),
                "type": p.get("type", ""),
                "license": p.get("license", ""),
            },
            "raw": json.dumps(p),
        }

    def _lookup(self, interval, server=None):
        server = server or self.server
        url = f"{self.BASE}/{server}/{interval}/0/json"
        resp = httpx.get(url, timeout=20.0)
        resp.raise_for_status()
        return resp.json().get("collection", [])

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        q = (query or "").strip()
        if re.match(r"^10\.\d{4,9}/\S+", q):
            # DOI -> single-manuscript lookup.
            url = f"{self.BASE}/{self.server}/{q}/na/json"
            resp = httpx.get(url, timeout=20.0)
            resp.raise_for_status()
            papers = resp.json().get("collection", [])
            return [self._paper(p) for p in papers[:max_results]]
        if re.match(r"^\d{4}-\d{2}-\d{2}(/?\d{4}-\d{2}-\d{2})?$", q):
            papers = self._lookup(q)
        else:
            # No interpretable date/DOI -> most recent N preprints.
            papers = self._lookup(str(max_results))
        return [self._paper(p) for p in papers[:max_results]]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        if not re.match(r"^10\.\d{4,9}/\S+", str(record_id)):
            return []
        papers = self._lookup_doi(str(record_id))
        if not papers:
            return []
        return [self._paper(papers[0])]

    def _lookup_doi(self, doi):
        url = f"{self.BASE}/{self.server}/{doi}/na/json"
        resp = httpx.get(url, timeout=20.0)
        resp.raise_for_status()
        return resp.json().get("collection", [])


class ChemRxivAdapter(ResourceAdapter):
    """ChemRxiv preprint search — https://chemrxiv.org (OpenEngage API).

    Requires an OpenEngage ``token`` (``STITCH_CHEMXIV_TOKEN``); the token is
    sent as an ``Authorization: Bearer`` header. Search is full-text via
    ``/item/search``; fetch pulls one preprint by its ``id``.
    """

    name = "chemrxiv"
    domain = "scholarly"
    requires_key = True
    BASE = "https://chemrxiv.org/engage/api-gateway/chemrxiv/assets/orp/item"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        api_key: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("STITCH_CHEMXIV_TOKEN", "")
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.5, jitter=0.1)
        )

    def inject_auth(self, url, params=None, headers=None):
        h = dict(headers or {})
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return url, dict(params or {}), h

    def _item(self, it):
        authors = it.get("authors", [])
        if isinstance(authors, list):
            names = [a.get("name", "") if isinstance(a, dict) else str(a) for a in authors]
        else:
            names = [str(authors)]
        return {
            "source": "chemrxiv",
            "id": str(it.get("id", "")),
            "title": it.get("title", ""),
            "url": it.get("url", f"https://chemrxiv.org/engage/chemrxiv/public-article-details/{it.get('id', '')}"),
            "published": it.get("published_on", ""),
            "snippet": _strip_tags(it.get("abstract", ""))[:240],
            "authors": ", ".join(n for n in names if n),
            "fields": {
                "doi": it.get("doi", ""),
                "topics": ", ".join(t.get("name", "") if isinstance(t, dict) else str(t) for t in it.get("topics", []) or []),
            },
            "raw": json.dumps(it),
        }

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/search",
            {"query": query, "page_size": min(max_results, 100)},
            {},
        )
        resp = httpx.get(url, headers=headers, params=params, timeout=20.0)
        resp.raise_for_status()
        body = resp.json()
        items = body.get("data", []) if isinstance(body, dict) else body
        return [self._item(i) for i in items[:max_results]]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/{record_id}", params, {}
        )
        resp = httpx.get(url, headers=headers, params=params, timeout=20.0)
        resp.raise_for_status()
        body = resp.json()
        item = body.get("data", body) if isinstance(body, dict) else body
        if not item:
            return []
        if isinstance(item, list):
            return [self._item(item[0])]
        return [self._item(item)]


class AlphaVantageAdapter(ResourceAdapter):
    """Alpha Vantage market data — https://www.alphavantage.co.

    Requires ``STITCH_ALPHA_VANTAGE_KEY`` (free key; ~5-75 req / day). ``search``
    runs a company/business-keyword ``SEARCH`` lookup; ``fetch`` pulls daily
    OHLC market data for a symbol via ``TIME_SERIES_DAILY``. Error payloads
    (``notes`` / ``information``) surface as a single empty result.
    """

    name = "alphavantage"
    domain = "financial"
    requires_key = True
    BASE = "https://www.alphavantage.co"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        api_key: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("STITCH_ALPHA_VANTAGE_KEY", "")
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=2.0, jitter=1.0)
        )

    def inject_auth(self, url, params=None, headers=None):
        p = dict(params or {})
        p["apikey"] = self.api_key
        return url, p, dict(headers or {})

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/query",
            {"function": "SEARCH", "keywords": query, "apikey": self.api_key},
            {},
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        body = resp.json()
        rows = body.get("data", [])
        if not rows:
            # No data / rate-limit -> surface the note, no crash.
            note = body.get("notes") or body.get("information") or ""
            return [{"source": "alphavantage", "id": "", "title": note or query, "url": "", "snippet": note, "fields": {}, "raw": json.dumps(body)}]
        out = []
        for r in rows[:max_results]:
            out.append({
                "source": "alphavantage",
                "id": r.get("symbol", ""),
                "title": r.get("companyName", r.get("symbol", "")),
                "url": "",
                "snippet": f"{r.get('companyName', '')} — {r.get('industry', '')}",
                "fields": {
                    "instrument_type": r.get("instrument_type", ""),
                    "ticker": r.get("ticker", ""),
                    "sector": r.get("sector", ""),
                },
                "raw": json.dumps(r),
            })
        return out

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/query",
            {"function": "TIME_SERIES_DAILY", "symbol": record_id, "apikey": self.api_key},
            {},
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        body = resp.json()
        ts = body.get("Time Series (Daily)")
        if not ts:
            note = body.get("notes") or body.get("information") or ""
            return [{"source": "alphavantage", "id": str(record_id), "title": note or str(record_id), "url": "", "snippet": note, "fields": {}, "raw": json.dumps(body)}]
        first_date, ohlcv = next(iter(ts.items()))
        meta = body.get("Meta Data", {})
        return [
            {
                "source": "alphavantage",
                "id": meta.get("2. symbol", str(record_id)),
                "title": f"{meta.get('1. symbol', str(record_id))} daily close",
                "url": "",
                "snippet": f"latest {first_date}: open {ohlcv.get('1. open', '')}, close {ohlcv.get('4. close', '')}",
                "fields": {
                    "symbol": meta.get("2. symbol", str(record_id)),
                    "last_refreshed": meta.get("4. last refreshed", ""),
                    "open": ohlcv.get("1. open", ""),
                    "high": ohlcv.get("2. high", ""),
                    "low": ohlcv.get("3. low", ""),
                    "close": ohlcv.get("4. close", ""),
                    "volume": ohlcv.get("5. volume", ""),
                },
                "raw": json.dumps(ohlcv),
            }
        ]
