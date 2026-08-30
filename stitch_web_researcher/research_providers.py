"""Domain data-source adapters built on the unified :class:`ResourceAdapter`.

These demonstrate the ``ResourceAdapter`` contract for the scholarly / geo
resources called out in ``docs/research_access_layer_plan.md``. Each adapter
owns only its request + parse logic; politeness, quota, auth injection, live
header retuning and retry/backoff come free from the base class.

Built so far:
  * :class:`OpenAlexAdapter`  — scholarly works search / lookup (scholarly)
  * :class:`OpenMeteoAdapter` — weather/climate + place lookup (geo)
"""

import json
from typing import Dict, List, Optional, Tuple, Union

import httpx

from stitch_web_researcher.search_providers import RateLimit, RateState, ResourceAdapter

_UA = "stitch-web-researcher/0.4.9"


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
            url, {"q": query, "per_page": min(max_results, 200)}, {}
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
                    "title": (w.get("title") or [""])[0],
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
                "title": (w.get("title") or [""])[0],
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
