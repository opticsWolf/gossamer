"""Parity: adapter parse kernels, pilot batch (v0.8.10) vs ``src/adapters.rs``.

Covers Open-Meteo geocoding/forecast parsing and Frankfurter pair
splitting + rate parsing (v2 list and map shapes), over realistic
fixtures plus hostile shapes (nulls, wrong types, empties, exotic
scalars) with exact error comparison. URL/param building, HTTP,
keys, rate limiting and retry stay Python — the existing httpx-mock
tests cover that seam unchanged.
"""

import json

import pytest

from gossamer import _core


# ── fixtures ─────────────────────────────────────────────────────

GEOCODE = {
    "results": [
        {"name": "Berlin", "admin1": "Berlin", "country": "Germany",
         "latitude": 52.52, "longitude": 13.41,
         "url": "https://open-meteo.com/en/docs"},
        {"name": "Berlin", "admin1": "New Hampshire", "country": "United States",
         "latitude": 44.47, "longitude": -71.18},
        {"name": "Nowhere", "latitude": 0.0, "longitude": 0.0},
    ]
}
FORECAST = {
    "latitude": 52.52, "longitude": 13.41,
    "current": {"temperature_2m": 18.5, "weathercode": 3,
                "time": "2024-01-01T12:00"},
}
FRANK_V2 = [
    {"base": "USD", "quote": "EUR", "rate": 0.92, "date": "2024-01-01"},
    {"base": "USD", "quote": "JPY", "rate": 150.0, "date": "2024-01-01"},
]
FRANK_MAP = {"base": "USD", "date": "2024-01-01",
             "quotes": {"EUR": 0.92, "JPY": 150.0}}


def _outcome(fn, *args):
    try:
        return False, fn(*args)
    except Exception as e:  # noqa: BLE001
        return True, f"{type(e).__name__}: {e}"


def _rs_search(body, max_results=5):
    return json.loads(_core.openmeteo_parse_search(
        json.dumps(body), max_results,
        "https://api.open-meteo.com/v1/forecast"))


def _v_search(body, max_results=5):
    hits = body.get("results", [])
    out = []
    for h in hits[:max_results]:
        title = ", ".join(
            part for part in (h.get("name"), h.get("admin1"), h.get("country")) if part
        )
        out.append({
            "source": "open-meteo",
            "id": f"{h.get('latitude', 0)},{h.get('longitude', 0)}",
            "title": title,
            "url": h.get("url") or (
                f"https://api.open-meteo.com/v1/forecast?latitude={h.get('latitude')}"
                f"&longitude={h.get('longitude')}"),
            "snippet": h.get("country", ""),
        })
    return out


SEARCH_CASES = [
    GEOCODE,
    {"results": []},
    {},
    {"results": None},
    {"results": "abc"},
    {"results": {"a": 1}},
    {"results": [None]},
    {"results": ["x"]},
    {"results": [{}]},
    {"results": [{"name": None, "latitude": None}]},
    {"results": [{"name": 5, "admin1": "R", "country": "C",
                  "latitude": 1, "longitude": 2}]},
    {"results": [{"name": ["a"], "latitude": 1, "longitude": 2}]},
    {"results": [{"name": "N", "country": {"x": 1},
                  "latitude": 1, "longitude": 2}]},
    {"results": [{"name": "N", "url": "", "latitude": 1, "longitude": 2}]},
    {"results": [{"name": "N", "url": 0, "latitude": 1, "longitude": 2}]},
]


@pytest.mark.parametrize("body", SEARCH_CASES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_openmeteo_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


def test_openmeteo_forecast_parity():
    def _v(data, lat=52.52, lon=13.41):
        current = data.get("current", {})
        return {
            "source": "open-meteo",
            "id": f"{lat},{lon}",
            "title": "Open-Meteo forecast",
            "url": ("https://api.open-meteo.com/v1/forecast"
                    f"?latitude={lat}&longitude={lon}"),
            "snippet": ", ".join(f"{k}={v}" for k, v in current.items()),
        }

    for data in [FORECAST, {"current": {}}, {"current": None},
                 {"current": {"a": [1, {"b": 2}], "c": None}},
                 {"other": 1}]:
        py_raised, py_val = _outcome(_v, data)
        rs_raised, rs_val = _outcome(
            lambda d: json.loads(_core.openmeteo_parse_forecast(
                json.dumps(d), "52.52", "13.41",
                "https://api.open-meteo.com/v1/forecast")), data)
        assert (py_raised, rs_raised) == (py_raised, py_raised), data
        assert rs_val == py_val, data
    # Non-dict current raises AttributeError on both sides (covered above).


SPLIT_CASES = [
    "USD/EUR", "usd eur", "USD", "  usd  ", "USD/EUR/JPY",
    "", "   ", None, "USDD", "USD/EURO", "US/EUR", "usd-eur",
    "USD//EUR", "/EUR", "EUR/",
]


@pytest.mark.parametrize("spec", SPLIT_CASES)
def test_split_pair_parity(spec):
    import re as _re

    def _v(spec):
        parts = _re.split(r"[\s/]+", (spec or "").strip().upper())
        parts = [p for p in parts if p]
        if not parts:
            raise ValueError(
                "FrankfurterAdapter needs a currency (USD) or pair (USD/EUR).")
        base = parts[0]
        if not _re.fullmatch(r"[A-Z]{3}", base):
            raise ValueError(f"Not a currency code: {parts[0]!r}")
        quote = parts[1] if len(parts) > 1 else None
        if quote is not None and not _re.fullmatch(r"[A-Z]{3}", quote):
            raise ValueError(f"Not a currency code: {parts[1]!r}")
        return base, quote

    py_raised, py_val = _outcome(_v, spec)
    rs_raised, rs_val = _outcome(_core.frankfurter_split_pair, spec)
    assert (py_raised, rs_raised) == (py_raised, py_raised), spec
    assert rs_val == py_val, spec


def _v_rates(body, base="USD", date=None, max_results=5):
    rows = body if isinstance(body, list) else [body]
    out = []

    def _row(b, q, rate, day):
        return {
            "source": "frankfurter", "id": f"{b}/{q}",
            "title": f"{b}/{q} = {rate} ({day})", "url": "",
            "published": day,
            "snippet": (f"1 {b} = {rate} {q} on {day} "
                        "(central-bank reference rates)"),
            "fields": {"base": b, "quote": q, "rate": rate, "date": day},
        }

    for row in rows:
        if not isinstance(row, dict):
            continue
        day = str(row.get("date", date or ""))
        b = row.get("base", base)
        pairs = []
        if "quote" in row:
            pairs = [(row.get("quote"), row.get("rate"))]
        for q, rate in (row.get("quotes", {}) or {}).items():
            pairs.append((q, rate))
        for q, rate in pairs:
            if not q:
                continue
            out.append(_row(b, q, rate, day))
            if len(out) >= max_results:
                return out
    return out


RATES_CASES = [
    FRANK_V2, FRANK_MAP,
    {"base": "USD"},
    {"base": "USD", "quotes": {}},
    {"base": "USD", "quotes": None},
    {"base": "USD", "quotes": "abc"},
    {"base": "USD", "quotes": [1]},
    {"base": "USD", "quote": None, "rate": 1.0},
    {"base": "USD", "quote": "", "rate": 1.0, "date": "2024-05-01"},
    {"base": "USD", "quote": "EUR"},
    {"quotes": {"EUR": 0.9}},
    {"base": None, "quote": "EUR", "rate": "high", "date": 20240101},
    [{"nope": 1}, "str", None, 42],
    [],
    {"base": "USD", "quote": "EUR", "rate": {"x": 1}},
]


@pytest.mark.parametrize("body", RATES_CASES)
@pytest.mark.parametrize("max_results", [1, 5, -2])
def test_frankfurter_rates_parity(body, max_results):
    py_raised, py_val = _outcome(_v_rates, body, "USD", None, max_results)
    rs_raised, rs_val = _outcome(
        lambda b, m: json.loads(_core.frankfurter_parse_rates(
            json.dumps(b), "USD", None, m)), body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


# --- batch 2: Yahoo / NVD / Zenodo kernels (v0.8.11) ------------------
# Vendored originals are verbatim copies of the retired
# `NvdAdapter._row`, `ZenodoAdapter._names/_hit`, the Yahoo/NVD/Zenodo
# `_search_impl`/`fetch` parse logic and the `_first_desc`/`_strip_tags`
# helpers (minus `raw`, which crosses the boundary separately).

import re as _re2


def _v_first_desc(cve, limit=240):
    for d in cve.get("descriptions", []) or []:
        if d.get("lang") == "en" or not d.get("lang"):
            return " ".join((d.get("value") or "").split())[:limit]
    return ""


def _v_strip_tags(text):
    if not text:
        return ""
    return _re2.sub(r"<[^>]+>", " ", text)


YAHOO_QUOTES = {
    "quotes": [
        {"symbol": "AAPL", "shortname": "Apple Inc.",
         "exchange": "NMS", "quoteType": "EQUITY",
         "marketCap": 3000000000000},
        {"symbol": "SAP.DE", "shortName": "SAP SE",
         "exchange": "GER", "quoteType": "EQUITY"},
        {"symbol": "X"},
    ]
}


def _v_yahoo_search(body, max_results=5):
    quotes = body.get("quotes", []) or []
    out = []
    for q in quotes[:max_results]:
        name = q.get("shortname") or q.get("shortName") or q.get("symbol", "")
        out.append(
            {
                "source": "yahoo",
                "id": q.get("symbol", ""),
                "title": name,
                "url": f"https://finance.yahoo.com/quote/{q.get('symbol', '')}",
                "snippet": (
                    f"{name} \u2014 {q.get('exchange', '')} "
                    f"{q.get('quoteType', '')}"
                ),
                "fields": {
                    "yahoo": {
                        "exchange": q.get("exchange", ""),
                        "quote_type": q.get("quoteType", ""),
                        "market_cap": q.get("marketCap", ""),
                    }
                },
            }
        )
    return out


def _rs_yahoo_search(body, max_results=5):
    return json.loads(_core.yahoo_parse_search(
        json.dumps(body), max_results))


YAHOO_SEARCH_CASES = [
    YAHOO_QUOTES,
    {},
    {"quotes": None},
    {"quotes": []},
    {"quotes": ""},
    {"quotes": 0},
    {"quotes": {}},
    {"quotes": "ab"},
    {"quotes": 5},
    {"quotes": True},
    {"quotes": [None]},
    {"quotes": ["x"]},
    {"quotes": [""]},
    {"quotes": [{}]},
    {"quotes": [{"symbol": "A"}]},
    {"quotes": [{"symbol": None, "shortname": None}]},
    {"quotes": [{"symbol": 5, "shortname": 7, "exchange": ["N"],
                 "quoteType": {"t": 1}, "marketCap": 1.5}]},
    {"quotes": [{"shortname": "N", "exchange": 0, "quoteType": False}]},
    {"quotes": [5]},
    {"quotes": [[1]]},
    {"quotes": [{"symbol": "A", "shortname": ""}]},
    {"quotes": [{"symbol": ["A"], "shortname": ["N"]}]},
]


@pytest.mark.parametrize("body", YAHOO_SEARCH_CASES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_yahoo_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_yahoo_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_yahoo_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


YAHOO_CHART = {
    "chart": {
        "result": [
            {"meta": {
                "symbol": "AAPL", "longName": "Apple Inc.",
                "regularMarketPrice": 230.5, "currency": "USD",
                "fullExchangeName": "NasdaqGS", "previousClose": 229.0,
            }}
        ]
    }
}


def _v_yahoo_fetch(body, record_id):
    meta = (
        (body.get("chart", {}) or {}).get("result", [{}])[0]
        .get("meta", {})
    )
    return {
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
    }


def _rs_yahoo_fetch(body, rid, fallback_json):
    both = json.loads(_core.yahoo_parse_fetch(
        json.dumps(body), rid, fallback_json))
    return both["record"]


YAHOO_FETCH_BODIES = [
    YAHOO_CHART,
    {},
    {"chart": None},
    {"chart": ""},
    {"chart": 0},
    {"chart": {}},
    {"chart": "x"},
    {"chart": 5},
    {"chart": ["x"]},
    {"chart": {"result": None}},
    {"chart": {"result": [{}]}},
    {"chart": {"result": []}},
    {"chart": {"result": ["ab"]}},
    {"chart": {"result": [""]}},
    {"chart": {"result": [5]}},
    {"chart": {"result": [None]}},
    {"chart": {"result": "x"}},
    {"chart": {"result": {}}},
    {"chart": {"result": 0}},
    {"chart": {"result": [{"meta": None}]}},
    {"chart": {"result": [{"meta": 5}]}},
    {"chart": {"result": [{"meta": "x"}]}},
    {"chart": {"result": [{"meta": ["x"]}]}},
    {"chart": {"result": [{"meta": {}}]}},
    {"chart": {"result": [{"meta": {
        "symbol": None, "longName": 0, "shortName": False,
        "regularMarketPrice": None, "currency": ["USD"],
        "fullExchangeName": {"e": 1}, "previousClose": 0}}]}},
    {"chart": {"result": [{"meta": {"shortName": "Short Only"}}]}},
    {"chart": {"result": [{"other": 1}], "error": None}},
]

# (record_id, rid, fallback_json): exact boundary spellings — every raw
# `record_id` here round-trips identically, so kernel output must equal
# the original's. Non-JSON-native ids (tuple/set/object) are documented
# to arrive str()-rendered (see `test_yahoo_fallback_helper`); NaN
# payloads cannot cross the JSON boundary at all.
YAHOO_FALLBACKS = [
    (None, None, None),
    ("AAPL", "AAPL", None),
    ("", "", None),
    (5, "5", "5"),
    (1.5, "1.5", "1.5"),
    (True, "True", "true"),
    ([1, 2], "[1, 2]", "[1, 2]"),
    ({"a": 1}, "{'a': 1}", '{"a": 1}'),
]


@pytest.mark.parametrize("body", YAHOO_FETCH_BODIES)
@pytest.mark.parametrize("fb", YAHOO_FALLBACKS)
def test_yahoo_fetch_parity(body, fb):
    record_id, rid, fallback_json = fb
    py_raised, py_val = _outcome(_v_yahoo_fetch, body, record_id)
    rs_raised, rs_val = _outcome(_rs_yahoo_fetch, body, rid, fallback_json)
    assert (py_raised, rs_raised) == (py_raised, py_raised), (body, fb)
    assert rs_val == py_val, (body, fb)


def test_yahoo_fallback_helper():
    from gossamer.research_providers import _yahoo_fallback
    assert _yahoo_fallback(None) == (None, None)
    assert _yahoo_fallback("AAPL") == ("AAPL", None)
    assert _yahoo_fallback(5) == ("5", "5")
    assert _yahoo_fallback([1, 2]) == ("[1, 2]", "[1, 2]")
    # Not expressible in JSON: str()-rendered (documented boundary).
    assert _yahoo_fallback((1, 2)) == ("(1, 2)", None)
    assert _yahoo_fallback({1, 2}) == (str({1, 2}), None)
    assert _yahoo_fallback(float("nan")) == ("nan", None)


def _v_nvd_route(q):
    m = _re2.match(r"^CVE-\d{4}-\d{4,}$", q, _re2.IGNORECASE)
    if m:
        return "cveId", q.upper()
    return "keywordSearch", q


NVD_ROUTE_CASES = [
    "CVE-2021-44228", "cve-2021-44228", "Cve-2021-44228",
    "CVE-2021-44228123", "CVE-0000-0000", "CVE-12345-6789012345",
    "CVE-21-44228", "CVE-2021-442", "CVE-2021-4422a8",
    "XCVE-2021-44228", "CVE-2021-44228x", "CVE 2021-44228",
    "keyword search", "", "  ",
    "CVE-2021-44228\n", "CVE-2021-44228\r", "CVE-2021-44228\t",
    "CVE-2021-44228 ", " CVE-2021-44228", "CVE-2021-44228\n\n",
    "log4shell", "CVE-2021-44228/and/more",
]


@pytest.mark.parametrize("q", NVD_ROUTE_CASES)
def test_nvd_route_parity(q):
    py_raised, py_val = _outcome(_v_nvd_route, q)
    rs_raised, rs_val = _outcome(lambda s: tuple(_core.nvd_route_query(s)), q)
    assert (py_raised, rs_raised) == (py_raised, py_raised), repr(q)
    assert rs_val == py_val, repr(q)


NVD_CVE = {
    "id": "CVE-2021-44228",
    "published": "2021-12-10T10:15Z",
    "descriptions": [
        {"lang": "en",
         "value": "  Apache Log4j2  Remote code execution  "},
        {"lang": "de", "value": "Deutsch"},
    ],
    "metrics": {
        "cvssMetricV31": [
            {"cvssData": {"baseSeverity": "CRITICAL", "baseScore": 10.0,
                          "vectorString": "CVSS:3.1/AV:N/AC:L"}}],
    },
}


def _v_nvd_row(cve, fallback_id=""):
    metrics = cve.get("metrics", {}) or {}
    cvss = {}
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
        "snippet": _v_first_desc(cve),
        "fields": {
            "nvd": {
                "severity": cvss.get("baseSeverity", ""),
                "base_score": cvss.get("baseScore", ""),
                "vector": cvss.get("vectorString", ""),
            }
        },
    }


def _v_nvd_search(body, max_results=5):
    items = body.get("vulnerabilities", [])
    return [
        _v_nvd_row(item.get("cve", {})) for item in items[:max_results]
    ]


def _v_nvd_fetch(body, fallback_id=""):
    items = body.get("vulnerabilities", [])
    if not items:
        return []
    return [_v_nvd_row(items[0].get("cve", {}), fallback_id)]


def _rs_nvd_search(body, max_results=5):
    return json.loads(_core.nvd_parse_vulns(
        json.dumps(body), "", max_results))


def _rs_nvd_fetch(body, fallback_id=""):
    return json.loads(_core.nvd_parse_fetch(
        json.dumps(body), fallback_id))


NVD_SEARCH_BODIES = [
    {"vulnerabilities": [{"cve": NVD_CVE}]},
    {"vulnerabilities": [{"cve": NVD_CVE}, {"cve": {"id": "CVE-2"}}]},
    {},
    {"vulnerabilities": None},
    {"vulnerabilities": []},
    {"vulnerabilities": ""},
    {"vulnerabilities": 0},
    {"vulnerabilities": {}},
    {"vulnerabilities": "ab"},
    {"vulnerabilities": 5},
    {"vulnerabilities": True},
    {"vulnerabilities": [None]},
    {"vulnerabilities": ["x"]},
    {"vulnerabilities": [5]},
    {"vulnerabilities": [{}]},
    {"vulnerabilities": [{"cve": None}]},
    {"vulnerabilities": [{"cve": 5}]},
    {"vulnerabilities": [{"cve": "x"}]},
    {"vulnerabilities": [{"other": 1}]},
    {"vulnerabilities": [{"cve": {}}]},
    {"vulnerabilities": [{"cve": {"id": None, "published": None}}]},
    {"vulnerabilities": [{"cve": {"id": 5, "published": 20211210}}]},
    {"vulnerabilities": [{"cve": {"id": ["CVE-1"],
                                  "published": ["2021"]}}]},
    {"vulnerabilities": [{"cve": {"id": {"i": 1},
                                  "published": {"p": 1}}}]},
    {"vulnerabilities": [{"cve": {"published": "2021"}}]},
    {"vulnerabilities": [{"cve": {"id": "CVE-1", "published": ""}}]},
    {"vulnerabilities": [{"cve": {"metrics": None}}]},
    {"vulnerabilities": [{"cve": {"metrics": "x"}}]},
    {"vulnerabilities": [{"cve": {"metrics": 5}}]},
    {"vulnerabilities": [{"cve": {"metrics": {"cvssMetricV31": None}}}]},
    {"vulnerabilities": [{"cve": {"metrics": {"cvssMetricV31": []}}}]},
    {"vulnerabilities": [{"cve": {"metrics": {"cvssMetricV31": "ab"}}}]},
    {"vulnerabilities": [{"cve": {"metrics": {"cvssMetricV31": ""}}}]},
    {"vulnerabilities": [{"cve": {"metrics": {"cvssMetricV31": 5}}}]},
    {"vulnerabilities": [{"cve": {"metrics": {"cvssMetricV31": [None]}}}]},
    {"vulnerabilities": [{"cve": {"metrics": {"cvssMetricV31": ["ab"]}}}]},
    {"vulnerabilities": [{"cve": {"metrics": {"cvssMetricV31": [""]}}}]},
    {"vulnerabilities": [{"cve": {"metrics": {"cvssMetricV31": [5]}}}]},
    {"vulnerabilities": [{"cve": {"metrics": {"cvssMetricV31": [{}]}}}]},
    {"vulnerabilities": [{"cve": {"metrics": {
        "cvssMetricV31": [{"cvssData": None}]}}}]},
    {"vulnerabilities": [{"cve": {"metrics": {
        "cvssMetricV31": [{"cvssData": "x"}]}}}]},
    {"vulnerabilities": [{"cve": {"metrics": {
        "cvssMetricV31": [{"cvssData": 0}]}}}]},
    {"vulnerabilities": [{"cve": {"metrics": {
        "cvssMetricV31": [{"other": 1}]}}}]},
    {"vulnerabilities": [{"cve": {"metrics": {
        "cvssMetricV31": [],
        "cvssMetricV30": [{"cvssData": {"baseSeverity": "HIGH"}}]}}}]},
    {"vulnerabilities": [{"cve": {"metrics": {
        "cvssMetricV2": [{"cvssData": {"baseScore": 7.5}}]}}}]},
    {"vulnerabilities": [{"cve": {"metrics": {
        "cvssMetricV31": [{"cvssData": {
            "baseSeverity": None, "baseScore": 0,
            "vectorString": False}}]}}}]},
    {"vulnerabilities": [{"cve": {"descriptions": None}}]},
    {"vulnerabilities": [{"cve": {"descriptions": "x"}}]},
    {"vulnerabilities": [{"cve": {"descriptions": 5}}]},
    {"vulnerabilities": [{"cve": {"descriptions": [None]}}]},
    {"vulnerabilities": [{"cve": {"descriptions": ["x"]}}]},
    {"vulnerabilities": [{"cve": {"descriptions": [5]}}]},
    {"vulnerabilities": [{"cve": {"descriptions": [
        {"lang": "fr", "value": "Bonjour"}]}}]},
    {"vulnerabilities": [{"cve": {"descriptions": [
        {"lang": "fr", "value": "Bonjour"},
        {"value": "NoLang " * 100}]}}]},
    {"vulnerabilities": [{"cve": {"descriptions": [
        {"lang": None, "value": None}]}}]},
    {"vulnerabilities": [{"cve": {"descriptions": [
        {"lang": "en", "value": 5}]}}]},
    {"vulnerabilities": [{"cve": {"descriptions": [
        {"lang": "en", "value": ["a"]}]}}]},
    {"vulnerabilities": [{"cve": {"descriptions": [
        {"lang": "en", "value": {"a": 1}}]}}]},
    {"vulnerabilities": [{"cve": {"descriptions": [
        {"lang": "en", "value": ""}]}}]},
    {"vulnerabilities": [{"cve": {"descriptions": [
        {"lang": "en"}]}}]},
]


@pytest.mark.parametrize("body", NVD_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_nvd_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_nvd_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_nvd_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


NVD_FETCH_BODIES = [
    {"vulnerabilities": [{"cve": NVD_CVE}]},
    {"vulnerabilities": [{"cve": NVD_CVE}, {"cve": {"id": "CVE-2"}}]},
    {},
    {"vulnerabilities": None},
    {"vulnerabilities": []},
    {"vulnerabilities": ""},
    {"vulnerabilities": 0},
    {"vulnerabilities": {}},
    {"vulnerabilities": "ab"},
    {"vulnerabilities": 5},
    {"vulnerabilities": [{"cve": 5}]},
    {"vulnerabilities": [{"cve": "x"}]},
    {"vulnerabilities": [{"other": 1}]},
    {"vulnerabilities": [{"cve": {}}]},
    {"vulnerabilities": [{"cve": {"metrics": {"cvssMetricV2": "x"}}}]},
]


@pytest.mark.parametrize("body", NVD_FETCH_BODIES)
@pytest.mark.parametrize("fallback_id", ["CVE-2021-0001", ""])
def test_nvd_fetch_parity(body, fallback_id):
    py_raised, py_val = _outcome(_v_nvd_fetch, body, fallback_id)
    rs_raised, rs_val = _outcome(_rs_nvd_fetch, body, fallback_id)
    assert (py_raised, rs_raised) == (py_raised, py_raised), (body, fallback_id)
    assert rs_val == py_val, (body, fallback_id)


ZENODO_HIT = {
    "id": 123456,
    "links": {"html": "https://zenodo.org/records/123456"},
    "metadata": {
        "title": "Some dataset",
        "publication_date": "2024-05-01",
        "creators": [{"name": "Doe, Jane"},
                     {"person_or_org": {"name": "Smith, John"}}],
        "description": "<p>Abstract with <b>markup</b>.</p>",
        "resource_type": {"title": {"en": "Dataset"}, "id": "dataset"},
    },
}


def _v_zenodo_names(people):
    out = []
    for a in people or []:
        if isinstance(a, dict):
            name = a.get("name") or (a.get("person_or_org") or {}).get("name", "")
            if name:
                out.append(name)
        elif a:
            out.append(str(a))
    return ", ".join(out)


def _v_zenodo_hit(h, fallback_id=""):
    m = h.get("metadata", {}) or {}
    links = h.get("links", {}) or {}
    rec_id = str(h.get("id", fallback_id))
    rtype = m.get("resource_type", {})
    if isinstance(rtype, dict):
        rtype = rtype.get("title", {}).get("en", "") if isinstance(
            rtype.get("title"), dict) else rtype.get("id", "")
    return {
        "source": "zenodo",
        "id": rec_id,
        "title": m.get("title", ""),
        "url": links.get("html")
        or links.get("self_html")
        or (f"https://zenodo.org/records/{rec_id}" if rec_id else ""),
        "published": m.get("publication_date", ""),
        "authors": _v_zenodo_names(
            m.get("creators") or m.get("contributors") or m.get("authors")
        ),
        "snippet": _v_strip_tags(m.get("description", ""))[:240],
        "fields": {"zenodo": {"resource_type": rtype or ""}},
    }


def _v_zenodo_search(body, max_results=5):
    hits = body.get("hits", {}).get("hits", [])
    return [_v_zenodo_hit(h) for h in hits[:max_results]]


def _v_zenodo_fetch(body, record_id=""):
    return _v_zenodo_hit(body, str(record_id))


def _rs_zenodo_search(body, max_results=5):
    return json.loads(_core.zenodo_parse_search(
        json.dumps(body), max_results))


def _rs_zenodo_fetch(body, record_id=""):
    return json.loads(_core.zenodo_parse_fetch(
        json.dumps(body), str(record_id)))


ZENODO_SEARCH_BODIES = [
    {"hits": {"hits": [ZENODO_HIT]}},
    {"hits": {"hits": [ZENODO_HIT, {"id": 7}]}},
    {},
    {"hits": None},
    {"hits": ""},
    {"hits": 0},
    {"hits": 5},
    {"hits": ["x"]},
    {"hits": {"hits": None}},
    {"hits": {"hits": []}},
    {"hits": {"hits": ""}},
    {"hits": {"hits": 0}},
    {"hits": {"hits": {}}},
    {"hits": {"hits": "ab"}},
    {"hits": {"hits": 5}},
    {"hits": {"hits": [None]}},
    {"hits": {"hits": ["x"]}},
    {"hits": {"hits": [5]}},
    {"hits": {"hits": [{}]}},
    {"hits": {"hits": [{"id": None}]}},
    {"hits": {"hits": [{"id": 5, "metadata": None}]}},
    {"hits": {"hits": [{"id": "r-1", "metadata": "x"}]}},
    {"hits": {"hits": [{"id": ["r"], "links": 5}]}},
    {"hits": {"hits": [{"metadata": {}, "links": {}}]}},
    {"hits": {"hits": [{"id": 0, "metadata": {"title": None}}]}},
    {"hits": {"hits": [{"id": "", "links": {"html": ""}}]}},
    {"hits": {"hits": [{"id": 9,
                        "links": {"html": "", "self_html": "https://s/9"}}]}},
    {"hits": {"hits": [{"id": 9, "links": {"self_html": 0}}]}},
    {"hits": {"hits": [{"id": 9, "links": {"html": 5}}]}},
    {"hits": {"hits": [{"metadata": {"resource_type": None}}]}},
    {"hits": {"hits": [{"metadata": {"resource_type": "x"}}]}},
    {"hits": {"hits": [{"metadata": {"resource_type": 5}}]}},
    {"hits": {"hits": [{"metadata": {"resource_type": {}}}]}},
    {"hits": {"hits": [{"metadata": {"resource_type": {"id": "ds"}}}]}},
    {"hits": {"hits": [{"metadata": {"resource_type": {
        "title": "Dataset"}}}]}},
    {"hits": {"hits": [{"metadata": {"resource_type": {
        "title": {"en": "Paper"}, "id": "publication"}}}]}},
    {"hits": {"hits": [{"metadata": {"resource_type": {
        "title": {}, "id": "x"}}}]}},
    {"hits": {"hits": [{"metadata": {"resource_type": {
        "title": {"en": None}}}}]}},
    {"hits": {"hits": [{"metadata": {"resource_type": {
        "title": {"en": 0}, "id": "x"}}}]}},
    {"hits": {"hits": [{"metadata": {"title": 5, "publication_date": None,
                                      "description": None}}]}},
    {"hits": {"hits": [{"metadata": {"description": 5}}]}},
    {"hits": {"hits": [{"metadata": {"description": "<a><b>x</b>"}}]}},
    {"hits": {"hits": [{"metadata": {"description": "plain"}}]}},
    {"hits": {"hits": [{"metadata": {"creators": None,
                                      "description": "d"}}]}},
    {"hits": {"hits": [{"metadata": {"creators": "ab"}}]}},
    {"hits": {"hits": [{"metadata": {"creators": 5}}]}},
    {"hits": {"hits": [{"metadata": {"creators": {"k": "v"}}}]}},
    {"hits": {"hits": [{"metadata": {"creators": ["x", "", None, 5]}}]}},
    {"hits": {"hits": [{"metadata": {"creators": [{"name": 5}]}}]}},
    {"hits": {"hits": [{"metadata": {"creators": [{"name": None,
        "person_or_org": {"name": "P"}}]}}]}},
    {"hits": {"hits": [{"metadata": {"creators": [{"name": "",
        "person_or_org": {"name": "P"}}]}}]}},
    {"hits": {"hits": [{"metadata": {"creators": [
        {"person_or_org": "x"}]}}]}},
    {"hits": {"hits": [{"metadata": {"creators": [
        {"person_or_org": None}]}}]}},
    {"hits": {"hits": [{"metadata": {"creators": [],
        "contributors": [{"name": "C"}]}}]}},
    {"hits": {"hits": [{"metadata": {"creators": [],
        "contributors": [], "authors": "Au"}}]}},
]


@pytest.mark.parametrize("body", ZENODO_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_zenodo_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_zenodo_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_zenodo_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


ZENODO_FETCH_BODIES = [
    ZENODO_HIT,
    {},
    {"id": 1},
    {"metadata": "x"},
    {"metadata": {"creators": [{"name": 5}]}},
    {"metadata": {"description": 5}},
    {"links": 5, "id": 1},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", ZENODO_FETCH_BODIES)
@pytest.mark.parametrize("record_id", ["123", "", 5, None])
def test_zenodo_fetch_parity(body, record_id):
    py_raised, py_val = _outcome(_v_zenodo_fetch, body, record_id)
    rs_raised, rs_val = _outcome(_rs_zenodo_fetch, body, record_id)
    assert (py_raised, rs_raised) == (py_raised, py_raised), (body, record_id)
    assert rs_val == py_val, (body, record_id)


def _rand_value(rng, depth=0):
    pick = rng.random()
    if depth > 2 or pick < 0.30:
        return rng.choice([None, True, False, 0, 1, -3, 2.5, "", "ab",
                           "CVE-2021-44228", [], {}])
    if pick < 0.55:
        return [rng.choice(["a", "", 0, None, True, {"k": "v"}])
                for _ in range(rng.randint(0, 3))]
    if pick < 0.80:
        return {rng.choice(["a", "id", "name", "title", "meta", "cve",
                            "hits", "chart", "result", "metrics",
                            "descriptions", "metadata"]):
                _rand_value(rng, depth + 1)
                for _ in range(rng.randint(0, 2))}
    return rng.choice(["x y ", "2021-12-10T10:15Z", 7, 0.5])


def _fuzz_bodies(seed, n):
    rng = __import__("random").Random(seed)
    return [_rand_value(rng) if rng.random() < 0.7 else {} for _ in range(n)]


@pytest.mark.parametrize("body", _fuzz_bodies(20260905, 150))
@pytest.mark.parametrize("max_results", [3, -1])
def test_fuzz_search_kernels(body, max_results):
    assert _outcome(_v_yahoo_search, body, max_results) == \
        _outcome(_rs_yahoo_search, body, max_results), body
    assert _outcome(_v_nvd_search, body, max_results) == \
        _outcome(_rs_nvd_search, body, max_results), body
    assert _outcome(_v_zenodo_search, body, max_results) == \
        _outcome(_rs_zenodo_search, body, max_results), body


# --- end-to-end seam: real adapters, stubbed HTTP ---------------------
# Pins the Python wrapper side (raw re-attachment, fallback spelling)
# against the vendored originals over realistic payloads.

from unittest.mock import patch as _patch

from gossamer.research_providers import (
    NvdAdapter as _Nvd,
    YahooFinanceAdapter as _Yahoo,
    ZenodoAdapter as _Zenodo,
)


def _stub(body):
    import unittest.mock as _m
    r = _m.MagicMock()
    r.json.return_value = body
    r.raise_for_status.return_value = None
    return r


@_patch("gossamer.research_providers.httpx.get")
def test_e2e_yahoo(mock_get):
    mock_get.return_value = _stub(YAHOO_QUOTES)
    out = _Yahoo(delay=0.0).search("AAPL", max_results=5)
    assert [r["id"] for r in out] == ["AAPL", "SAP.DE", "X"]
    assert out[0]["title"] == "Apple Inc."
    assert out[0]["raw"] == json.dumps(YAHOO_QUOTES["quotes"][0])
    assert out[2]["title"] == "X"  # symbol fallback

    mock_get.return_value = _stub(YAHOO_CHART)
    (rec,) = _Yahoo(delay=0.0).fetch("AAPL")
    assert rec["id"] == "AAPL"
    assert rec["snippet"].startswith("230.5 USD (NasdaqGS)")
    assert rec["raw"] == json.dumps(YAHOO_CHART["chart"]["result"][0]["meta"])

    # Non-string record ids keep their spelling in id/title when the
    # payload carries no symbol of its own ...
    nosym = {"chart": {"result": [{"meta": {"currency": "USD"}}]}}
    mock_get.return_value = _stub(nosym)
    (rec5,) = _Yahoo(delay=0.0).fetch(5)
    assert rec5["id"] == 5 and "quote/5" in rec5["url"]
    assert rec5["title"] == 5
    # ... except non-JSON-native ones, which arrive str()-rendered.
    (rect,) = _Yahoo(delay=0.0).fetch((1, 2))
    assert rect["id"] == "(1, 2)"
    assert rect["title"] == "(1, 2)"


@_patch("gossamer.research_providers.httpx.get")
def test_e2e_nvd(mock_get):
    body = {"vulnerabilities": [{"cve": NVD_CVE}]}
    mock_get.return_value = _stub(body)
    out = _Nvd(delay=0.0).search("CVE-2021-44228", max_results=5)
    assert out[0]["id"] == "CVE-2021-44228"
    assert out[0]["published"] == "2021-12-10"
    assert out[0]["fields"]["nvd"]["severity"] == "CRITICAL"
    assert out[0]["snippet"] == "Apache Log4j2 Remote code execution"
    assert out[0]["raw"] == json.dumps(NVD_CVE)
    assert mock_get.call_args.kwargs["params"].get("cveId") == "CVE-2021-44228"

    mock_get.return_value = _stub({"vulnerabilities": []})
    assert _Nvd(delay=0.0).search("keyword", max_results=5) == []
    mock_get.return_value = _stub(body)
    (rec,) = _Nvd(delay=0.0).fetch("cve-2021-44228")
    assert rec["id"] == "CVE-2021-44228"


@_patch("gossamer.research_providers.httpx.get")
def test_e2e_zenodo(mock_get):
    body = {"hits": {"hits": [ZENODO_HIT]}}
    mock_get.return_value = _stub(body)
    out = _Zenodo(delay=0.0).search("dataset", max_results=5)
    assert out[0]["id"] == "123456"
    assert out[0]["authors"] == "Doe, Jane, Smith, John"
    assert out[0]["fields"]["zenodo"]["resource_type"] == "Dataset"
    assert out[0]["raw"] == json.dumps(ZENODO_HIT)

    mock_get.return_value = _stub(ZENODO_HIT)
    (rec,) = _Zenodo(delay=0.0).fetch(123456)
    assert rec["id"] == "123456"
    assert rec["raw"] == json.dumps(ZENODO_HIT)


# --- batch 3: legal/patent JSON kernels (v0.8.12) --------------------
# Vendored originals are verbatim copies of the retired
# `CourtListenerAdapter._row`, `GovInfoAdapter._row`, `HudocAdapter._row`
# and `PatentsViewAdapter._row` plus the search/fetch parse logic
# (minus `raw`, which crosses the boundary separately).

CL_ROW = {
    "cluster_id": 12345,
    "caseName": "Roe v. Wade",
    "caseNameFull": "Roe et al. v. Wade, District Attorney",
    "absolute_url": "/opinion/12345/roe-v-wade/",
    "dateFiled": "1973-01-22",
    "court": "scotus",
    "court_citation_string": "410 U.S. 113",
    "docketNumber": "70-18",
    "neutralCite": "",
    "citeCount": 25000,
}


def _v_cl_row(r):
    return {
        "source": "courtlistener",
        "id": str(r.get("cluster_id", "")),
        "title": r.get("caseName") or r.get("caseNameFull", ""),
        "url": f"https://www.courtlistener.com{r.get('absolute_url', '')}",
        "published": r.get("dateFiled", ""),
        "snippet": _v_strip_tags(
            (r.get("caseNameFull") or r.get("caseName") or ""))[:240],
        "fields": {
            "court": r.get("court", ""),
            "court_citation": r.get("court_citation_string", ""),
            "docket_number": r.get("docketNumber", ""),
            "neutral_cite": r.get("neutralCite", ""),
            "cite_count": r.get("citeCount", ""),
        },
    }


def _v_cl_search(body, max_results=5):
    results = body.get("results", [])
    return [_v_cl_row(r) for r in results[:max_results]]


def _v_cl_fetch(body):
    return _v_cl_row(body)


def _rs_cl_search(body, max_results=5):
    return json.loads(_core.courtlistener_parse_search(
        json.dumps(body), max_results))


def _rs_cl_fetch(body):
    return json.loads(_core.courtlistener_parse_fetch(json.dumps(body)))


CL_SEARCH_BODIES = [
    {"results": [CL_ROW]},
    {"results": [CL_ROW, {"cluster_id": 7}]},
    {},
    {"results": None},
    {"results": []},
    {"results": ""},
    {"results": 0},
    {"results": {}},
    {"results": "ab"},
    {"results": 5},
    {"results": [None]},
    {"results": ["x"]},
    {"results": [5]},
    {"results": [{}]},
    {"results": [{"cluster_id": None, "caseName": None}]},
    {"results": [{"cluster_id": 5, "caseName": 7, "absolute_url": ["u"]}]},
    {"results": [{"caseNameFull": ["A"], "dateFiled": 19730122}]},
    {"results": [{"caseName": "", "caseNameFull": 0}]},
    {"results": [{"caseNameFull": {"t": 1}}]},
    {"results": [{"caseName": {"t": 1}, "caseNameFull": "F"}]},
    {"results": [{"caseNameFull": "n" * 300, "court": None}]},
]


@pytest.mark.parametrize("body", CL_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_cl_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_cl_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_cl_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


CL_FETCH_BODIES = [
    CL_ROW,
    {},
    {"cluster_id": 1},
    {"caseName": 5, "caseNameFull": "F"},
    {"caseNameFull": ["A"]},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", CL_FETCH_BODIES)
def test_cl_fetch_parity(body):
    py_raised, py_val = _outcome(_v_cl_fetch, body)
    rs_raised, rs_val = _outcome(_rs_cl_fetch, body)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


GI_ROW = {
    "packageId": "BILLS-118hr1234",
    "granuleId": "",
    "title": "A Bill To Do Things",
    "dateIssued": "2024-01-15",
    "collectionCode": "BILLS",
    "download": {"txtLink": "https://www.govinfo.gov/txt/bill.txt",
                 "pdfLink": "https://www.govinfo.gov/pdf/bill.pdf"},
}


def _v_gi_row(r):
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
    }


def _v_gi_search(body, max_results=5):
    return [_v_gi_row(r) for r in body.get("results", [])[:max_results]]


def _v_gi_fetch(body, rid=""):
    return {
        "source": "govinfo",
        "id": body.get("packageId", rid),
        "title": body.get("title", rid),
        "url": body.get("download", {}).get("txtLink", "")
        or f"https://www.govinfo.gov/app/details/{rid}",
        "published": str(body.get("dateIssued", "")),
        "snippet": str(body.get("collectionCode", "")),
        "fields": {"collection": body.get("collectionCode", "")},
    }


def _rs_gi_search(body, max_results=5):
    return json.loads(_core.govinfo_parse_search(
        json.dumps(body), max_results))


def _rs_gi_fetch(body, rid=""):
    return json.loads(_core.govinfo_parse_fetch(json.dumps(body), rid))


GI_SEARCH_BODIES = [
    {"results": [GI_ROW]},
    {"results": [GI_ROW, {"packageId": "P2"}]},
    {},
    {"results": None},
    {"results": []},
    {"results": ""},
    {"results": 0},
    {"results": {}},
    {"results": "ab"},
    {"results": 5},
    {"results": [None]},
    {"results": ["x"]},
    {"results": [5]},
    {"results": [{}]},
    {"results": [{"packageId": None, "granuleId": None}]},
    {"results": [{"packageId": 0, "granuleId": "", "download": None}]},
    {"results": [{"packageId": "P", "granuleId": "G1",
                  "download": {"pdfLink": "https://pdf"}}]},
    {"results": [{"download": {"txtLink": "", "pdfLink": 0}}]},
    {"results": [{"packageId": "P", "download": "x"}]},
    {"results": [{"packageId": "P", "download": 5}]},
    {"results": [{"packageId": ["P"], "title": ["T"],
                  "dateIssued": 20240115, "collectionCode": None}]},
    {"results": [{"packageId": "P", "granuleId": 0,
                  "title": {"t": 1}}]},
]


@pytest.mark.parametrize("body", GI_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_gi_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_gi_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_gi_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


GI_FETCH_BODIES = [
    GI_ROW,
    {},
    {"packageId": "P1"},
    {"packageId": "P1", "download": None},
    {"packageId": "P1", "download": "x"},
    {"packageId": "P1", "download": {"txtLink": None}},
    {"packageId": "P1", "download": {"txtLink": 0, "other": 1}},
    {"packageId": "P1", "download": {"txtLink": 5}},
    {"packageId": None, "title": None, "dateIssued": None},
    {"download": {}},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", GI_FETCH_BODIES)
@pytest.mark.parametrize("rid", ["BILLS-1", ""])
def test_gi_fetch_parity(body, rid):
    py_raised, py_val = _outcome(_v_gi_fetch, body, rid)
    rs_raised, rs_val = _outcome(_rs_gi_fetch, body, rid)
    assert (py_raised, rs_raised) == (py_raised, py_raised), (body, rid)
    assert rs_val == py_val, (body, rid)


HUDOC_COLS = {
    "itemid": "001-123456",
    "docname": "CASE OF DOE v. ROMANIA",
    "kpdate": "20240115",
    "appno": "12345/20",
    "ecli": "ECLI:CE:ECHR:2024:0115JUD001234520",
}


def _v_hudoc_row(columns):
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
    }


def _v_hudoc_search(body):
    return [_v_hudoc_row(r.get("columns", {})) for r in body.get("results", [])]


def _v_hudoc_fetch(body):
    results = body.get("results", [])
    if not results:
        return []
    return [_v_hudoc_row(results[0].get("columns", {}))]


def _rs_hudoc_search(body):
    return json.loads(_core.hudoc_parse_search(json.dumps(body)))


def _rs_hudoc_fetch(body):
    return json.loads(_core.hudoc_parse_fetch(json.dumps(body)))


HUDOC_SEARCH_BODIES = [
    {"results": [{"columns": HUDOC_COLS}]},
    {"results": [{"columns": HUDOC_COLS}, {"columns": {}}]},
    {},
    {"results": None},
    {"results": []},
    {"results": ""},
    {"results": 0},
    {"results": False},
    {"results": {}},
    {"results": "ab"},
    {"results": 5},
    {"results": [None]},
    {"results": ["x"]},
    {"results": [5]},
    {"results": [{}]},
    {"results": [{"columns": None}]},
    {"results": [{"columns": 5}]},
    {"results": [{"columns": "x"}]},
    {"results": [{"other": 1}]},
    {"results": [{}]},
    {"results": [{"columns": {"itemid": 0, "docname": None,
                              "kpdate": 20240115, "appno": ["a"]}}]},
    {"results": [{"columns": {"itemid": "", "kpdate": None}}]},
    {"results": [{"columns": HUDOC_COLS}, None]},
]


@pytest.mark.parametrize("body", HUDOC_SEARCH_BODIES)
def test_hudoc_search_parity(body):
    py_raised, py_val = _outcome(_v_hudoc_search, body)
    rs_raised, rs_val = _outcome(_rs_hudoc_search, body)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


HUDOC_FETCH_BODIES = [
    {"results": [{"columns": HUDOC_COLS}]},
    {"results": [{"columns": HUDOC_COLS}, {"columns": {"itemid": "other"}}]},
    {},
    {"results": None},
    {"results": []},
    {"results": ""},
    {"results": 0},
    {"results": {}},
    {"results": "ab"},
    {"results": 5},
    {"results": [{"columns": 5}]},
    {"results": [{"other": 1}]},
    {"results": [{}]},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", HUDOC_FETCH_BODIES)
def test_hudoc_fetch_parity(body):
    py_raised, py_val = _outcome(_v_hudoc_fetch, body)
    rs_raised, rs_val = _outcome(_rs_hudoc_fetch, body)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


PV_PATENT = {
    "patent_number": "10000000",
    "patent_title": "Coherent LADAR using intra-pixel quadrature detection",
    "patent_date": "2018-06-19",
    "assignee_organization": "ACME Corp",
}


def _v_pv_row(p):
    number = str(p.get("patent_number", p.get("id", "")))
    title = p.get("patent_title", p.get("title", number))
    date = str(p.get("patent_date", p.get("date", "")))[:10]
    return {
        "source": "patentsview",
        "id": number,
        "title": title,
        "url": f"https://patents.google.com/patent/US{number}" if number else "",
        "published": date,
        "snippet": f"{title} ({date})",
        "fields": {
            "assignee": p.get("assignee_organization", p.get("assignee", "")),
        },
    }


def _v_pv_search(body, max_results=5):
    if body.get("error"):
        raise RuntimeError(f"PatentsView error: {body.get('error')}")
    return [_v_pv_row(p) for p in body.get("patents", [])[:max_results]]


def _v_pv_fetch(body):
    if isinstance(body, dict) and body.get("patents"):
        return [_v_pv_row(body["patents"][0])]
    if isinstance(body, dict) and body.get("patent_number"):
        return [_v_pv_row(body)]
    return []


def _rs_pv_search(body, max_results=5):
    return json.loads(_core.patentsview_parse_search(
        json.dumps(body), max_results))


def _rs_pv_fetch(body):
    return json.loads(_core.patentsview_parse_fetch(json.dumps(body)))


PV_SEARCH_BODIES = [
    {"patents": [PV_PATENT]},
    {"patents": [PV_PATENT, {"patent_number": "2"}]},
    {"error": "bad key", "patents": [PV_PATENT]},
    {"error": ""},
    {"error": None},
    {},
    {"patents": None},
    {"patents": []},
    {"patents": ""},
    {"patents": 0},
    {"patents": {}},
    {"patents": "ab"},
    {"patents": 5},
    {"patents": [None]},
    {"patents": ["x"]},
    {"patents": [5]},
    {"patents": [{}]},
    {"patents": [{"id": "9", "title": "T", "date": "2020-01-01",
                  "assignee": "A2"}]},
    {"patents": [{"patent_number": None, "patent_title": None,
                  "patent_date": None, "assignee_organization": None}]},
    {"patents": [{"patent_number": 7, "patent_title": 8,
                  "patent_date": 20200101, "assignee": ["A"]}]},
    {"patents": [{"id": ["1"], "title": {"t": 1}, "date": {"d": 1}}]},
    {"patents": [{"patent_title": "Only Title"}]},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", PV_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_pv_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_pv_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_pv_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


PV_FETCH_BODIES = [
    {"patents": [PV_PATENT]},
    {"patents": [PV_PATENT, {"patent_number": "2"}]},
    PV_PATENT,
    {"patent_number": "3", "other": 1},
    {},
    {"patents": None},
    {"patents": []},
    {"patents": ""},
    {"patents": "ab"},
    {"patents": ["x"]},
    {"patents": [5]},
    {"patents": [{"x": 1}]},
    {"patents": 5},
    {"patents": {"p": 1}},
    {"patent_number": ""},
    {"patent_number": None},
    {"other": 1},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", PV_FETCH_BODIES)
def test_pv_fetch_parity(body):
    py_raised, py_val = _outcome(_v_pv_fetch, body)
    rs_raised, rs_val = _outcome(_rs_pv_fetch, body)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


@pytest.mark.parametrize("body", _fuzz_bodies(20260906, 150))
@pytest.mark.parametrize("max_results", [3, -1])
def test_fuzz_legal_patent_kernels(body, max_results):
    assert _outcome(_v_cl_search, body, max_results) == \
        _outcome(_rs_cl_search, body, max_results), body
    assert _outcome(_v_gi_search, body, max_results) == \
        _outcome(_rs_gi_search, body, max_results), body
    assert _outcome(_v_hudoc_search, body) == \
        _outcome(_rs_hudoc_search, body), body
    assert _outcome(_v_pv_search, body, max_results) == \
        _outcome(_rs_pv_search, body, max_results), body


# --- end-to-end seam: real adapters, stubbed HTTP ---------------------

from gossamer.research_providers import (
    CourtListenerAdapter as _CL,
    GovInfoAdapter as _GI,
    HudocAdapter as _HUDOC,
    PatentsViewAdapter as _PV,
)


@_patch("gossamer.research_providers.httpx.get")
def test_e2e_legal_patent(mock_get):
    mock_get.return_value = _stub({"results": [CL_ROW]})
    out = _CL(delay=0.0).search("roe", max_results=5)
    assert out[0]["id"] == "12345"
    assert out[0]["title"] == "Roe v. Wade"
    assert out[0]["raw"] == json.dumps(CL_ROW)

    mock_get.return_value = _stub(CL_ROW)
    (rec,) = _CL(delay=0.0).fetch(12345)
    assert rec["fields"]["court"] == "scotus"

    mock_get.return_value = _stub({"results": [{"columns": HUDOC_COLS}]})
    out = _HUDOC(delay=0.0).search("doe", max_results=5)
    assert out[0]["id"] == "001-123456"
    assert out[0]["published"] == "20240115"
    # raw is the columns object (the original rows columns, not hits).
    assert out[0]["raw"] == json.dumps(HUDOC_COLS)

    mock_get.return_value = _stub({"results": [{"columns": HUDOC_COLS}]})
    (rec,) = _HUDOC(delay=0.0).fetch("001-123456")
    assert rec["fields"]["ecli"].startswith("ECLI:CE:ECHR")


@_patch("gossamer.research_providers.httpx.post")
@_patch("gossamer.research_providers.httpx.get")
def test_e2e_govinfo(mock_get, mock_post):
    mock_post.return_value = _stub({"results": [GI_ROW]})
    out = _GI(delay=0.0, api_key="K").search("bill", max_results=5)
    assert out[0]["id"] == "BILLS-118hr1234"
    assert out[0]["url"].endswith("bill.txt")
    assert out[0]["raw"] == json.dumps(GI_ROW)

    mock_get.return_value = _stub(GI_ROW)
    (rec,) = _GI(delay=0.0, api_key="K").fetch("BILLS-118hr1234")
    assert rec["title"] == "A Bill To Do Things"
    assert rec["raw"] == json.dumps(GI_ROW)


@_patch("gossamer.research_providers.httpx.get")
def test_e2e_patentsview(mock_get):
    mock_get.return_value = _stub({"patents": [PV_PATENT]})
    out = _PV(delay=0.0, api_key="K").search("ladar", max_results=5)
    assert out[0]["id"] == "10000000"
    assert out[0]["fields"]["assignee"] == "ACME Corp"
    assert out[0]["raw"] == json.dumps(PV_PATENT)

    mock_get.return_value = _stub(PV_PATENT)
    (rec,) = _PV(delay=0.0, api_key="K").fetch("10000000")
    assert rec["published"] == "2018-06-19"
    assert rec["raw"] == json.dumps(PV_PATENT)

    mock_get.return_value = _stub({"patents": [PV_PATENT]})
    (rec,) = _PV(delay=0.0, api_key="K").fetch("10000000")
    assert rec["raw"] == json.dumps(PV_PATENT)


# --- batch 4: OLDP / Federal Register / preprints (v0.8.13) ---------
# Vendored originals are verbatim copies of the retired
# `OldpAdapter._court_name/_case_row` (+ law branch),
# `FederalRegisterAdapter._doc`, `BioRxivAdapter._paper` and
# `ChemRxivAdapter._item` plus the search/fetch parse logic
# (minus `raw`, which crosses the boundary separately).

OLDP_CASE = {
    "id": 98765,
    "slug": "bverfg-1-bvr-1234-20",
    "court": {"name": "BVerfG"},
    "file_number": "1 BvR 1234/20",
    "date": "2021-03-24",
    "ecli": "ECLI:DE:BVerfG:2021:rs20210324.1bvr123420",
    "decision_type": "Beschluss",
    "snippets": ["Klima <b>Schutz</b> ist wichtig", "zweiter Treffer"],
}


def _v_oldp_court(court):
    if isinstance(court, dict):
        return court.get("name", "")
    return str(court or "")


def _v_oldp_case(c):
    court = _v_oldp_court(c.get("court"))
    file_no = c.get("file_number", "")
    title = f"{court} {file_no}".strip() or c.get("slug", "")
    snippets = c.get("snippets") or []
    snippet = " \u2026 ".join(str(s)[:200] for s in snippets[:3])
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
    }


def _v_oldp_law(body, rid=""):
    return {
        "source": "oldp",
        "id": rid,
        "title": body.get("title", rid),
        "url": f"https://de.openlegaldata.io/law/{body.get('slug', '')}",
        "snippet": str(body.get("text", ""))[:400],
        "fields": {"book": body.get("book", ""),
                   "section": body.get("section", "")},
    }


def _v_oldp_search(body, max_results=5):
    hits = body.get("results", [])
    return [_v_oldp_case(c) for c in hits[:max_results]]


def _rs_oldp_search(body, max_results=5):
    return json.loads(_core.oldp_parse_search(
        json.dumps(body), max_results))


def _rs_oldp_case(body):
    return json.loads(_core.oldp_parse_case(json.dumps(body)))


def _rs_oldp_law(body, rid=""):
    return json.loads(_core.oldp_parse_law(json.dumps(body), rid))


OLDP_SEARCH_BODIES = [
    {"results": [OLDP_CASE]},
    {"results": [OLDP_CASE, {"id": 1}]},
    {},
    {"results": None},
    {"results": []},
    {"results": ""},
    {"results": 0},
    {"results": {}},
    {"results": "ab"},
    {"results": 5},
    {"results": [None]},
    {"results": ["x"]},
    {"results": [5]},
    {"results": [{}]},
    {"results": [{"id": None, "court": None, "snippets": None}]},
    {"results": [{"court": "BGH", "file_number": 0, "slug": "s"}]},
    {"results": [{"court": 5, "file_number": ["1"], "id": ["i"]}]},
    {"results": [{"court": {}, "file_number": "", "slug": ""}]},
    {"results": [{"court": {"other": 1}, "snippets": "abcdef"}]},
    {"results": [{"snippets": ["a", None, 5, ["x"], {"s": 1}]}]},
    {"results": [{"snippets": {"a": 1}}]},
    {"results": [{"snippets": 5}]},
    {"results": [{"snippets": ["x" * 300]}]},
    {"results": [{"court": ["BVerfG"], "date": 20210324}]},
]


@pytest.mark.parametrize("body", OLDP_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_oldp_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_oldp_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_oldp_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


OLDP_CASE_BODIES = [
    OLDP_CASE,
    {},
    {"court": "BGH"},
    {"snippets": ["a", "b", "c", "d"]},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", OLDP_CASE_BODIES)
def test_oldp_case_parity(body):
    py_raised, py_val = _outcome(_v_oldp_case, body)
    rs_raised, rs_val = _outcome(_rs_oldp_case, body)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


OLDP_LAW_BODIES = [
    {"title": "GG", "slug": "gg", "text": "Die W\u00fcrde " * 200,
     "book": "Grundgesetz", "section": "Art 1"},
    {},
    {"title": None, "text": None},
    {"text": 5, "book": ["B"]},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", OLDP_LAW_BODIES)
@pytest.mark.parametrize("rid", ["law:gg", ""])
def test_oldp_law_parity(body, rid):
    py_raised, py_val = _outcome(_v_oldp_law, body, rid)
    rs_raised, rs_val = _outcome(_rs_oldp_law, body, rid)
    assert (py_raised, rs_raised) == (py_raised, py_raised), (body, rid)
    assert rs_val == py_val, (body, rid)


FED_DOC = {
    "document_number": "2024-12345",
    "title": "Air Quality Standards",
    "html_url": "https://www.federalregister.gov/documents/2024/12345",
    "doc_date": "2024-06-01",
    "abstract": "<p>EPA proposes <b>new</b> standards.</p>",
    "document_type": "Proposed Rule",
    "type": "Rule",
    "agency": {"name": "Environmental Protection Agency"},
}


def _v_fed_doc(d):
    agency = d.get("agency", {}) or {}
    return {
        "source": "federalregister",
        "id": d.get("document_number", ""),
        "title": d.get("title", ""),
        "url": d.get("html_url", d.get("text_url", "")),
        "published": d.get("doc_date", ""),
        "snippet": _v_strip_tags(d.get("abstract", d.get("excerpt", "")))[:240],
        "fields": {
            "document_type": d.get("document_type", ""),
            "type": d.get("type", ""),
            "agency": agency.get("name", ""),
            "document_number": d.get("document_number", ""),
        },
    }


def _v_fed_search(body, max_results=5):
    docs = body.get("results", body.get("documents", [])) or []
    return [_v_fed_doc(d) for d in docs[:max_results]]


def _v_fed_fetch(body):
    return _v_fed_doc(body)


def _rs_fed_search(body, max_results=5):
    return json.loads(_core.fed_parse_search(
        json.dumps(body), max_results))


def _rs_fed_fetch(body):
    return json.loads(_core.fed_parse_fetch(json.dumps(body)))


FED_SEARCH_BODIES = [
    {"results": [FED_DOC]},
    {"documents": [FED_DOC]},
    {"results": [FED_DOC], "documents": [{"document_number": "other"}]},
    {"documents": [{"document_number": "D"}]},
    {},
    {"results": None},
    {"results": None, "documents": [FED_DOC]},
    {"results": [], "documents": [FED_DOC]},
    {"results": "", "documents": [FED_DOC]},
    {"results": 0},
    {"results": False},
    {"results": {}},
    {"results": "ab"},
    {"results": 5},
    {"results": [None]},
    {"results": ["x"]},
    {"results": [5]},
    {"results": [{}]},
    {"results": [{"agency": None, "html_url": None}]},
    {"results": [{"agency": "EPA", "text_url": "https://text"}]},
    {"results": [{"agency": 5, "abstract": 7, "excerpt": "E"}]},
    {"results": [{"agency": {"other": 1}, "abstract": ["A"]}]},
    {"results": [{"document_number": 0, "doc_date": 20240601}]},
]


@pytest.mark.parametrize("body", FED_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_fed_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_fed_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_fed_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


FED_FETCH_BODIES = [
    FED_DOC,
    {},
    {"agency": "EPA"},
    {"abstract": {"a": 1}},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", FED_FETCH_BODIES)
def test_fed_fetch_parity(body):
    py_raised, py_val = _outcome(_v_fed_fetch, body)
    rs_raised, rs_val = _outcome(_rs_fed_fetch, body)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


BIO_PAPER = {
    "doi": "10.1101/2024.01.01.123456",
    "title": "Something about proteins",
    "date": "2024-01-02",
    "abstract": "<p>We show <i>things</i>.</p>",
    "authors": "Doe, J.; Smith, K.",
    "category": "biochemistry",
    "version": "1",
    "type": "New Results",
    "license": "cc-by",
}


def _v_bio_paper(p, server="biorxiv"):
    doi = p.get("doi", "")
    return {
        "source": "biorxiv",
        "id": doi,
        "title": p.get("title", ""),
        "url": f"https://www.biorxiv.org/content/{doi}" if doi else "",
        "published": p.get("date", ""),
        "snippet": _v_strip_tags(p.get("abstract", ""))[:240],
        "authors": p.get("authors", ""),
        "fields": {
            "server": server,
            "category": p.get("category", ""),
            "version": p.get("version", ""),
            "type": p.get("type", ""),
            "license": p.get("license", ""),
        },
    }


def _v_bio_search(body, max_results=5, server="biorxiv"):
    papers = body.get("collection", [])
    return [_v_bio_paper(p, server) for p in papers[:max_results]]


def _v_bio_fetch(body, server="biorxiv"):
    papers = body.get("collection", [])
    if not papers:
        return []
    return [_v_bio_paper(papers[0], server)]


def _rs_bio_search(body, max_results=5, server="biorxiv"):
    return json.loads(_core.biorxiv_parse_collection(
        json.dumps(body), max_results, server))


def _rs_bio_fetch(body, server="biorxiv"):
    return json.loads(_core.biorxiv_parse_fetch(json.dumps(body), server))


BIO_SEARCH_BODIES = [
    {"collection": [BIO_PAPER]},
    {"collection": [BIO_PAPER, {"doi": "10.1/x"}]},
    {},
    {"collection": None},
    {"collection": []},
    {"collection": ""},
    {"collection": 0},
    {"collection": {}},
    {"collection": "ab"},
    {"collection": 5},
    {"collection": [None]},
    {"collection": ["x"]},
    {"collection": [5]},
    {"collection": [{}]},
    {"collection": [{"doi": 0, "abstract": 5, "authors": ["A"]}]},
    {"collection": [{"doi": ["10.1/x"], "title": {"t": 1}}]},
]


@pytest.mark.parametrize("body", BIO_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
@pytest.mark.parametrize("server", ["biorxiv", "medrxiv"])
def test_bio_search_parity(body, max_results, server):
    py_raised, py_val = _outcome(_v_bio_search, body, max_results, server)
    rs_raised, rs_val = _outcome(_rs_bio_search, body, max_results, server)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


BIO_FETCH_BODIES = [
    {"collection": [BIO_PAPER]},
    {"collection": [BIO_PAPER, {"doi": "10.1/y"}]},
    {},
    {"collection": None},
    {"collection": []},
    {"collection": ""},
    {"collection": 0},
    {"collection": {}},
    {"collection": "ab"},
    {"collection": 5},
    {"collection": [{"doi": "10.1/z"}]},
]


@pytest.mark.parametrize("body", BIO_FETCH_BODIES)
@pytest.mark.parametrize("server", ["biorxiv", "medrxiv"])
def test_bio_fetch_parity(body, server):
    py_raised, py_val = _outcome(_v_bio_fetch, body, server)
    rs_raised, rs_val = _outcome(_rs_bio_fetch, body, server)
    assert (py_raised, rs_raised) == (py_raised, py_raised), (body, server)
    assert rs_val == py_val, (body, server)


CHEM_ITEM = {
    "id": "chemrxiv-123",
    "title": "A catalyst for things",
    "url": "https://chemrxiv.org/item/123",
    "published_on": "2024-02-01",
    "abstract": "<p>We catalyze <b>stuff</b>.</p>",
    "authors": [{"name": "Doe, J."}, "Smith, K.", {"name": ""}, None, 0],
    "doi": "10.26434/chemrxiv-123",
    "topics": [{"name": "Catalysis"}, "Organic", {"name": 5}],
}


def _v_chem_item(it):
    authors = it.get("authors", [])
    if isinstance(authors, list):
        names = [a.get("name", "") if isinstance(a, dict) else str(a)
                 for a in authors]
    else:
        names = [str(authors)]
    return {
        "source": "chemrxiv",
        "id": str(it.get("id", "")),
        "title": it.get("title", ""),
        "url": it.get("url", f"https://chemrxiv.org/engage/chemrxiv/public-article-details/{it.get('id', '')}"),
        "published": it.get("published_on", ""),
        "snippet": _v_strip_tags(it.get("abstract", ""))[:240],
        "authors": ", ".join(n for n in names if n),
        "fields": {
            "doi": it.get("doi", ""),
            "topics": ", ".join(t.get("name", "") if isinstance(t, dict) else str(t) for t in it.get("topics", []) or []),
        },
    }


def _v_chem_search(body, max_results=5):
    items = body.get("data", []) if isinstance(body, dict) else body
    return [_v_chem_item(i) for i in items[:max_results]]


def _v_chem_fetch(body):
    item = body.get("data", body) if isinstance(body, dict) else body
    if not item:
        return []
    if isinstance(item, list):
        return [_v_chem_item(item[0])]
    return [_v_chem_item(item)]


def _rs_chem_search(body, max_results=5):
    return json.loads(_core.chemrxiv_parse_search(
        json.dumps(body), max_results))


def _rs_chem_fetch(body):
    return json.loads(_core.chemrxiv_parse_fetch(json.dumps(body)))


CHEM_SEARCH_BODIES = [
    {"data": [CHEM_ITEM]},
    {"data": [CHEM_ITEM, {"id": "c2"}]},
    {},
    {"data": None},
    {"data": []},
    {"data": ""},
    {"data": 0},
    {"data": {}},
    {"data": "ab"},
    {"data": 5},
    {"data": [None]},
    {"data": ["x"]},
    {"data": [5]},
    {"data": [{}]},
    {"data": [{"authors": None, "topics": None}]},
    {"data": [{"authors": 0, "topics": 0}]},
    {"data": [{"authors": "ab", "topics": "ab"}]},
    {"data": [{"authors": {"a": "Doe"}, "topics": {"t": "Cat"}}]},
    {"data": [{"authors": [None, False, 0, "", [], {}]}]},
    {"data": [{"authors": [{"name": 5}]}]},
    {"data": [{"authors": [{"name": 5}, "ok"]}]},
    {"data": [{"authors": ["ok", {"name": 5}]}]},
    {"data": [{"authors": [[1]], "topics": [[1]]}]},
    {"data": [{"authors": [{"name": None}], "topics": [{}]}]},
    {"data": [{"topics": [{"name": 5}]}]},
    {"data": [{"topics": ["ok", {"name": 5}]}]},
    {"data": [{"topics": [{}, {}]}]},
    {"data": [{"id": None, "url": None, "abstract": None}]},
    [],
    "x",
    5,
    None,
    True,
]


@pytest.mark.parametrize("body", CHEM_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_chem_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_chem_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_chem_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


CHEM_FETCH_BODIES = [
    {"data": [CHEM_ITEM]},
    {"data": CHEM_ITEM},
    CHEM_ITEM,
    {},
    {"data": None},
    {"data": []},
    {"data": ""},
    {"data": 0},
    {"data": {}},
    {"data": "ab"},
    {"data": 5},
    {"data": [None]},
    {"data": [{"authors": [{"name": 5}]}]},
    {"other": 1},
    [],
    "",
    0,
    "ab",
    5,
    None,
    [CHEM_ITEM],
    [None],
]


@pytest.mark.parametrize("body", CHEM_FETCH_BODIES)
def test_chem_fetch_parity(body):
    py_raised, py_val = _outcome(_v_chem_fetch, body)
    rs_raised, rs_val = _outcome(_rs_chem_fetch, body)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


@pytest.mark.parametrize("body", _fuzz_bodies(20260907, 150))
@pytest.mark.parametrize("max_results", [3, -1])
def test_fuzz_oldp_fed_preprint_kernels(body, max_results):
    assert _outcome(_v_oldp_search, body, max_results) == \
        _outcome(_rs_oldp_search, body, max_results), body
    assert _outcome(_v_fed_search, body, max_results) == \
        _outcome(_rs_fed_search, body, max_results), body
    assert _outcome(_v_bio_search, body, max_results) == \
        _outcome(_rs_bio_search, body, max_results), body
    assert _outcome(_v_chem_search, body, max_results) == \
        _outcome(_rs_chem_search, body, max_results), body


# --- end-to-end seam: real adapters, stubbed HTTP ---------------------

from gossamer.research_providers import (
    BioRxivAdapter as _BIO,
    ChemRxivAdapter as _CHEM,
    FederalRegisterAdapter as _FED,
    OldpAdapter as _OLDP,
)


@_patch("gossamer.research_providers.httpx.get")
def test_e2e_oldp(mock_get):
    mock_get.return_value = _stub({"results": [OLDP_CASE]})
    out = _OLDP(delay=0.0).search({"text": "klima"}, max_results=5)
    assert out[0]["id"] == "98765"
    assert out[0]["title"] == "BVerfG 1 BvR 1234/20"
    assert "Klima" in out[0]["snippet"]
    assert out[0]["raw"] == json.dumps(OLDP_CASE)

    mock_get.return_value = _stub(OLDP_CASE)
    (rec,) = _OLDP(delay=0.0).fetch(98765)
    assert rec["fields"]["court"] == "BVerfG"

    law = {"title": "GG", "slug": "gg", "text": "Artikel 1.",
           "book": "Buch", "section": "Art 1"}
    mock_get.return_value = _stub(law)
    (rec,) = _OLDP(delay=0.0).fetch("law:gg")
    assert rec["id"] == "law:gg"
    assert rec["title"] == "GG"
    assert rec["raw"] == json.dumps(law)


@_patch("gossamer.research_providers.httpx.get")
def test_e2e_fed_biorxiv(mock_get):
    mock_get.return_value = _stub({"results": [FED_DOC]})
    out = _FED(delay=0.0).search("air quality", max_results=5)
    assert out[0]["id"] == "2024-12345"
    assert out[0]["fields"]["agency"] == "Environmental Protection Agency"
    assert out[0]["raw"] == json.dumps(FED_DOC)

    mock_get.return_value = _stub(FED_DOC)
    (rec,) = _FED(delay=0.0).fetch("2024-12345")
    assert rec["title"] == "Air Quality Standards"

    mock_get.return_value = _stub({"collection": [BIO_PAPER]})
    out = _BIO(delay=0.0).search("10.1101/2024.01.01.123456", max_results=5)
    assert out[0]["id"] == "10.1101/2024.01.01.123456"
    assert out[0]["authors"] == "Doe, J.; Smith, K."
    assert out[0]["raw"] == json.dumps(BIO_PAPER)

    (rec,) = _BIO(delay=0.0).fetch("10.1101/2024.01.01.123456")
    assert rec["fields"]["server"] == "biorxiv"
    assert _BIO(delay=0.0, server="medrxiv").server == "medrxiv"


@_patch("gossamer.research_providers.httpx.get")
def test_e2e_chemrxiv(mock_get):
    item = {"id": "c1", "title": "T", "authors": [{"name": "A. Uthor"}],
            "topics": [{"name": "Cat"}]}
    mock_get.return_value = _stub({"data": [item]})
    out = _CHEM(delay=0.0, api_key="K").search("catalyst", max_results=5)
    assert out[0]["authors"] == "A. Uthor"
    assert out[0]["fields"]["topics"] == "Cat"
    assert out[0]["raw"] == json.dumps(item)

    mock_get.return_value = _stub({"data": item})
    (rec,) = _CHEM(delay=0.0, api_key="K").fetch("c1")
    assert rec["id"] == "c1"
    assert rec["raw"] == json.dumps(item)
