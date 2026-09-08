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

from gossamer import _core as _rust
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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each work.
        body = resp.json()
        works = body.get("results", [])
        records = json.loads(
            _rust.openalex_parse_search(json.dumps(body), max_results)
        )
        for rec, w in zip(records, works[:max_results]):
            rec["raw"] = json.dumps(w)
        return records

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url = f"{self.BASE}/works/{record_id}"
        url, params, headers = self.inject_auth(url, params, {})
        resp = httpx.get(url, params=params, headers=headers, timeout=15.0)
        resp.raise_for_status()
        # Row building in Rust (src/adapters.rs); `raw` re-attached here.
        body = resp.json()
        rec = json.loads(_rust.openalex_parse_fetch(json.dumps(body)))
        rec["raw"] = json.dumps(body)
        return [rec]

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
        # Response parsing in Rust (src/adapters.rs); `raw` re-attached
        # here so it stays byte-identical `json.dumps` of each hit.
        body = resp.json()
        hits = body.get("results", [])
        records = json.loads(
            _rust.openmeteo_parse_search(
                json.dumps(body), max_results, self.BASE
            )
        )
        for rec, h in zip(records, hits[:max_results]):
            rec["raw"] = json.dumps(h)
        return records

    def fetch(self, lat_lon, params=None):
        self._enforce_delay()
        lat, lon = _parse_lat_lon(lat_lon)
        p = {"latitude": lat, "longitude": lon}
        if params:
            p.update({k: v for k, v in params.items() if v is not None})
        resp = httpx.get(self.BASE, params=p, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        # Parsing in Rust (src/adapters.rs); lat/lon pre-rendered so float
        # spellings stay exactly Python's; `raw` re-attached verbatim.
        rec = json.loads(
            _rust.openmeteo_parse_forecast(
                json.dumps(data), f"{lat}", f"{lon}", self.BASE
            )
        )
        rec["raw"] = json.dumps(data)
        return [rec]

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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each work.
        body = resp.json()
        msg = body.get("message", {})
        items = msg.get("items", []) if isinstance(msg, dict) else []
        records = json.loads(
            _rust.crossref_parse_search(json.dumps(body), max_results)
        )
        for rec, w in zip(records, items[:max_results]):
            rec["raw"] = json.dumps(w)
        return records

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url = f"{self.BASE}/works/{record_id}"
        url, params, headers = self.inject_auth(url, params, {})
        resp = httpx.get(url, params=params, headers=headers, timeout=20.0)
        resp.raise_for_status()
        # Row building in Rust (src/adapters.rs); `raw` re-attached here.
        # The DOI fallback keeps the exact `record_id` spelling.
        rid, fallback_json = _json_fallback(record_id)
        body = resp.json()
        rec = json.loads(
            _rust.crossref_parse_fetch(json.dumps(body), fallback_json or json.dumps(rid))
        )
        rec["raw"] = json.dumps(body["message"])
        return [rec]
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
        # ElementTree parses first so malformed feeds raise ParseError
        # exactly as before; rows build in Rust (src/adapters.rs) and
        # `raw` is re-attached here via `ET.tostring` (byte-identical).
        root = ET.fromstring(resp_xml)
        entries = _entry_children(root, "entry")
        records = json.loads(_rust.arxiv_parse_search(resp_xml, max_results))
        for rec, entry in zip(records, entries):
            rec["raw"] = ET.tostring(entry, encoding="unicode")
        return records

    def fetch(self, record_id, params=None):
        # id_list is the version-safe documented way to fetch specific papers.
        ident = _bare_arxiv_id(record_id)
        resp_xml = self._get({"id_list": ident, "start": 0, "max_results": 1})
        # ElementTree parses first (ParseError precedence + `raw`).
        root = ET.fromstring(resp_xml)
        entries = _entry_children(root, "entry")
        # A valid entry's <id> contains 'abs/'; a bad id yields an error feed
        # (a single entry with a query id and an 'Error' summary).
        rid, fallback_json = _json_fallback(record_id)
        rec = json.loads(
            _rust.arxiv_parse_fetch(
                resp_xml, ident, fallback_json or json.dumps(rid))
        )
        if entries and "abs/" in _entry_field(entries[0], "id"):
            rec["raw"] = ET.tostring(entries[0], encoding="unicode")
        else:
            rec["raw"] = ""
        return [rec]

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
        # Static note built in Rust (src/adapters.rs); `raw` re-attached
        # here with the original expression so it stays byte-identical.
        rec = json.loads(_rust.worldbank_note())
        rec["raw"] = json.dumps({"search_unavailable": True, "query": query})
        return [rec]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        p = {"format": "json", "per_page": 20}
        if params:
            p.update({k: v for k, v in params.items() if v is not None})
        resp = httpx.get(f"{self.DATA}/{record_id}", params=p, timeout=20.0)
        resp.raise_for_status()
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of the payload.
        # Data endpoint returns [pagination, [data points, ...]].
        payload = resp.json()
        rid, fallback_json = _json_fallback(record_id)
        rec = json.loads(
            _rust.worldbank_parse_fetch(
                json.dumps(payload),
                fallback_json or json.dumps(rid),
                f"{self.DATA}/{record_id}",
            )
        )
        rec["raw"] = json.dumps(payload)
        return [rec]

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

    # NOTE: the old `_record` row helper was retired in the Rust port
    # (M17); both fetch paths build their rows in `src/adapters.rs`.
    def _fetch_official(self, record_id, params=None):
        url, params, headers = self.inject_auth(
            f"{self.BASE}/series/observations",
            {"series_id": record_id, "file_type": "json"},
            {},
        )
        resp = httpx.get(url, params=params, headers=headers, timeout=20.0)
        resp.raise_for_status()
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of the payload.
        data = resp.json()
        rid, fallback_json = _json_fallback(record_id)
        rec = json.loads(
            _rust.fred_parse_official(
                json.dumps(data), fallback_json or json.dumps(rid))
        )
        rec["raw"] = json.dumps(data)
        return [rec]

    def _fetch_csv(self, record_id):
        # Keyless fallback: the graph CSV download (official API needs a key).
        resp = httpx.get(
            self.GRAPH_CSV, params={"id": record_id}, timeout=20.0
        )
        resp.raise_for_status()
        # Row building in Rust (src/adapters.rs), including the
        # `raw` line window; `record_id` keeps its exact spelling.
        rid, fallback_json = _json_fallback(record_id)
        return [
            json.loads(
                _rust.fred_parse_csv(
                    resp.text, fallback_json or json.dumps(rid))
            )
        ]

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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each repo.
        body = resp.json()
        items = body.get("items", [])
        records = json.loads(
            _rust.github_parse_search(json.dumps(body), max_results)
        )
        for rec, r in zip(records, items[:max_results]):
            rec["raw"] = json.dumps(r)
        return records

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        # record_id is "owner/repo".
        url, params, headers = self.inject_auth(
            f"{self.BASE}/repos/{record_id}", params, {}
        )
        resp = httpx.get(url, params=params, headers=headers, timeout=20.0)
        resp.raise_for_status()
        # Row building in Rust (src/adapters.rs); `raw` re-attached here.
        # A missing id/title falls back to the exact `record_id` spelling.
        rid, fallback_json = _json_fallback(record_id)
        repo = resp.json()
        rec = json.loads(
            _rust.github_parse_fetch(
                json.dumps(repo), fallback_json or json.dumps(rid))
        )
        rec["raw"] = json.dumps(repo)
        return [rec]

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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each doc.
        body = resp.json()
        docs = body.get("docs", [])
        records = json.loads(
            _rust.openlibrary_parse_search(
                json.dumps(body), max_results, self.BASE)
        )
        for rec, d in zip(records, docs[:max_results]):
            rec["raw"] = json.dumps(d)
        return records

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        # Search may hand back a /works/ key; keep whichever prefix we were given.
        key = record_id
        if not key.startswith(("/books/", "/works/")):
            key = f"/books/{record_id}"
        resp = httpx.get(f"{self.BASE}{key}.json", timeout=20.0)
        resp.raise_for_status()
        # Row building in Rust (src/adapters.rs); `raw` re-attached here.
        # ``authors`` shape differs between the edition (/books) and work
        # (/works) endpoints: [{name}], [{author:{key}}], or plain strings.
        body = resp.json()
        rec = json.loads(
            _rust.openlibrary_parse_fetch(json.dumps(body), key, self.BASE)
        )
        rec["raw"] = json.dumps(body)
        return [rec]

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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each result.
        body = resp.json()
        results = body.get("results", [])
        records = json.loads(
            _rust.doaj_parse_search(json.dumps(body), max_results)
        )
        for rec, r in zip(records, results[:max_results]):
            rec["raw"] = json.dumps(r)
        return records

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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each uid.
        body = resp.json()
        ids = body["esearchresult"].get("idlist", [])
        records = json.loads(
            _rust.pubmed_parse_search(json.dumps(body), max_results)
        )
        for rec, uid in zip(records, ids[:max_results]):
            rec["raw"] = json.dumps({"uid": uid})
        return records

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
        # ElementTree parses first so malformed payloads raise ParseError
        # exactly as before; the row builds in Rust (src/adapters.rs)
        # and `raw` is the verbatim response text.
        root = ET.fromstring(resp.text)
        entry = root.find("PubmedArticle")
        if entry is None:
            return [{"source": "pubmed", "id": str(record_id), "title": "", "raw": resp.text}]
        rec = json.loads(_rust.pubmed_parse_fetch(resp.text, str(record_id)))
        if rec is None:
            return [{"source": "pubmed", "id": str(record_id), "title": "", "raw": resp.text}]
        rec["raw"] = resp.text
        return [rec]

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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each object.
        body = resp.json()
        records = json.loads(
            _rust.nasa_parse_search(json.dumps(body), start, max_results)
        )
        for rec, neo in zip(records, objects[:max_results]):
            rec["raw"] = json.dumps(neo)
        return records

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/neo/rest/v1/neo/{record_id}", params, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        # The single-object endpoint returns the object directly.
        # Row building in Rust (src/adapters.rs); `raw` re-attached here.
        rid, fallback_json = _json_fallback(record_id)
        neo = resp.json()
        rec = json.loads(
            _rust.nasa_parse_fetch(
                json.dumps(neo), fallback_json or json.dumps(rid))
        )
        rec["raw"] = json.dumps(neo)
        return [rec]

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

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        q = (query or "").strip()
        # CVE-id routing in Rust (src/adapters.rs); results paging stays.
        key, val = _rust.nvd_route_query(q)
        url, params, headers = self.inject_auth(
            self.BASE,
            {key: val, "resultsPerPage": min(max_results, 100)},
            {},
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each CVE object.
        body = resp.json()
        items = body.get("vulnerabilities", [])
        records = json.loads(
            _rust.nvd_parse_vulns(json.dumps(body), "", max_results)
        )
        for rec, item in zip(records, items[:max_results]):
            rec["raw"] = json.dumps(item.get("cve", {}))
        return records

    def fetch(self, record_id, params=None):
        # record_id is a CVE id, e.g. CVE-2021-44228.
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            self.BASE, {"cveId": record_id}, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        # Row building in Rust (src/adapters.rs); `raw` re-attached here.
        body = resp.json()
        items = body.get("vulnerabilities", [])
        if not items:
            return []
        records = json.loads(
            _rust.nvd_parse_fetch(json.dumps(body), str(record_id))
        )
        records[0]["raw"] = json.dumps(items[0].get("cve", {}))
        return records

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

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/records",
            {"q": query, "size": min(max_results, 100), "page": 1},
            {},
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        # Hit building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each hit.
        body = resp.json()
        hits = body.get("hits", {}).get("hits", [])
        records = json.loads(
            _rust.zenodo_parse_search(json.dumps(body), max_results)
        )
        for rec, h in zip(records, hits[:max_results]):
            rec["raw"] = json.dumps(h)
        return records

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/records/{record_id}", params, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        # Hit building in Rust (src/adapters.rs); `raw` re-attached here.
        body = resp.json()
        rec = json.loads(
            _rust.zenodo_parse_fetch(json.dumps(body), str(record_id))
        )
        rec["raw"] = json.dumps(body)
        return [rec]

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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of the origin.
        body = resp.json()
        records = json.loads(_rust.swh_parse_search(
            json.dumps(body), q, max_results))
        for rec in records:
            rec["raw"] = json.dumps(body)
        return records

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
            # Row building in Rust (src/adapters.rs); `raw` re-attached.
            body = resp.json()
            rec = json.loads(_rust.swh_parse_fetch_origin(
                json.dumps(body), q))
            rec["raw"] = json.dumps(body)
            return [rec]
        # SWEET id: best-effort content lookup.
        resp = httpx.get(f"{self.BASE}/source/sid/{sid}", timeout=20.0)
        resp.raise_for_status()
        # Row building in Rust (src/adapters.rs); `raw` re-attached here.
        src = resp.json()
        rec = json.loads(_rust.swh_parse_fetch_sid(json.dumps(src), sid))
        rec["raw"] = json.dumps(src)
        return [rec]

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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each member.
        body = resp.json()
        results = body.get("results", [])
        records = json.loads(
            _rust.congress_parse_search(json.dumps(body), max_results)
        )
        for rec, r in zip(records, results[:max_results]):
            rec["raw"] = json.dumps(r)
        return records

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/members/{record_id}", {"api_key": self.api_key}, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        # Row building in Rust (src/adapters.rs); `raw` re-attached here.
        # A missing id/title falls back to the exact `record_id` spelling.
        rid, fallback_json = _json_fallback(record_id)
        r = resp.json()
        rec = json.loads(
            _rust.congress_parse_fetch(
                json.dumps(r), fallback_json or json.dumps(rid))
        )
        rec["raw"] = json.dumps(r)
        return [rec]

def _json_fallback(record_id):
    """Resolve a ``record_id`` fallback for the Rust fetch kernels.

    Returns ``(rendered, fallback_json)``: None stays null, strings
    pass through, JSON-native values (int/float/bool/list/dict)
    round-trip exactly via ``fallback_json``, and anything else
    (tuples, sets, objects — not expressible in JSON) arrives
    pre-rendered with ``str()``. The last group renders identically
    in URLs/snippets and differs from the original only in the
    ``id``/``title`` value type (``str`` instead of the raw object)
    — the same JSON-string boundary the whole port uses (cf. NaN
    payloads, lone surrogates).
    """
    if record_id is None:
        return None, None
    if isinstance(record_id, str):
        return record_id, None
    rid = str(record_id)
    try:
        probe = json.dumps(record_id, allow_nan=False)
        fallback_json = None if isinstance(record_id, tuple) else probe
    except (TypeError, ValueError):
        fallback_json = None
    return rid, fallback_json


# Backward-compatible alias (M12 name).
def _yahoo_fallback(record_id):
    """Alias of :func:`_json_fallback` (kept for compatibility)."""
    return _json_fallback(record_id)


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
        # Record building in Rust (src/adapters.rs); `raw` re-attached
        # here so it stays byte-identical `json.dumps` of each quote.
        body = resp.json()
        quotes = body.get("quotes", []) or []
        records = json.loads(
            _rust.yahoo_parse_search(json.dumps(body), max_results)
        )
        for rec, q in zip(records, quotes[:max_results]):
            rec["raw"] = json.dumps(q)
        return records

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/v8/finance/chart/{record_id}", params, {}
        )
        resp = httpx.get(url, headers=headers, params=params, timeout=20.0)
        resp.raise_for_status()
        # Meta parsing in Rust (src/adapters.rs); `raw` re-attached here.
        # `_yahoo_fallback` keeps the exact `record_id` spelling (see it
        # for the tuple/set/object boundary note).
        rid, fallback_json = _yahoo_fallback(record_id)
        body = resp.json()
        both = json.loads(
            _rust.yahoo_parse_fetch(json.dumps(body), rid, fallback_json)
        )
        rec, meta = both["record"], both["meta"]
        rec["raw"] = json.dumps(meta)
        return [rec]

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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each element.
        body = resp.json()
        elements = body.get("elements", [])
        records = json.loads(
            _rust.overpass_parse_search(json.dumps(body), max_results)
        )
        for rec, el in zip(records, elements[:max_results]):
            rec["raw"] = json.dumps(el)
        return records

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
        # Row building in Rust (src/adapters.rs). Each record carries
        # its rebuilt `raw` payload; it is re-dumped here so `raw`
        # stays byte-identical `json.dumps` of the decoded row.
        body = resp.json()
        records = json.loads(
            _rust.census_parse_search(json.dumps(body), dataset, max_results)
        )
        for rec in records:
            rec["raw"] = json.dumps(rec.pop("raw"))
        return records

    def fetch(self, record_id, params=None):
        # Lookup one geography row by id within a dataset spec.
        self._enforce_delay()
        dataset, extra = _parse_census_query(record_id)
        url, params, headers = self.inject_auth(
            f"{self.BASE}/{dataset}", {**extra, **dict(params or {})}, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        # Row building in Rust (src/adapters.rs); `raw` re-dumped here.
        # The id match compares Python-`str()` on both sides.
        body = resp.json()
        recs = json.loads(
            _rust.census_parse_fetch(
                json.dumps(body), dataset, str(record_id))
        )
        for rec in recs:
            rec["raw"] = json.dumps(rec.pop("raw"))
        return recs

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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each result.
        body = resp.json()
        results = body.get("results", [])
        records = json.loads(
            _rust.courtlistener_parse_search(json.dumps(body), max_results)
        )
        for rec, r in zip(records, results[:max_results]):
            rec["raw"] = json.dumps(r)
        return records

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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here.
        body = resp.json()
        rec = json.loads(_rust.courtlistener_parse_fetch(json.dumps(body)))
        rec["raw"] = json.dumps(body)
        return [rec]

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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each document.
        body = resp.json()
        docs = body.get("results", body.get("documents", [])) or []
        records = json.loads(
            _rust.fed_parse_search(json.dumps(body), max_results)
        )
        for rec, d in zip(records, docs[:max_results]):
            rec["raw"] = json.dumps(d)
        return records

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/documents/{record_id}.json", params, {}
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        # Row building in Rust (src/adapters.rs); `raw` re-attached here.
        body = resp.json()
        rec = json.loads(_rust.fed_parse_fetch(json.dumps(body)))
        rec["raw"] = json.dumps(body)
        return [rec]

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
            # Row building in Rust (src/adapters.rs); `raw` re-attached
            # here so it stays byte-identical `json.dumps` of each paper.
            body = resp.json()
            papers = body.get("collection", [])
            records = json.loads(
                _rust.biorxiv_parse_collection(
                    json.dumps(body), max_results, self.server)
            )
            for rec, p in zip(records, papers[:max_results]):
                rec["raw"] = json.dumps(p)
            return records
        papers = self._lookup(q)
        body = {"collection": papers}
        records = json.loads(
            _rust.biorxiv_parse_collection(
                json.dumps(body), max_results, self.server)
        )
        for rec, p in zip(records, papers[:max_results]):
            rec["raw"] = json.dumps(p)
        return records

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        if not re.match(r"^10\.\d{4,9}/\S+", str(record_id)):
            return []
        papers = self._lookup_doi(str(record_id))
        if not papers:
            return []
        # Row building in Rust (src/adapters.rs); `raw` re-attached here.
        body = {"collection": papers}
        records = json.loads(
            _rust.biorxiv_parse_fetch(json.dumps(body), self.server)
        )
        records[0]["raw"] = json.dumps(papers[0])
        return records

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

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/search",
            {"query": query, "page_size": min(max_results, 100)},
            {},
        )
        resp = httpx.get(url, headers=headers, params=params, timeout=20.0)
        resp.raise_for_status()
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each item.
        body = resp.json()
        items = body.get("data", []) if isinstance(body, dict) else body
        records = json.loads(
            _rust.chemrxiv_parse_search(json.dumps(body), max_results)
        )
        for rec, i in zip(records, items[:max_results]):
            rec["raw"] = json.dumps(i)
        return records

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/{record_id}", params, {}
        )
        resp = httpx.get(url, headers=headers, params=params, timeout=20.0)
        resp.raise_for_status()
        # Row building in Rust (src/adapters.rs); `raw` re-attached here.
        body = resp.json()
        item = body.get("data", body) if isinstance(body, dict) else body
        if not item:
            return []
        records = json.loads(_rust.chemrxiv_parse_fetch(json.dumps(body)))
        if isinstance(item, list):
            records[0]["raw"] = json.dumps(item[0])
        else:
            records[0]["raw"] = json.dumps(item)
        return records

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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # (search note rows also take dumps of the whole body).
        rows = body.get("bestMatches", [])
        records = json.loads(
            _rust.alphavantage_parse_search(
                json.dumps(body), json.dumps(query), max_results)
        )
        if not rows:
            records[0]["raw"] = json.dumps(body)
            return records
        for rec, r in zip(records, rows[:max_results]):
            rec["raw"] = json.dumps(r)
        return records

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        url, params, headers = self.inject_auth(
            f"{self.BASE}/query",
            {"function": "TIME_SERIES_DAILY", "symbol": record_id, "apikey": self.api_key},
            {},
        )
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        # Row building in Rust (src/adapters.rs), including the empty
        # note row; `raw` is dumps of the OHLCV object (note rows take
        # dumps of the whole body — a JSON null meta marks them).
        body = resp.json()
        rid = str(record_id)
        both = json.loads(_rust.alphavantage_parse_fetch(json.dumps(body), rid))
        rec, meta = both["record"], both["meta"]
        if meta is None:
            rec["raw"] = json.dumps(body)
        else:
            rec["raw"] = json.dumps(meta)
        return [rec]


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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each hit.
        body = resp.json()
        hits = body.get("results", [])
        records = json.loads(
            _rust.oldp_parse_search(json.dumps(body), max_results)
        )
        for rec, c in zip(records, hits[:max_results]):
            rec["raw"] = json.dumps(c)
        return records

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        rid = str(record_id or "")
        if rid.startswith("law:"):
            url = f"{self.BASE}/laws/{rid[4:]}/"
        else:
            url = f"{self.BASE}/cases/{rid}/"
        resp = httpx.get(url, timeout=20.0)
        resp.raise_for_status()
        # Row building in Rust (src/adapters.rs); `raw` re-attached here.
        body = resp.json()
        if rid.startswith("law:"):
            rec = json.loads(_rust.oldp_parse_law(json.dumps(body), rid))
        else:
            rec = json.loads(_rust.oldp_parse_case(json.dumps(body)))
        rec["raw"] = json.dumps(body)
        return [rec]


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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each hit's columns.
        results = body.get("results", [])
        records = json.loads(_rust.hudoc_parse_search(json.dumps(body)))
        for rec, r in zip(records, results):
            rec["raw"] = json.dumps(r.get("columns", {}))
        return records

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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here.
        body = resp.json()
        records = json.loads(_rust.hudoc_parse_fetch(json.dumps(body)))
        records[0]["raw"] = json.dumps(results[0].get("columns", {}))
        return records


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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each result.
        body = resp.json()
        results = body.get("results", [])
        records = json.loads(
            _rust.govinfo_parse_search(json.dumps(body), max_results)
        )
        for rec, r in zip(records, results[:max_results]):
            rec["raw"] = json.dumps(r)
        return records

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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here.
        body = resp.json()
        rec = json.loads(_rust.govinfo_parse_fetch(json.dumps(body), rid))
        rec["raw"] = json.dumps(body)
        return [rec]


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
        # Implemented in Rust (src/adapters.rs).
        return _rust.frankfurter_split_pair(spec)

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
        # Row parsing in Rust (src/adapters.rs); `raw` re-attached per row
        # from the record fields, exactly as `_row` built it.
        records = json.loads(
            _rust.frankfurter_parse_rates(
                json.dumps(body), base, date, max_results
            )
        )
        for rec in records:
            fields = rec["fields"]
            rec["raw"] = json.dumps({
                "base": fields["base"], "quote": fields["quote"],
                "rate": fields["rate"], "date": fields["date"],
            })
        return records

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

    def _run(self, code: str, filters: dict, max_results: int) -> list:
        params = {"format": "JSON", "lang": "EN"}
        params.update(filters)
        resp = httpx.get(f"{self.BASE}/data/{code}", params=params, timeout=30.0)
        resp.raise_for_status()
        # Cell unpacking + row building in Rust (src/adapters.rs).
        # The kernel returns (record, dims, payload) triples: `fields`
        # dims are rebuilt here with native types (dims may be
        # non-strings in hostile cubes) and `raw` stays byte-identical
        # `json.dumps` of the payload.
        data = resp.json()
        triples = json.loads(
            _rust.eurostat_parse_cells(json.dumps(data), code, max_results)
        )
        out = []
        for triple in triples:
            rec = triple["record"]
            dims = {d: c for d, c in triple["dims"]}
            rec["fields"] = {"dataset": code, **dims,
                               "value": triple["payload"]["value"]}
            rec["raw"] = json.dumps(triple["payload"])
            out.append(rec)
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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each coin.
        body = resp.json()
        coins = body.get("coins", [])
        records = json.loads(
            _rust.coingecko_parse_search(json.dumps(body), max_results)
        )
        for rec, coin in zip(records, coins[:max_results]):
            rec["raw"] = json.dumps(coin)
        return records

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
        # Row building in Rust (src/adapters.rs); `raw` re-attached here.
        # A JSON null marks the empty row list (falsy payloads).
        rec_json = _rust.coingecko_parse_markets(json.dumps(rows), cid)
        if rec_json == "null":
            return []
        rec = json.loads(rec_json)
        rec["raw"] = json.dumps(rows[0])
        return [rec]


# ────────────────────────────────────────────────────────────────
# Wave 4 — patent offices (2026-09). All key-gated: there is no keyless
# patent search API left (see docs/PATENT_LANDSCAPE_2026-09-05.md).
# Shapes follow the offices' public documentation; authed live paths
# are covered by key-gated smoke tests, not the offline suite.
# ────────────────────────────────────────────────────────────────

class EpoOpsAdapter(ResourceAdapter):
    """European patents via EPO Open Patent Services (OPS 3.2).

    Requires ``GOSSAMER_EPO_KEY`` + ``GOSSAMER_EPO_SECRET`` (free OPS
    registration; OAuth2 client-credentials). ``search`` runs a CQL query
    (``ti=``, ``pa=``, ``pn=``…) over published data; ``fetch`` pulls one
    publication by EPODOC number (``EP1234567``). INPADOC family/legal-event
    data covers JP/CN/DE documents too, which makes this the default
    ``patent`` provider.
    """

    name = "epo"
    domain = "patent"
    requires_key = True
    BASE = "https://ops.epo.org/3.2"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
    ):
        self.api_key = api_key or _env_get("GOSSAMER_EPO_KEY", "")
        self.api_secret = api_secret or _env_get("GOSSAMER_EPO_SECRET", "")
        self._token = ""
        self._token_expires = 0.0
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=1.0, jitter=0.5)
        )

    def _auth_headers(self) -> dict:
        import time as _time

        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "EpoOpsAdapter needs GOSSAMER_EPO_KEY + GOSSAMER_EPO_SECRET "
                "(free OPS registration)."
            )
        if not self._token or _time.time() >= self._token_expires - 30:
            resp = httpx.post(
                f"{self.BASE}/auth/accesstoken",
                data={"grant_type": "client_credentials"},
                auth=(self.api_key, self.api_secret),
                timeout=20.0,
            )
            resp.raise_for_status()
            body = resp.json()
            self._token = body.get("access_token", "")
            try:
                ttl = int(body.get("expires_in", 1200))
            except (TypeError, ValueError):
                ttl = 1200
            self._token_expires = _time.time() + max(60, ttl)
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/xml"}

    @staticmethod
    def _text(element, limit: int = 400) -> str:
        parts = []
        for el in element.iter():
            if _local_name(el.tag) in ("invention-title", "title") and el.text:
                lang = el.attrib.get("lang", "")
                parts.append(f"[{lang}] {el.text.strip()}" if lang else el.text.strip())
        return " ".join(parts)[:limit]

    def _row(self, doc) -> Dict[str, str]:
        number, kind, date, applicants = "", "", "", []
        for el in doc.iter():
            lname = _local_name(el.tag)
            if lname == "document-id" and el.attrib.get("document-id-type") == "epodoc":
                for child in el:
                    cname = _local_name(child.tag)
                    if cname == "doc-number":
                        number = (child.text or "").strip()
                    elif cname == "kind":
                        kind = (child.text or "").strip()
                    elif cname == "date":
                        date = (child.text or "").strip()[:10]
            elif lname in ("applicant-name", "inventor-name"):
                name = (el.findtext(".//{*}name") or "").strip()
                if name:
                    applicants.append(name)
        epodoc = f"{number}{kind}"
        title = self._text(doc) or epodoc
        return {
            "source": "epo",
            "id": epodoc or number,
            "title": title,
            "url": f"https://worldwide.espacenet.com/patent/search?q=pn%3D{epodoc}" if epodoc else "",
            "published": date,
            "snippet": f"{title} — {', '.join(applicants[:3])}".strip(" —"),
            "fields": {
                "publication_number": number,
                "kind": kind,
                "applicants": ", ".join(applicants[:5]),
            },
            "raw": json.dumps({"epodoc": epodoc, "title": title, "date": date}),
        }

    def search(self, query, max_results=5):
        # Missing credentials never succeed on retry: fail fast instead of
        # burning backoff sleeps (same pattern as BioRxivAdapter).
        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "EpoOpsAdapter needs GOSSAMER_EPO_KEY + GOSSAMER_EPO_SECRET "
                "(free OPS registration)."
            )
        return super().search(query, max_results)

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        q = (query or "").strip()
        if not q:
            raise ValueError("EpoOpsAdapter search needs a CQL query (e.g. ti=quantum)")
        headers = self._auth_headers()
        end = min(max(1, max_results), 100)
        resp = httpx.get(
            f"{self.BASE}/rest-services/published-data/search",
            params={"q": q, "Range": f"1-{end}"},
            headers=headers,
            timeout=30.0,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        out = []
        for el in root.iter():
            if _local_name(el.tag) != "exchange-document":
                continue
            out.append(self._row(el))
            if len(out) >= max_results:
                break
        return out

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        rid = str(record_id or "").strip()
        if not rid:
            raise ValueError("EpoOpsAdapter fetch needs an EPODOC number")
        headers = self._auth_headers()
        resp = httpx.get(
            f"{self.BASE}/rest-services/published-data/publication/epodoc/{rid}",
            headers=headers,
            timeout=30.0,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        for el in root.iter():
            if _local_name(el.tag) == "exchange-document":
                return [self._row(el)]
        return []


class KiprisAdapter(ResourceAdapter):
    """Korean patents/utility models via the KIPRIS Plus open API.

    Requires ``GOSSAMER_KIPRIS_KEY`` (per-user ``serviceKey``; free
    development tier, paid operation tier). ``search`` runs a keyword
    search (``getWordSearch``); ``fetch`` looks up one application number.
    Responses are XML-only. One key per deployment (ToS §11).
    """

    name = "kipris"
    domain = "patent"
    requires_key = True
    BASE = "http://kipo-api.kipi.or.kr/openapi/service"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        api_key: Optional[str] = None,
    ):
        self.api_key = api_key or _env_get("GOSSAMER_KIPRIS_KEY", "")
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=1.0, jitter=0.5)
        )

    def _require_key(self):
        if not self.api_key:
            raise RuntimeError("KiprisAdapter needs GOSSAMER_KIPRIS_KEY (free dev tier).")

    @staticmethod
    def _item_to_dict(item) -> dict:
        """Generic XML item -> {tag: text} (field names vary by service)."""
        out = {}
        for child in item:
            name = _local_name(child.tag)
            out[name] = (child.text or "").strip()
        return out

    @staticmethod
    def _row(d: dict) -> Dict[str, str]:
        app_no = d.get("applicationNumber", d.get("application_number", ""))
        title = d.get("inventionTitle", d.get("title", app_no))
        return {
            "source": "kipris",
            "id": app_no,
            "title": title,
            "url": "",
            "published": d.get("publicationDate", d.get("registrationDate", "")),
            "snippet": f"{title} — {d.get('applicantName', '')}".strip(" —"),
            "fields": {
                "applicant": d.get("applicantName", ""),
                "status": d.get("applicationStatus", d.get("registerStatus", "")),
            },
            "raw": json.dumps(d, ensure_ascii=False),
        }

    def _items(self, service: str, operation: str, params: dict):
        self._require_key()
        query = {"serviceKey": self.api_key, "numOfRows": 10, **params}
        resp = httpx.get(f"{self.BASE}/{service}/{operation}", params=query, timeout=25.0)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        items = []
        for el in root.iter():
            if _local_name(el.tag) == "item":
                items.append(self._item_to_dict(el))
        return items

    def search(self, query, max_results=5):
        if not self.api_key:
            raise RuntimeError("KiprisAdapter needs GOSSAMER_KIPRIS_KEY (free dev tier).")
        return super().search(query, max_results)

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        q = (query or "").strip()
        if not q:
            raise ValueError("KiprisAdapter search needs a keyword query")
        items = self._items(
            "patUtliInfoSearchService", "getWordSearch",
            {"word": q, "numOfRows": min(max_results, 100)},
        )
        return [self._row(d) for d in items[:max_results]]

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        rid = str(record_id or "").strip()
        if not rid:
            raise ValueError("KiprisAdapter fetch needs an application number")
        items = self._items(
            "patUtliInfoSearchService", "getWordSearch",
            {"word": rid, "numOfRows": 5},
        )
        return [self._row(items[0])] if items else []


class PatentsViewAdapter(ResourceAdapter):
    """US patents via the PatentsView Search Platform (v2 search API).

    Requires ``GOSSAMER_PATENTSVIEW_API_KEY`` (``X-Api-Key`` header; request
    via the PatentsView service desk). The legacy keyless v1 query API is
    retired. ``search`` runs a title/abstract full-text query
    (``api/v1/patents``) or accepts a raw query dict passthrough;
    ``fetch`` pulls one patent by number (``api/v1/patents/<id>/``).
    Base URL is configurable (``GOSSAMER_PATENTSVIEW_BASE``) in case the
    platform host moves again.
    """

    name = "patentsview"
    domain = "patent"
    requires_key = True
    DEFAULT_BASE = "https://search.patentsview.org"

    def __init__(
        self,
        delay: Optional[Union[float, RateLimit]] = None,
        fetch_delay: Optional[float] = None,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.api_key = api_key or _env_get("GOSSAMER_PATENTSVIEW_API_KEY", "")
        self.base_url = (
            base_url or _env_get("GOSSAMER_PATENTSVIEW_BASE", "") or self.DEFAULT_BASE
        ).rstrip("/")
        self._last_search = 0.0
        self._last_fetch = 0.0
        self._init_rate_limit(
            delay if delay is not None else RateLimit(search_interval=1.0, jitter=0.5)
        )

    def _headers(self) -> dict:
        if not self.api_key:
            raise RuntimeError(
                "PatentsViewAdapter needs GOSSAMER_PATENTSVIEW_API_KEY "
                "(service desk)."
            )
        return {"X-Api-Key": self.api_key, "Accept": "application/json"}

    def search(self, query, max_results=5):
        # Fail fast without credentials (see EpoOpsAdapter).
        self._headers()
        return super().search(query, max_results)

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        headers = self._headers()
        if isinstance(query, dict):
            q = query.get("q", {})
            fields = query.get("f", ["patent_number", "patent_title", "patent_date"])
        else:
            text = (query or "").strip()
            if not text:
                raise ValueError("PatentsViewAdapter search needs a text query")
            q = {"_text_all": {"patent_title": text}}
            fields = ["patent_number", "patent_title", "patent_date"]
        resp = httpx.get(
            f"{self.base_url}/api/v1/patents/",
            params={"q": json.dumps(q), "f": json.dumps(fields),
                    "o": json.dumps({"per_page": min(max_results, 100)})},
            headers=headers,
            timeout=25.0,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("error"):
            raise RuntimeError(f"PatentsView error: {body.get('error')}")
        # Row building in Rust (src/adapters.rs); `raw` re-attached here
        # so it stays byte-identical `json.dumps` of each patent.
        patents = body.get("patents", [])
        records = json.loads(
            _rust.patentsview_parse_search(json.dumps(body), max_results)
        )
        for rec, p in zip(records, patents[:max_results]):
            rec["raw"] = json.dumps(p)
        return records

    def fetch(self, record_id, params=None):
        self._enforce_delay()
        headers = self._headers()
        rid = str(record_id or "").strip()
        if not rid:
            raise ValueError("PatentsViewAdapter fetch needs a patent number")
        resp = httpx.get(
            f"{self.base_url}/api/v1/patents/{rid}/",
            headers=headers,
            timeout=25.0,
        )
        resp.raise_for_status()
        body = resp.json()
        # Row building in Rust (src/adapters.rs); `raw` re-attached here.
        records = json.loads(_rust.patentsview_parse_fetch(json.dumps(body)))
        if not records:
            return []
        if isinstance(body, dict) and body.get("patents"):
            records[0]["raw"] = json.dumps(body["patents"][0])
        else:
            records[0]["raw"] = json.dumps(body)
        return records


