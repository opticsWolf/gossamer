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
  (Retired: ``EurlexAdapter`` / ``GermanGovAdapter`` were removed — their
  endpoints do not exist. EU law: resolve CELEX via ``legal-content`` URLs;
  German gazette: ``recht.bund.de`` ELI permalinks; German decisions:
  Open Legal Data. See ``docs/PROVIDER_ALTERNATIVES_*.md``.)
  * :class:`BioRxivAdapter`       — bioRxiv / medRxiv preprints (scholarly, keyless)
  * :class:`ChemRxivAdapter`      — ChemRxiv preprints (scholarly, token)
  * :class:`AlphaVantageAdapter`  — market data: company search + daily OHLC (financial, key)
"""

import json
import re
import xml.etree.ElementTree as ET
from datetime import date as _date
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs

import httpx

from gossamer.env import getenv as _env_get
from gossamer.search_providers import RateLimit, RateState, ResourceAdapter

def _package_version() -> str:
    """Installed dist version (single source: pyproject); never stale."""
    try:
        from importlib.metadata import version as _metadata_version

        return _metadata_version("gossamer")
    except Exception:  # pragma: no cover - editable/src layouts without metadata
        return "0.0.0"


_UA = f"gossamer/{_package_version()}"

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
        self.email = email or _env_get("GOSSAMER_OPENALEX_EMAIL", "") or "research@example.org"
        self.api_key = api_key or _env_get("GOSSAMER_OPENALEX_KEY", "")
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
    _ARXIV_UA = "gossamer/0.5.3 (mailto:researcher@example.org)"

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

    Keyless via the ``fredgraph.csv`` download (a free ``GOSSAMER_FRED_KEY``
    unlocks the official observations API instead). ``search`` treats the
    query as a series id (FRED has no public series-search REST endpoint).
    The old ``api.fred.stlouisfed.org`` hostname does not resolve in DNS.
    """

    name = "fred"
    domain = "financial"
    requires_key = False
    BASE = "https://api.stlouisfed.org/fred"
    GRAPH_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        api_key: Optional[str] = None,
    ):
        self.api_key = api_key or _env_get("GOSSAMER_FRED_KEY", "")
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
        if self.api_key:
            return self._fetch_official(record_id, params)
        return self._fetch_csv(record_id)

    def _record(self, record_id, obs, raw):
        points = [f"{o.get('date', '')}={o.get('value', '')}" for o in obs[-10:]]
        return [
            {
                "source": "fred",
                "id": record_id,
                "title": f"FRED series {record_id}",
                "url": f"https://fred.stlouisfed.org/series/{record_id}",
                "snippet": f"{len(obs)} observations; last: {points[-1] if points else 'n/a'}",
                "fields": {"fred": {"observations": obs[-50:]}},
                "raw": raw,
            }
        ]

    def _fetch_official(self, record_id, params=None):
        url, params, headers = self.inject_auth(
            f"{self.BASE}/series/observations",
            {"series_id": record_id, "file_type": "json"},
            {},
        )
        resp = httpx.get(url, params=params, headers=headers, timeout=20.0)
        resp.raise_for_status()
        data = resp.json()
        obs = [
            {"date": o.get("date", ""), "value": o.get("value", "")}
            for o in data.get("observations", [])
        ]
        return self._record(record_id, obs, json.dumps(data))

    def _fetch_csv(self, record_id):
        # Keyless fallback: the graph CSV download (official API needs a key).
        resp = httpx.get(
            self.GRAPH_CSV, params={"id": record_id}, timeout=20.0
        )
        resp.raise_for_status()
        lines = resp.text.strip().splitlines()
        obs = []
        for line in lines[1:]:
            date, _, value = line.partition(",")
            date, value = date.strip(), value.strip()
            if date and value:
                obs.append({"date": date, "value": value})
        return self._record(record_id, obs, "\n".join(lines[:51]))

class GitHubAdapter(ResourceAdapter):
    """GitHub code / repository search (https://docs.github.com/rest).

    Keyless (60 requests/hr) or with ``GOSSAMER_GITHUB_TOKEN`` (5,000/hr).
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
        self.api_key = api_key or _env_get("GOSSAMER_GITHUB_TOKEN", "")
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

    Keyless (3 rps) or with ``GOSSAMER_NCBI_KEY`` (10 rps). Send ``email`` —
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
        self.api_key = api_key or _env_get("GOSSAMER_NCBI_KEY", "", legacy=["GOSSAMER_NCBC_KEY", "STITCH_NCBC_KEY", "STITCH_NCBI_KEY"])
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
        p.setdefault("tool", "gossamer")
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
    ``GOSSAMER_NASA_KEY``. NeoWs is date-indexed, so :meth:`search` treats the
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
        self.api_key = api_key or _env_get("GOSSAMER_NASA_KEY", "DEMO_KEY")
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
            f"{self.BASE}/neo/rest/v1/feed",
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
            f"{self.BASE}/neo/rest/v1/neo/{record_id}", params, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        # The single-object endpoint returns the object directly.
        neo = resp.json()
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
    """NIST National Vulnerability Database (CVE API 2.0).

    Keyless (5 req / 30 s) or with ``GOSSAMER_NVD_API_KEY`` (50 req / 30 s,
    sent as the ``apiKey`` query parameter). A CVE-id shaped query hits the
    indexed ``cveId`` field, otherwise it is a full-text ``keywordSearch``.
    """

    name = "nvd"
    domain = "tech"
    requires_key = False
    BASE = "https://services.nvd.nist.gov/rest/json/cves/2.0"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        api_key: Optional[str] = None,
    ):
        self.api_key = api_key or _env_get("GOSSAMER_NVD_API_KEY", "")
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=6.0, jitter=1.0)
        )

    def inject_auth(self, url, params=None, headers=None):
        p = dict(params or {})
        if self.api_key:
            p["apiKey"] = self.api_key
        return url, p, dict(headers or {})

    def parse_headers(self, status, headers):
        return _rate_state_from_headers(headers, default_rps=50.0)

    @staticmethod
    def _row(cve: dict, fallback_id: str = ""):
        # CVSS v3.1 preferred, v3.0 / v2.0 as fallback — whichever metric
        # the record carries.
        metrics = cve.get("metrics", {}) or {}
        cvss: dict = {}
        for bucket in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
            entries = metrics.get(bucket) or []
            if entries:
                cvss = entries[0].get("cvssData", {}) or {}
                break
        cve_id = cve.get("id", fallback_id)
        published = cve.get("published", "")
        return {
            "source": "nvd",
            "id": cve_id,
            "title": cve_id,
            "url": f"https://nvd.nist.gov/vuln/detail/{cve_id}",
            "published": published[:10],
            "snippet": _first_desc(cve),
            "fields": {
                "nvd": {
                    "severity": cvss.get("baseSeverity", ""),
                    "base_score": cvss.get("baseScore", ""),
                    "vector": cvss.get("vectorString", ""),
                }
            },
            "raw": json.dumps(cve),
        }

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        q = (query or "").strip()
        if re.match(r"^CVE-\d{4}-\d{4,}$", q, re.IGNORECASE):
            key, val = "cveId", q.upper()
        else:
            key, val = "keywordSearch", q
        url, params, headers = self.inject_auth(
            self.BASE,
            {key: val, "resultsPerPage": min(max_results, 100)},
            {},
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        items = resp.json().get("vulnerabilities", [])
        return [
            self._row(item.get("cve", {})) for item in items[:max_results]
        ]

    def fetch(self, record_id, params=None):
        # record_id is a CVE id, e.g. CVE-2021-44228.
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            self.BASE, {"cveId": record_id}, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        items = resp.json().get("vulnerabilities", [])
        if not items:
            return []
        return [self._row(items[0].get("cve", {}), str(record_id))]

class ZenodoAdapter(ResourceAdapter):
    """Zenodo research-records search / lookup — https://zenodo.org/api.

    Keyless, or ``GOSSAMER_ZENODO_TOKEN`` for a higher rate. Records are searched
    via ``/records`` (``q``/``size``/``page``) and fetched via ``/records/<id>``
    (InvenioRDM API — the legacy ``/records/search`` path 404s).
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
        self.api_key = api_key or _env_get("GOSSAMER_ZENODO_TOKEN", "")
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

    @staticmethod
    def _names(people) -> str:
        """Creator/contributor list (InvenioRDM ``person_or_org`` or legacy
        ``{name}`` dicts, or plain strings) -> ", "-joined names."""
        out = []
        for a in people or []:
            if isinstance(a, dict):
                name = a.get("name") or (a.get("person_or_org") or {}).get("name", "")
                if name:
                    out.append(name)
            elif a:
                out.append(str(a))
        return ", ".join(out)

    def _hit(self, h, fallback_id=""):
        m = h.get("metadata", {}) or {}
        links = h.get("links", {}) or {}
        rec_id = str(h.get("id", fallback_id))
        rtype = m.get("resource_type", {})
        if isinstance(rtype, dict):
            rtype = rtype.get("title", {}).get("en", "") if isinstance(rtype.get("title"), dict) else rtype.get("id", "")
        return {
            "source": "zenodo",
            "id": rec_id,
            "title": m.get("title", ""),
            "url": links.get("html")
            or links.get("self_html")
            or (f"https://zenodo.org/records/{rec_id}" if rec_id else ""),
            "published": m.get("publication_date", ""),
            "authors": self._names(
                m.get("creators") or m.get("contributors") or m.get("authors")
            ),
            "snippet": _strip_tags(m.get("description", ""))[:240],
            "fields": {"zenodo": {"resource_type": rtype or ""}},
            "raw": json.dumps(h),
        }

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/records",
            {"q": query, "size": min(max_results, 100), "page": 1},
            {},
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
        return [self._hit(h) for h in hits[:max_results]]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/records/{record_id}", params, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        return [self._hit(resp.json(), str(record_id))]

class SoftwareHeritageAdapter(ResourceAdapter):
    """Software Heritage source-archive lookup — https://archive.softwareheritage.org.

    Keyless (client auto-paces to server hints). There is no public REST
    full-text code search (the old ``/search/`` path 404s), so ``search``
    resolves an *origin URL* (``https://github.com/…``) to its archive
    record, and ``fetch`` pulls one origin by URL. SWEET ids are passed
    through to the ``/source/sid/`` endpoint best-effort.
    """

    name = "softwareheritage"
    domain = "tech"
    requires_key = False
    BASE = "https://archive.softwareheritage.org/api/1"

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

    @staticmethod
    def _origin_row(origin: dict, fallback: str = "") -> dict:
        url = origin.get("url", fallback)
        visits = origin.get("origin_visits_url", "")
        types = ", ".join(origin.get("visit_types", []) or [])
        return {
            "source": "softwareheritage",
            "id": url,
            "title": url,
            "url": f"https://archive.softwareheritage.org/browse/origin/?origin_url={url}" if url else visits,
            "snippet": f"archived origin; visit types: {types}" if types else "archived origin",
            "fields": {"softwareheritage": {"visit_types": types}},
            "raw": json.dumps(origin),
        }

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        q = (query or "").strip()
        if not q:
            raise ValueError("SoftwareHeritageAdapter needs an origin URL to look up")
        # Bare repo paths are completed to https:// URLs.
        if "://" not in q:
            q = "https://" + q.lstrip("/")
        resp = httpx.get(f"{self.BASE}/origin/{q}/get/", timeout=20.0)
        resp.raise_for_status()
        return [self._origin_row(resp.json(), q)][:max_results]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        sid = str(record_id or "").strip()
        if sid.startswith("https://archive.softwareheritage.org/"):
            m = re.search(r"/(s|d|p|r):([^/?]+)", sid)
            if m:
                sid = f"{m.group(1)}:{m.group(2)}"
        if "://" in sid or "." in sid.split("/")[0]:
            # Origin URL (or bare host/path): same lookup as search.
            q = sid if "://" in sid else "https://" + sid.lstrip("/")
            resp = httpx.get(f"{self.BASE}/origin/{q}/get/", timeout=20.0)
            resp.raise_for_status()
            return [self._origin_row(resp.json(), q)]
        # SWEET id: best-effort content lookup.
        resp = httpx.get(f"{self.BASE}/source/sid/{sid}", timeout=20.0)
        resp.raise_for_status()
        src = resp.json()
        meta = src.get("meta", {}) or {}
        return [
            {
                "source": "softwareheritage",
                "id": src.get("id", sid),
                "title": meta.get("name", src.get("id", sid)),
                "url": f"https://archive.softwareheritage.org/{src.get('id', sid)}",
                "published": meta.get("date", ""),
                "snippet": meta.get("description", "")[:240],
                "fields": {"softwareheritage": {"type": src.get("type", "")}},
                "raw": json.dumps(src),
            }
        ]

class CongressAdapter(ResourceAdapter):
    """Congress.gov legislative data via api.data.gov — https://api.data.gov/congress.

    Requires ``GOSSAMER_CONGRESS_KEY`` (data.gov key; 5,000 calls/hr). Members are
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
        self.api_key = api_key or _env_get("GOSSAMER_CONGRESS_KEY", "")
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
        # Live shape: top-level ``quotes`` with lowercase ``shortname``.
        quotes = resp.json().get("quotes", []) or []
        out: List[Dict[str, str]] = []
        for q in quotes[:max_results]:
            name = q.get("shortname") or q.get("shortName") or q.get("symbol", "")
            out.append(
                {
                    "source": "yahoo",
                    "id": q.get("symbol", ""),
                    "title": name,
                    "url": f"https://finance.yahoo.com/quote/{q.get('symbol', '')}",
                    "snippet": (
                        f"{name} — {q.get('exchange', '')} "
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
    """Overpass API geo queries for OpenStreetMap data.

    Keyless; no published hard limit (small requests are prioritised, so be
    polite). The query is an Overpass QL string sent URL-encoded to the
    interpreter endpoint; results are OSM nodes / ways / relations with
    their tags. Default host is the kumi.systems mirror (verified live —
    ``overpass-api.de`` 406s automated clients); alternates:
    ``https://overpass.private.coffee/api/interpreter``.
    """

    name = "overpass"
    domain = "geo"
    requires_key = False
    BASE = "https://overpass.kumi.systems/api/interpreter"

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

    Requires ``GOSSAMER_CENSUS_KEY`` (~5,000 req/day). The Census API is not a
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
        self.api_key = api_key or _env_get("GOSSAMER_CENSUS_KEY", "")
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.5, jitter=0.25)
        )

    def inject_auth(self, url, params=None, headers=None):
        p = dict(params or {})
        # Never send an empty key (the API answers keyless-shaped errors).
        if self.api_key:
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
        *,
        api_key: Optional[str] = None,
    ):
        # Search is keyless; cluster/opinion *detail* requires a token.
        self.api_key = api_key or _env_get("GOSSAMER_COURTLISTENER_KEY", "")
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.1, jitter=0.02)
        )

    def inject_auth(self, url, params=None, headers=None):
        h = dict(headers or {})
        h.setdefault("User-Agent", _UA)
        if self.api_key:
            h["Authorization"] = f"Token {self.api_key}"
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
            f"{self.BASE}/clusters/{record_id}/", params, {}
        )
        resp = httpx.get(url, headers=headers, params=params, timeout=20.0)
        if resp.status_code == 401:
            raise RuntimeError(
                "CourtListener cluster detail requires authentication; set "
                "GOSSAMER_COURTLISTENER_KEY (search stays keyless)."
            )
        resp.raise_for_status()
        return [self._row(resp.json())]

class EcfrAdapter(ResourceAdapter):
    """US Code of Federal Regulations (eCFR) lookup — https://www.ecfr.gov.

    Keyless, via the versioner API (titles + structure tree). The API is
    citation-addressed rather than full-text: ``search`` and ``fetch`` both
    parse a citation (``"21 CFR 113"``, ``"21/113"``, ``"21.113"``) and
    return the corresponding title/part node with its section listing.
    ``record_id`` / ``query`` accepts ``"title"`` alone (whole title) or
    ``"title/part"``.
    """

    name = "ecfr"
    domain = "legal"
    requires_key = False
    BASE = "https://www.ecfr.gov/api/versioner/v1"

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

    def _issue_date(self, title: str) -> str:
        resp = httpx.get(f"{self.BASE}/titles.json", timeout=20.0)
        resp.raise_for_status()
        for t in resp.json().get("titles", []):
            if str(t.get("number", "")) == str(title):
                return t.get("latest_issue_date", "")
        raise ValueError(f"eCFR has no title {title!r}")

    def _structure(self, title: str) -> dict:
        date = self._issue_date(title)
        if not date:
            raise ValueError(f"eCFR title {title!r} has no issue date")
        resp = httpx.get(
            f"{self.BASE}/structure/{date}/title-{title}.json", timeout=30.0
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _find_part(node: dict, part: str):
        """DFS for the part node whose identifier matches *part*.

        First pass prefers ``type == "part"`` hits; the second pass accepts
        any identifier hit (e.g. appendices numbered like parts).
        """
        want = str(part).lstrip("0")

        def walk(only_parts: bool):
            stack = [node]
            while stack:
                current = stack.pop()
                ident = str(current.get("identifier", "")).lstrip("0")
                if ident == want and (
                    not only_parts or current.get("type") == "part"
                ):
                    return current
                stack.extend(reversed(current.get("children", []) or []))
            return None

        return walk(True) or walk(False)

    @staticmethod
    def _sections(node: dict, limit: int = 12) -> list:
        out = []
        for child in node.get("children", []) or []:
            if child.get("type") == "section":
                out.append(
                    f"{child.get('identifier', '')} {child.get('label', '')}".strip()
                )
                if len(out) >= limit:
                    break
        return out

    def _part(self, title, part):
        tree = self._structure(title)
        node = self._find_part(tree, part)
        if node is None:
            raise ValueError(f"eCFR title {title} has no part {part!r}")
        label = node.get("label", "")
        desc = node.get("label_description", "")
        sections = self._sections(node)
        snippet = " ".join(s for s in [desc, f"Sections: {'; '.join(sections)}"] if s)[:400]
        return {
            "source": "ecfr",
            "id": f"{title}/{part}",
            "title": f"Title {title}: {label}" if label else f"Title {title} part {part}",
            "url": f"https://www.ecfr.gov/current/title-{title}/part-{part}",
            "snippet": snippet,
            "fields": {
                "title_no": str(title),
                "part": str(part),
                "label": label,
                "section_count": len(node.get("children", []) or []),
            },
            "raw": json.dumps(node),
        }

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        title, part = self._parse_citation(query)
        if not title:
            raise ValueError(
                f"Could not parse an eCFR citation from {query!r}; try "
                '"21 CFR 113" or "21/113".'
            )
        if not part:
            tree = self._structure(title)
            label = tree.get("label", "")
            descs = [
                str(c.get("label", "")) for c in (tree.get("children", []) or [])[:8]
            ]
            return [{
                "source": "ecfr",
                "id": str(title),
                "title": label or f"Title {title}",
                "url": f"https://www.ecfr.gov/current/title-{title}",
                "snippet": "; ".join(d for d in descs if d)[:400],
                "fields": {"title_no": str(title)},
                "raw": json.dumps(
                    {k: tree.get(k) for k in ("identifier", "label", "type")}
                ),
            }]
        return [self._part(title, part)]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        title, part = self._parse_citation(record_id)
        if not title:
            raise ValueError(
                f"Could not parse an eCFR citation from {record_id!r}."
            )
        if not part:
            return self._search_impl(title)
        return [self._part(title, part)]

class FederalRegisterAdapter(ResourceAdapter):
    """US Federal Register documents — https://www.federalregister.gov.

    Keyless (the ``api.`` hostname is retired; an empty ``api_key`` parameter
    triggers a redirect, so none is ever sent). Search is full-text over
    documents / notices via ``/api/v1/documents.json``; fetch pulls one
    document by its ``document_number``.
    """

    name = "federalregister"
    domain = "legal"
    requires_key = False
    BASE = "https://www.federalregister.gov/api/v1"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
    ):
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.25, jitter=0.05)
        )

    def inject_auth(self, url, params=None, headers=None):
        return url, dict(params or {}), dict(headers or {})

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
            # Full-text search runs on conditions[term]; bare ``q`` matches
            # nothing (verified live).
            {"conditions[term]": query, "per_page": min(max_results, 100)},
            {},
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        # The search envelope nests hits under ``results`` (``documents``
        # is only the fetch path's shape); be lenient to both.
        body = resp.json()
        docs = body.get("results", body.get("documents", [])) or []
        return [self._doc(d) for d in docs[:max_results]]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/documents/{record_id}.json", params, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        return [self._doc(resp.json())]

class BioRxivAdapter(ResourceAdapter):
    """bioRxiv / medRxiv preprint lookup — https://api.biorxiv.org.

    Keyless. The official API is not full-text: it serves preprint metadata by
    date interval or by DOI. ``search`` therefore accepts a DOI (single
    lookup) or a ``YYYY-MM-DD`` / ``YYYY-MM-DD/YYYY-MM-DD`` interval (date
    range); any other string raises ``ValueError`` with an actionable message
    instead of silently returning nothing (the old "N most recent" fallback
    hit an API error in practice). ``fetch`` looks up one preprint by DOI.
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

    @staticmethod
    def _check_query(q: str) -> str:
        """Validate a free-text query; return it stripped or raise."""
        q = (q or "").strip()
        if re.match(r"^10\.\d{4,9}/\S+", q):
            return q
        if re.match(r"^\d{4}-\d{2}-\d{2}(/?\d{4}-\d{2}-\d{2})?$", q):
            return q
        raise ValueError(
            "BioRxivAdapter is date/DOI-addressed, not full-text: pass a DOI "
            f"(10.xxxx/...) or a YYYY-MM-DD[/YYYY-MM-DD] interval, got {q!r}."
        )

    def search(self, query, max_results=5):
        # Validate before the retry wrapper: a malformed query will never
        # succeed on retry, so fail fast instead of burning backoff sleeps.
        self._check_query(query)
        return super().search(query, max_results)

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        q = self._check_query(query)
        if re.match(r"^10\.\d{4,9}/\S+", q):
            # DOI -> single-manuscript lookup.
            url = f"{self.BASE}/{self.server}/{q}/na/json"
            resp = httpx.get(url, timeout=20.0)
            resp.raise_for_status()
            papers = resp.json().get("collection", [])
            return [self._paper(p) for p in papers[:max_results]]
        papers = self._lookup(q)
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

    Requires an OpenEngage ``token`` (``GOSSAMER_CHEMXIV_TOKEN``); the token is
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
        self.api_key = api_key or _env_get("GOSSAMER_CHEMXIV_TOKEN", "")
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

    Requires ``GOSSAMER_ALPHA_VANTAGE_KEY`` (free key; ~5-75 req / day). ``search``
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
        self.api_key = api_key or _env_get("GOSSAMER_ALPHA_VANTAGE_KEY", "", legacy=["STITCH_ALPHAVANTAGE_KEY"])
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
        # SYMBOL_SEARCH nests matches under ``bestMatches`` (``1. symbol`` /
        # ``2. name`` / ``3. type`` / ``4. region`` / ``8. currency``).
        rows = body.get("bestMatches", [])
        if not rows:
            # No data / rate-limit -> surface the note, no crash.
            note = body.get("Note") or body.get("Information") or body.get("notes") or body.get("information") or ""
            return [{"source": "alphavantage", "id": "", "title": note or query, "url": "", "snippet": note, "fields": {}, "raw": json.dumps(body)}]
        out = []
        for r in rows[:max_results]:
            symbol = r.get("1. symbol", "")
            out.append({
                "source": "alphavantage",
                "id": symbol,
                "title": r.get("2. name", symbol),
                "url": "",
                "snippet": f"{r.get('2. name', '')} — {r.get('3. type', '')} {r.get('4. region', '')}",
                "fields": {
                    "instrument_type": r.get("3. type", ""),
                    "ticker": symbol,
                    "currency": r.get("8. currency", ""),
                    "match_score": r.get("9. matchScore", ""),
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


# ────────────────────────────────────────────────────────────────
# Wave 3 — verified replacements & Eurozone coverage (2026-09)
#
# Every adapter below was verified live before it was written (see
# docs/LIVE_PROVIDER_TEST_*.md and docs/PROVIDER_ALTERNATIVES_*.md):
# the exact request URL, the real response shape, and the parse keys.
# ────────────────────────────────────────────────────────────────

def _local_name(tag: str) -> str:
    """Strip an XML namespace: ``{ns}Obs`` -> ``Obs``."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


class OldpAdapter(ResourceAdapter):
    """Open Legal Data — German/EU case law + statutes (REST + dumps).

    Keyless. ``search`` runs a full-text case search
    (``/api/cases/search/?text=``) with optional ``court`` / date filters;
    ``fetch`` pulls one case (``/api/cases/<id>/``) or statute
    (``id`` starting with ``law:`` → ``/api/laws/<id>/``).
    Covers ~425k decisions (BVerfG, BGH, state courts, EuGH) and ~177k norms.
    """

    name = "oldp"
    domain = "legal"
    requires_key = False
    BASE = "https://de.openlegaldata.io/api"

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

    @staticmethod
    def _court_name(court) -> str:
        if isinstance(court, dict):
            return court.get("name", "")
        return str(court or "")

    def _case_row(self, c: dict) -> Dict[str, str]:
        court = self._court_name(c.get("court"))
        file_no = c.get("file_number", "")
        title = f"{court} {file_no}".strip() or c.get("slug", "")
        snippets = c.get("snippets") or []
        snippet = " … ".join(str(s)[:200] for s in snippets[:3])
        return {
            "source": "oldp",
            "id": str(c.get("id", "")),
            "title": title,
            "url": f"https://de.openlegaldata.io/case/{c.get('slug', '')}",
            "published": str(c.get("date", "")),
            "snippet": snippet,
            "fields": {
                "court": court,
                "file_number": file_no,
                "ecli": c.get("ecli", ""),
                "decision_type": c.get("decision_type", ""),
            },
            "raw": json.dumps(c),
        }

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        q = query if isinstance(query, dict) else {"text": query}
        params = {"page_size": min(max_results, 100)}
        if isinstance(q, dict):
            text = q.get("text", "")
            if not (text or "").strip():
                raise ValueError("OldpAdapter search needs a 'text' query")
            params["text"] = text
            for key in ("court", "start_date", "end_date", "decision_type",
                        "court_jurisdiction", "return_text"):
                if q.get(key) not in (None, ""):
                    params[key] = q[key]
        else:
            text = (query or "").strip()
            if not text:
                raise ValueError("OldpAdapter search needs a text query")
            params["text"] = text
        resp = httpx.get(f"{self.BASE}/cases/search/", params=params, timeout=20.0)
        resp.raise_for_status()
        hits = resp.json().get("results", [])
        return [self._case_row(c) for c in hits[:max_results]]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        rid = str(record_id or "")
        if rid.startswith("law:"):
            url = f"{self.BASE}/laws/{rid[4:]}/"
        else:
            url = f"{self.BASE}/cases/{rid}/"
        resp = httpx.get(url, timeout=20.0)
        resp.raise_for_status()
        body = resp.json()
        if rid.startswith("law:"):
            return [{
                "source": "oldp",
                "id": rid,
                "title": body.get("title", rid),
                "url": f"https://de.openlegaldata.io/law/{body.get('slug', '')}",
                "snippet": str(body.get("text", ""))[:400],
                "fields": {"book": body.get("book", ""), "section": body.get("section", "")},
                "raw": json.dumps(body),
            }]
        return [self._case_row(body)]


class HudocAdapter(ResourceAdapter):
    """ECtHR case law via the HUDOC query API (unofficial but stable).

    Keyless. ``search`` runs a KQL full-text query
    (``/app/query/results``) filtered to ECHR content in the requested
    language (default English); ``fetch`` looks up one ``itemid``.
    Query grammar mirrors the echr-extractor project.
    """

    name = "hudoc"
    domain = "legal"
    requires_key = False
    BASE = "https://hudoc.echr.coe.int"
    BASE_FILTER = (
        'contentsitename:ECHR AND (NOT (doctype=PR OR doctype=HFCOMOLD '
        'OR doctype=HECOMOLD))'
    )
    FIELDS = "itemid,docname,appno,kpdate,ecli"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        language: str = "ENG",
    ):
        self.language = (language or "ENG").upper()
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=1.0, jitter=0.5)
        )

    def _query(self, text: str) -> str:
        clauses = [self.BASE_FILTER, f'(languageisocode="{self.language}")']
        text = (text or "").strip().replace('"', "")
        if text:
            clauses.append(f"({text})")
        return " AND ".join(clauses)

    @staticmethod
    def _row(columns: dict) -> Dict[str, str]:
        itemid = columns.get("itemid", "")
        return {
            "source": "hudoc",
            "id": itemid,
            "title": columns.get("docname", ""),
            "url": f"https://hudoc.echr.coe.int/eng?i={itemid}" if itemid else "",
            "published": str(columns.get("kpdate", ""))[:10],
            "snippet": f"application no. {columns.get('appno', '')}".strip(),
            "fields": {
                "appno": columns.get("appno", ""),
                "ecli": columns.get("ecli", ""),
            },
            "raw": json.dumps(columns),
        }

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        if not (query or "").strip():
            raise ValueError("HudocAdapter search needs a text query")
        resp = httpx.get(
            f"{self.BASE}/app/query/results",
            params={
                "query": self._query(query),
                "select": self.FIELDS,
                "sort": "itemid Ascending",
                "start": 0,
                "length": min(max_results, 100),
            },
            timeout=25.0,
        )
        resp.raise_for_status()
        body = resp.json()
        return [self._row(r.get("columns", {})) for r in body.get("results", [])]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        rid = str(record_id or "").strip()
        if not rid:
            raise ValueError("HudocAdapter fetch needs an itemid")
        resp = httpx.get(
            f"{self.BASE}/app/query/results",
            params={
                "query": f"{self.BASE_FILTER} AND (itemid={rid!r})".replace("'", '"'),
                "select": self.FIELDS,
                "sort": "itemid Ascending",
                "start": 0,
                "length": 1,
            },
            timeout=25.0,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return []
        return [self._row(results[0].get("columns", {}))]


class GovInfoAdapter(ResourceAdapter):
    """US government publications via the GovInfo API (bills, CFR, FR, Code).

    Keyless with the shared ``DEMO_KEY`` (or ``GOSSAMER_GOVINFO_KEY`` for a
    free personal key with higher limits). ``search`` runs a full-text
    search (POST ``/search`` with ``historical: true``); ``fetch`` pulls a
    package summary by id (``/packages/<id>/summary``).
    """

    name = "govinfo"
    domain = "legal"
    requires_key = False
    BASE = "https://api.govinfo.gov"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        api_key: Optional[str] = None,
    ):
        self.api_key = api_key or _env_get("GOSSAMER_GOVINFO_KEY", "DEMO_KEY")
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=0.5, jitter=0.25)
        )

    @staticmethod
    def _row(r: dict) -> Dict[str, str]:
        pkg = r.get("packageId", "")
        granule = r.get("granuleId", "")
        dl = r.get("download", {}) or {}
        url = (
            dl.get("txtLink")
            or dl.get("pdfLink")
            or (f"https://www.govinfo.gov/app/details/{pkg}" if pkg else "")
        )
        return {
            "source": "govinfo",
            "id": granule or pkg,
            "title": r.get("title", ""),
            "url": url,
            "published": str(r.get("dateIssued", "")),
            "snippet": f"{r.get('collectionCode', '')} {pkg}".strip(),
            "fields": {
                "collection": r.get("collectionCode", ""),
                "package_id": pkg,
                "granule_id": granule,
            },
            "raw": json.dumps(r),
        }

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        q = (query or "").strip()
        if not q:
            raise ValueError("GovInfoAdapter search needs a text query")
        resp = httpx.post(
            f"{self.BASE}/search",
            params={"api_key": self.api_key},
            json={"query": q, "pageSize": min(max_results, 100),
                  "offsetMark": "*", "historical": True},
            timeout=25.0,
        )
        resp.raise_for_status()
        return [self._row(r) for r in resp.json().get("results", [])[:max_results]]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        rid = str(record_id or "").strip()
        if not rid:
            raise ValueError("GovInfoAdapter fetch needs a package id")
        resp = httpx.get(
            f"{self.BASE}/packages/{rid}/summary",
            params={"api_key": self.api_key},
            timeout=20.0,
        )
        resp.raise_for_status()
        body = resp.json()
        return [{
            "source": "govinfo",
            "id": body.get("packageId", rid),
            "title": body.get("title", rid),
            "url": body.get("download", {}).get("txtLink", "")
            or f"https://www.govinfo.gov/app/details/{rid}",
            "published": str(body.get("dateIssued", "")),
            "snippet": str(body.get("collectionCode", "")),
            "fields": {"collection": body.get("collectionCode", "")},
            "raw": json.dumps(body),
        }]


class FrankfurterAdapter(ResourceAdapter):
    """Foreign-exchange rates via Frankfurter v2 (84 central banks).

    Keyless, no quotas. ``search`` takes a base currency (``"USD"`` → latest
    table, one record per quote) or a pair (``"USD/EUR"`` → single rate);
    ``fetch`` resolves the same ``BASE/QUOTE`` ids (plus ``"BASE"`` for the
    full table). Time series via the optional ``date`` / ``start``+``end``
    params (``YYYY-MM-DD``).
    """

    name = "frankfurter"
    domain = "financial"
    requires_key = False
    BASE = "https://api.frankfurter.dev/v2"

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

    @staticmethod
    def _split_pair(spec: str):
        """``"USD/EUR"`` / ``"USD EUR"`` -> ``(base, quote|None)``."""
        parts = re.split(r"[\s/]+", (spec or "").strip().upper())
        parts = [p for p in parts if p]
        if not parts:
            raise ValueError(
                "FrankfurterAdapter needs a currency (USD) or pair (USD/EUR)."
            )
        base = parts[0]
        if not re.fullmatch(r"[A-Z]{3}", base):
            raise ValueError(f"Not a currency code: {parts[0]!r}")
        quote = parts[1] if len(parts) > 1 else None
        if quote is not None and not re.fullmatch(r"[A-Z]{3}", quote):
            raise ValueError(f"Not a currency code: {parts[1]!r}")
        return base, quote

    @staticmethod
    def _row(base: str, quote: str, rate, date: str) -> Dict[str, str]:
        return {
            "source": "frankfurter",
            "id": f"{base}/{quote}",
            "title": f"{base}/{quote} = {rate} ({date})",
            "url": "",
            "published": date,
            "snippet": f"1 {base} = {rate} {quote} on {date} (central-bank reference rates)",
            "fields": {"base": base, "quote": quote, "rate": rate, "date": date},
            "raw": json.dumps({"base": base, "quote": quote, "rate": rate, "date": date}),
        }

    def _query_rates(self, base, quote, date=None, start=None, end=None, max_results=5):
        params: dict = {"base": base}
        if quote:
            params["quotes"] = quote
        if date:
            params["date"] = date
        if start:
            params["from"] = start
        if end:
            params["to"] = end
        resp = httpx.get(f"{self.BASE}/rates", params=params, timeout=20.0)
        resp.raise_for_status()
        body = resp.json()
        rows = body if isinstance(body, list) else [body]
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            day = str(row.get("date", date or ""))
            b = row.get("base", base)
            # v2 shape: one object per quote ({base, quote, rate, date});
            # map shape ({base, quotes: {EUR: …}}): accept both.
            pairs = []
            if "quote" in row:
                pairs = [(row.get("quote"), row.get("rate"))]
            for q, rate in (row.get("quotes", {}) or {}).items():
                pairs.append((q, rate))
            for q, rate in pairs:
                if not q:
                    continue
                out.append(self._row(b, q, rate, day))
                if len(out) >= max_results:
                    return out
        return out

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        if isinstance(query, dict):
            base, quote = self._split_pair(query.get("pair", query.get("base", "")))
            return self._query_rates(
                base, quote, query.get("date"), query.get("start"),
                query.get("end"), max_results,
            )
        base, quote = self._split_pair(query)
        return self._query_rates(base, quote, max_results=max_results)

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        base, quote = self._split_pair(str(record_id or ""))
        rows = self._query_rates(base, quote, max_results=1)
        return rows or []


class EurostatAdapter(ResourceAdapter):
    """EU macro statistics via the Eurostat dissemination API (JSON-stat).

    Keyless. ``search`` takes a dataset code (``nama_10_gdp``, ``prc_hicp_midx``,
    ``une_rt_a``, ``gov_10dd_edpt1``) with optional dimension filters, either as
    a ``"CODE?geo=DE&time=2023"`` string or a spec dict (``{"dataset": …}``).
    Returns one record per data cell (capped at ``max_results``), with the
    full dimension coordinates in ``fields``.
    """

    name = "eurostat"
    domain = "financial"
    requires_key = False
    BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0"

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

    @staticmethod
    def _parse_spec(query) -> Tuple[str, dict]:
        """``"CODE?a=1&b=2"`` or ``{"dataset": CODE, ...}`` -> (code, filters)."""
        if isinstance(query, dict):
            code = str(query.get("dataset", "")).strip()
            filters = {k: v for k, v in query.items() if k != "dataset"}
            return code, filters
        spec = str(query or "").strip()
        code, _, qs = spec.partition("?")
        filters: dict = {}
        for pair in qs.split("&"):
            if "=" in pair:
                k, _, v = pair.partition("=")
                filters.setdefault(k.strip(), []).append(v.strip())
        single = {k: (v[0] if len(v) == 1 else v) for k, v in filters.items()}
        return code.strip(), single

    @staticmethod
    def _unpack(data: dict, limit: int) -> list:
        """Unpack a JSON-stat cube into ``[(coords, value)]`` (capped)."""
        ids = data.get("id", [])
        sizes = data.get("size", [])
        dimensions = data.get("dimension", {}) or {}
        # Invert each dimension's category index: position -> code + label.
        table = {}
        for dim in ids:
            cat = (dimensions.get(dim, {}) or {}).get("category", {}) or {}
            index = cat.get("index", {}) or {}
            labels = cat.get("label", {}) or {}
            table[dim] = [(code, labels.get(code, code)) for code in sorted(index, key=index.get)]
        strides = []
        acc = 1
        for size in reversed(sizes):
            strides.insert(0, acc)
            acc *= max(1, size)
        values = data.get("value", {}) or {}
        out = []
        for flat, val in values.items():
            try:
                pos = int(flat)
            except (TypeError, ValueError):
                continue
            coords = []
            for i, dim in enumerate(ids):
                size = sizes[i] if i < len(sizes) else 1
                idx = (pos // strides[i]) % max(1, size) if strides else 0
                entries = table.get(dim, [])
                code, label = entries[idx] if idx < len(entries) else ("", "")
                coords.append((dim, code, label))
            out.append((coords, val))
            if len(out) >= limit:
                break
        return out

    def _run(self, code: str, filters: dict, max_results: int) -> list:
        params = {"format": "JSON", "lang": "EN"}
        params.update(filters)
        resp = httpx.get(f"{self.BASE}/data/{code}", params=params, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
        label = data.get("label", code)
        out = []
        for coords, val in self._unpack(data, max_results):
            coord_txt = " · ".join(f"{c[2] or c[1]}" for c in coords)
            dims = {dim: code_ for dim, code_, _label in coords}
            out.append({
                "source": "eurostat",
                "id": f"{code}:" + "/".join(dims.get(d, "") for d in dims),
                "title": f"{label}: {coord_txt} = {val}",
                "url": "",
                "snippet": f"{coord_txt} → {val}",
                "fields": {"dataset": code, **dims, "value": val},
                "raw": json.dumps({"dataset": code, "coords": coords, "value": val}),
            })
        return out

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        code, filters = self._parse_spec(query)
        if not code:
            raise ValueError(
                "EurostatAdapter needs a dataset code (e.g. nama_10_gdp, "
                "prc_hicp_midx); see the Eurostat Data Browser."
            )
        return self._run(code, filters, max_results)

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        code, filters = self._parse_spec(record_id)
        if not code:
            raise ValueError("EurostatAdapter fetch needs a dataset spec")
        return self._run(code, filters, 50)


class BundesbankAdapter(ResourceAdapter):
    """German/Eurozone rates & macro series via the Bundesbank SDMX service.

    Keyless (SDMX-ML only). ``search`` takes a ``"FLOW/KEY"`` spec — e.g.
    ``"BBEX3/D.USD.EUR.BB.AC.000"`` (ECB euro reference rates) — with
    optional ``startPeriod`` / ``endPeriod`` (dict spec or
    ``"FLOW/KEY?startPeriod=2024-01-01"``). Returns one record per
    observation (capped at ``max_results``).
    """

    name = "bundesbank"
    domain = "financial"
    requires_key = False
    BASE = "https://api.statistiken.bundesbank.de/rest"

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

    @staticmethod
    def _parse_spec(query) -> Tuple[str, str, dict]:
        """``"FLOW/KEY?..."`` or ``{"flow", "key", ...}`` -> (flow, key, params)."""
        if isinstance(query, dict):
            return (
                str(query.get("flow", "")).strip(),
                str(query.get("key", "")).strip(),
                {k: v for k, v in query.items() if k not in ("flow", "key")},
            )
        spec = str(query or "").strip()
        flow_key, _, qs = spec.partition("?")
        flow, _, key = flow_key.partition("/")
        params: dict = {}
        for pair in qs.split("&"):
            if "=" in pair:
                k, _, v = pair.partition("=")
                params[k.strip()] = v.strip()
        return flow.strip(), key.strip(), params

    @staticmethod
    def _observations(xml_text: str, limit: int) -> list:
        """Namespace-agnostic generic-data ``Obs`` extraction."""
        root = ET.fromstring(xml_text)
        out = []
        for el in root.iter():
            if _local_name(el.tag) != "Obs":
                continue
            period, value = "", ""
            for child in el:
                lname = _local_name(child.tag)
                if lname in ("ObsDimension", "TimeDimension"):
                    period = child.attrib.get("value", period)
                elif lname == "ObsValue":
                    value = child.attrib.get("value", value)
            if period or value:
                out.append((period, value))
            if len(out) >= limit:
                break
        return out

    def _run(self, flow: str, key: str, params: dict, max_results: int) -> list:
        resp = httpx.get(f"{self.BASE}/data/{flow}/{key}", params=params, timeout=30.0)
        resp.raise_for_status()
        out = []
        for period, value in self._observations(resp.text, max_results):
            out.append({
                "source": "bundesbank",
                "id": f"{flow}/{key}/{period}",
                "title": f"{flow} {key} {period} = {value}",
                "url": "",
                "snippet": f"{period}: {value}",
                "fields": {"flow": flow, "key": key, "date": period, "value": value},
                "raw": json.dumps({"flow": flow, "key": key, "date": period, "value": value}),
            })
        return out

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        flow, key, params = self._parse_spec(query)
        if not flow or not key:
            raise ValueError(
                'BundesbankAdapter needs a "FLOW/KEY" spec, e.g. '
                '"BBEX3/D.USD.EUR.BB.AC.000?startPeriod=2024-01-01".'
            )
        return self._run(flow, key, params, max_results)

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        flow, key, extra = self._parse_spec(record_id)
        if not flow or not key:
            raise ValueError("BundesbankAdapter fetch needs a FLOW/KEY spec")
        merged = dict(extra)
        merged.update(params or {})
        return self._run(flow, key, merged, 100)


class BisAdapter(ResourceAdapter):
    """Central-bank statistics via the BIS SDMX API (policy rates, credit,
    banking, property prices, effective exchange rates).

    Keyless (SDMX-ML only). ``search`` takes a ``"FLOW[/KEY]"`` spec — e.g.
    ``"WS_CBPOL"`` (browse, first series) or ``"WS_CBPOL/M.XM.EUR"`` — with
    optional ``startPeriod`` / ``endPeriod``. Series keys are dimension values
    joined by dots (use ``all`` as a wildcard segment).
    """

    name = "bis"
    domain = "financial"
    requires_key = False
    BASE = "https://stats.bis.org/api/v1"

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

    @staticmethod
    def _observations(xml_text: str, limit: int) -> list:
        """Generic structure-specific extraction: series dimension attrs +
        per-Obs attribute maps (dimension names vary by flow, so nothing is
        hardcoded except the TIME_PERIOD/OBS_VALUE convention)."""
        root = ET.fromstring(xml_text)
        out = []
        for series in root.iter():
            if _local_name(series.tag) != "Series":
                continue
            series_key = {k: v for k, v in series.attrib.items()}
            for obs in series:
                if _local_name(obs.tag) != "Obs":
                    continue
                attrs = dict(obs.attrib)
                period = attrs.pop("TIME_PERIOD", attrs.pop("TIME", ""))
                value = attrs.pop("OBS_VALUE", attrs.pop("OBS", ""))
                out.append((series_key, period, value, attrs))
                if len(out) >= limit:
                    return out
        return out

    def _run(self, flow: str, key: str, params: dict, max_results: int) -> list:
        if not key:
            key = "all"
        resp = httpx.get(f"{self.BASE}/data/{flow}/{key}", params=params, timeout=40.0)
        resp.raise_for_status()
        out = []
        for series_key, period, value, extra in self._observations(resp.text, max_results):
            key_txt = ".".join(f"{k}={v}" for k, v in series_key.items())
            out.append({
                "source": "bis",
                "id": f"{flow}/{key}/{period}",
                "title": f"{flow} {key_txt} {period} = {value}",
                "url": "",
                "snippet": f"{key_txt} — {period}: {value}",
                "fields": {"flow": flow, "key": key, "date": period,
                             "value": value, **{f"dim_{k}": v for k, v in series_key.items()}},
                "raw": json.dumps({"flow": flow, "series": series_key,
                                     "date": period, "value": value, "extra": extra}),
            })
        return out

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        spec = query if isinstance(query, dict) else {"spec": query}
        raw = str(spec.get("spec", "")).strip()
        flow, _, key = raw.partition("/")
        flow, key = flow.strip(), key.strip()
        if not flow:
            raise ValueError(
                'BisAdapter needs a "FLOW[/KEY]" spec, e.g. "WS_CBPOL" or '
                '"WS_CBPOL/M.XM.EUR?startPeriod=2024-01".'
            )
        params = {k: v for k, v in spec.items() if k not in ("spec",)}
        return self._run(flow, key, params, max_results)

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        raw = str(record_id or "").strip()
        flow, _, key = raw.partition("/")
        if not flow.strip():
            raise ValueError("BisAdapter fetch needs a FLOW[/KEY] spec")
        merged = dict(params or {})
        return self._run(flow.strip(), key.strip(), merged, 100)


class CoinGeckoAdapter(ResourceAdapter):
    """Crypto prices and markets via the CoinGecko demo API (keyless, shared
    rate limits — keep queries small). ``search`` looks up coins by name
    (``/search``); ``fetch`` pulls market snapshots by coin id
    (``/coins/markets``).
    """

    name = "coingecko"
    domain = "financial"
    requires_key = False
    BASE = "https://api.coingecko.com/api/v3"

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
            raise ValueError("CoinGeckoAdapter search needs a coin name")
        resp = httpx.get(f"{self.BASE}/search", params={"query": q}, timeout=20.0)
        resp.raise_for_status()
        out = []
        for coin in resp.json().get("coins", [])[:max_results]:
            cid = coin.get("id", "")
            out.append({
                "source": "coingecko",
                "id": cid,
                "title": f"{coin.get('name', '')} ({coin.get('symbol', '').upper()})",
                "url": f"https://www.coingecko.com/en/coins/{cid}" if cid else "",
                "snippet": f"market-cap rank {coin.get('market_cap_rank', '?')}",
                "fields": {
                    "symbol": coin.get("symbol", ""),
                    "market_cap_rank": coin.get("market_cap_rank", ""),
                },
                "raw": json.dumps(coin),
            })
        return out

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        cid = str(record_id or "").strip().lower()
        if not cid:
            raise ValueError("CoinGeckoAdapter fetch needs a coin id")
        resp = httpx.get(
            f"{self.BASE}/coins/markets",
            params={"vs_currency": "usd", "ids": cid,
                    "price_change_percentage": "24h"},
            timeout=20.0,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return []
        m = rows[0]
        return [{
            "source": "coingecko",
            "id": m.get("id", cid),
            "title": f"{m.get('name', cid)} ${m.get('current_price', '')}",
            "url": f"https://www.coingecko.com/en/coins/{m.get('id', cid)}",
            "snippet": (
                f"${m.get('current_price', '')} (24h {m.get('price_change_percentage_24h', '')}%), "
                f"mcap ${m.get('market_cap', '')}"
            ),
            "fields": {
                "symbol": m.get("symbol", ""),
                "current_price_usd": m.get("current_price", ""),
                "market_cap_usd": m.get("market_cap", ""),
                "change_24h_pct": m.get("price_change_percentage_24h", ""),
            },
            "raw": json.dumps(m),
        }]


