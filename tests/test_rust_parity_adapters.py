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


# --- batch 5: financial kernels (v0.8.14) ---------------------------
# Vendored originals are verbatim copies of the retired
# `EurostatAdapter._unpack` (+ `_run` row shaping),
# `CoinGeckoAdapter` search/fetch rows and the AlphaVantage
# search/fetch rows (minus `raw`, which crosses separately).

EU_CUBE = {
    "label": "GDP",
    "id": ["geo", "time"],
    "size": [2, 2],
    "dimension": {
        "geo": {"category": {"index": {"DE": 0, "FR": 1},
                             "label": {"DE": "Germany", "FR": "France"}}},
        "time": {"category": {"index": {"2022": 0, "2023": 1},
                              "label": {"2022": "2022", "2023": "2023"}}},
    },
    "value": {"0": 100.0, "1": 101.5, "2": 200.0, "3": 202.5},
}


def _v_eu_unpack(data, limit):
    ids = data.get("id", [])
    sizes = data.get("size", [])
    dimensions = data.get("dimension", {}) or {}
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


def _v_eu_cells(body, code, max_results=5):
    data = body
    label = data.get("label", code)
    out = []
    for coords, val in _v_eu_unpack(data, max_results):
        coord_txt = " \u00b7 ".join(f"{c[2] or c[1]}" for c in coords)
        dims = {dim: code_ for dim, code_, _label in coords}
        out.append({
            "record": {
                "source": "eurostat",
                "id": f"{code}:" + "/".join(dims.get(d, "") for d in dims),
                "title": f"{label}: {coord_txt} = {val}",
                "url": "",
                "snippet": f"{coord_txt} \u2192 {val}",
                # Placeholder fields (dataset + value); the wrapper
                # rebuilds the full dict with native dims.
                "fields": {"dataset": code, "value": val},
            },
            "dims": [[d, c] for d, c, _l in coords],
            "payload": {"dataset": code,
                        "coords": [list(t) for t in coords],
                        "value": val},
        })
    return out


def _rs_eu_cells(body, code, max_results=5):
    return json.loads(_core.eurostat_parse_cells(
        json.dumps(body), code, max_results))


EU_CASES = [
    EU_CUBE,
    {"label": "X", "id": ["a"], "size": [1],
     "dimension": {"a": {"category": {"index": {"x": 0}}}},
     "value": {"0": 1}},
    {},
    {"id": None},
    {"id": 5},
    {"id": "ab"},
    {"id": {"a": 1}},
    {"id": []},
    {"size": None},
    {"size": 5},
    {"size": "ab"},
    {"size": {}},
    {"size": [2, "x"]},
    {"size": [2, None]},
    {"size": [2.5]},
    {"size": [2.0]},
    {"size": [0.5]},
    {"size": [0]},
    {"size": [-3]},
    {"size": [True]},
    {"id": ["a", "b"], "size": [2]},
    {"id": ["a"], "size": [2], "dimension": {"a": {"category": {}}},
     "value": {"0": 1, "1": 2, "5": 3, "-1": 4, "x": 5, "1_0": 6,
               " 2 ": 7, "+3": 8}},
    {"id": ["a"], "size": [3],
     "dimension": {"a": {"category": {"index": {"x": 0, "y": 1},
                                      "label": "oops"}}},
     "value": {"0": 1}},
    {"id": ["a"], "size": [2],
     "dimension": {"a": {"category": {"index": [1, 2]}}},
     "value": {"0": 1}},
    {"id": ["a"], "size": [2],
     "dimension": {"a": [1, 2]},
     "value": {"0": 1}},
    {"id": ["a"], "size": [2], "dimension": "ab",
     "value": {"0": 1}},
    {"id": [["a"]], "size": [1], "value": {"0": 1}},
    {"id": [{"a": 1}], "size": [1], "value": {"0": 1}},
    {"id": [5], "size": [1],
     "dimension": {"x": {"category": {"index": {"q": 0}}}},
     "value": {"0": 7}},
    {"id": ["a"], "size": [1],
     "dimension": {"a": {"category": {"index": {"x": "o", "y": 1}}}},
     "value": {"0": 1}},
    {"id": ["a"], "size": [1],
     "dimension": {"a": {"category": {"index": {"x": None}}}},
     "value": {"0": 1}},
    {"id": ["a", 5], "size": [1, 1],
     "dimension": {"a": {"category": {"index": {"x": 0}}}},
     "value": {"0": [1, 2], "1": {"v": 3}}},
    {"value": None},
    {"value": "ab"},
    {"value": 5},
    {"value": {"ok": 1}},
    {"label": None, "value": {"0": 1}},
    {"label": ["L"], "value": {"0": 1}},
    {"id": ["a"], "size": [1], "value": {"0": 1},
     "dimension": {"a": {"category": {"index": {"x": 0},
                                      "label": {"x": None}}}}},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", EU_CASES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
@pytest.mark.parametrize("code", ["nama_10_gdp", ""])
def test_eurostat_parity(body, max_results, code):
    py_raised, py_val = _outcome(_v_eu_cells, body, code, max_results)
    rs_raised, rs_val = _outcome(_rs_eu_cells, body, code, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), (body, code)
    assert rs_val == py_val, (body, code)


CG_COIN = {"id": "bitcoin", "name": "Bitcoin", "symbol": "btc",
           "market_cap_rank": 1}
CG_MKT = {"id": "bitcoin", "name": "Bitcoin", "symbol": "btc",
          "current_price": 97000.5, "market_cap": 1900000000000,
          "price_change_percentage_24h": 2.5}


def _v_cg_search(body, max_results=5):
    out = []
    for coin in body.get("coins", [])[:max_results]:
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
        })
    return out


def _v_cg_fetch(body, cid=""):
    rows = body
    if not rows:
        return []
    m = rows[0]
    return {
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
    }


def _rs_cg_search(body, max_results=5):
    return json.loads(_core.coingecko_parse_search(
        json.dumps(body), max_results))


def _rs_cg_fetch(body, cid=""):
    out = _core.coingecko_parse_markets(json.dumps(body), cid)
    return [] if out == "null" else json.loads(out)


CG_SEARCH_BODIES = [
    {"coins": [CG_COIN]},
    {"coins": [CG_COIN, {"id": "eth"}]},
    {},
    {"coins": None},
    {"coins": []},
    {"coins": ""},
    {"coins": 0},
    {"coins": {}},
    {"coins": "ab"},
    {"coins": 5},
    {"coins": [None]},
    {"coins": ["x"]},
    {"coins": [5]},
    {"coins": [{}]},
    {"coins": [{"id": None, "symbol": None}]},
    {"coins": [{"symbol": 5, "name": ["B"], "market_cap_rank": None}]},
    {"coins": [{"id": 0, "symbol": "X", "market_cap_rank": 0}]},
]


@pytest.mark.parametrize("body", CG_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_cg_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_cg_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_cg_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


CG_FETCH_BODIES = [
    [CG_MKT],
    [CG_MKT, {"id": "eth"}],
    [],
    "",
    0,
    {},
    "ab",
    5,
    None,
    [{"id": None, "current_price": "high"}],
    {"a": 1},
    ["x"],
    [5],
    [{}],
]


@pytest.mark.parametrize("body", CG_FETCH_BODIES)
@pytest.mark.parametrize("cid", ["bitcoin", ""])
def test_cg_fetch_parity(body, cid):
    py_raised, py_val = _outcome(
        lambda b, c: [_v_cg_fetch(b, c)] if _v_cg_fetch(b, c) else [], body, cid)
    rs_raised, rs_val = _outcome(
        lambda b, c: [_rs_cg_fetch(b, c)] if _rs_cg_fetch(b, c) != [] else [],
        body, cid)
    assert (py_raised, rs_raised) == (py_raised, py_raised), (body, cid)
    assert rs_val == py_val, (body, cid)


AV_MATCH = {"1. symbol": "IBM", "2. name": "International Business Machines",
            "3. type": "Equity", "4. region": "United States",
            "8. currency": "USD", "9. matchScore": "0.9"}
AV_TS = {"Meta Data": {"1. symbol": "IBM", "2. symbol": "IBM",
                       "4. last refreshed": "2024-06-01"},
         "Time Series (Daily)": {"2024-06-01": {"1. open": "170.0",
             "2. high": "172.0", "3. low": "169.0", "4. close": "171.5",
             "5. volume": "4000000"}}}


def _v_av_search(body, query="", max_results=5):
    rows = body.get("bestMatches", [])
    if not rows:
        note = body.get("Note") or body.get("Information") or body.get("notes") or body.get("information") or ""
        return [{"source": "alphavantage", "id": "", "title": note or query,
                 "url": "", "snippet": note, "fields": {}}]
    out = []
    for r in rows[:max_results]:
        symbol = r.get("1. symbol", "")
        out.append({
            "source": "alphavantage",
            "id": symbol,
            "title": r.get("2. name", symbol),
            "url": "",
            "snippet": f"{r.get('2. name', '')} \u2014 {r.get('3. type', '')} {r.get('4. region', '')}",
            "fields": {
                "instrument_type": r.get("3. type", ""),
                "ticker": symbol,
                "currency": r.get("8. currency", ""),
                "match_score": r.get("9. matchScore", ""),
            },
        })
    return out


def _v_av_fetch(body, rid=""):
    ts = body.get("Time Series (Daily)")
    if not ts:
        note = body.get("notes") or body.get("information") or ""
        return [{"source": "alphavantage", "id": str(rid),
                 "title": note or str(rid), "url": "", "snippet": note,
                 "fields": {}}]
    first_date, ohlcv = next(iter(ts.items()))
    meta = body.get("Meta Data", {})
    return [{
        "source": "alphavantage",
        "id": meta.get("2. symbol", str(rid)),
        "title": f"{meta.get('1. symbol', str(rid))} daily close",
        "url": "",
        "snippet": f"latest {first_date}: open {ohlcv.get('1. open', '')}, close {ohlcv.get('4. close', '')}",
        "fields": {
            "symbol": meta.get("2. symbol", str(rid)),
            "last_refreshed": meta.get("4. last refreshed", ""),
            "open": ohlcv.get("1. open", ""),
            "high": ohlcv.get("2. high", ""),
            "low": ohlcv.get("3. low", ""),
            "close": ohlcv.get("4. close", ""),
            "volume": ohlcv.get("5. volume", ""),
        },
    }]


def _rs_av_search(body, query="", max_results=5):
    return json.loads(_core.alphavantage_parse_search(
        json.dumps(body), json.dumps(query), max_results))


def _rs_av_fetch(body, rid=""):
    both = json.loads(_core.alphavantage_parse_fetch(
        json.dumps(body), rid))
    return [both["record"]]


AV_SEARCH_BODIES = [
    {"bestMatches": [AV_MATCH]},
    {"bestMatches": [AV_MATCH, {"1. symbol": "IB"}]},
    {"Note": "Rate limit"},
    {"Information": "Info", "Note": ""},
    {"notes": "n1", "information": "n2"},
    {},
    {"bestMatches": None},
    {"bestMatches": []},
    {"bestMatches": ""},
    {"bestMatches": 0},
    {"bestMatches": {}},
    {"bestMatches": "ab"},
    {"bestMatches": 5},
    {"bestMatches": [None]},
    {"bestMatches": ["x"]},
    {"bestMatches": [5]},
    {"bestMatches": [{}]},
    {"bestMatches": [{"2. name": 7, "1. symbol": None}]},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", AV_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
@pytest.mark.parametrize("query", ["IBM", "", 5, None])
def test_av_search_parity(body, max_results, query):
    py_raised, py_val = _outcome(_v_av_search, body, query, max_results)
    rs_raised, rs_val = _outcome(_rs_av_search, body, query, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), (body, query)
    assert rs_val == py_val, (body, query)


AV_FETCH_BODIES = [
    AV_TS,
    {"Note": "limit", "Time Series (Daily)": {}},
    {"notes": "n", "information": "i"},
    {},
    {"Time Series (Daily)": None},
    {"Time Series (Daily)": []},
    {"Time Series (Daily)": ""},
    {"Time Series (Daily)": 0},
    {"Time Series (Daily)": {}},
    {"Time Series (Daily)": "ab"},
    {"Time Series (Daily)": 5},
    {"Time Series (Daily)": {"2024-01-01": None}},
    {"Time Series (Daily)": {"2024-01-01": 5}},
    {"Time Series (Daily)": {"2024-01-01": "x"}},
    {"Time Series (Daily)": {"2024-01-01": {}}},
    {"Time Series (Daily)": {"2024-01-01": {"1. open": 1}},
     "Meta Data": None},
    {"Time Series (Daily)": {"2024-01-01": {"1. open": 1}},
     "Meta Data": "x"},
    {"Time Series (Daily)": {"2024-01-01": {"1. open": 1}},
     "Meta Data": {"2. symbol": None}},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", AV_FETCH_BODIES)
@pytest.mark.parametrize("rid", ["IBM", ""])
def test_av_fetch_parity(body, rid):
    py_raised, py_val = _outcome(_v_av_fetch, body, rid)
    rs_raised, rs_val = _outcome(_rs_av_fetch, body, rid)
    assert (py_raised, rs_raised) == (py_raised, py_raised), (body, rid)
    assert rs_val == py_val, (body, rid)


@pytest.mark.parametrize("body", _fuzz_bodies(20260908, 150))
@pytest.mark.parametrize("max_results", [3, -1])
def test_fuzz_financial_kernels(body, max_results):
    assert _outcome(_v_eu_cells, body, "CODE", max_results) == \
        _outcome(_rs_eu_cells, body, "CODE", max_results), body
    assert _outcome(_v_cg_search, body, max_results) == \
        _outcome(_rs_cg_search, body, max_results), body
    assert _outcome(_v_av_search, body, "q", max_results) == \
        _outcome(_rs_av_search, body, "q", max_results), body


# --- end-to-end seam: real adapters, stubbed HTTP ---------------------

from gossamer.research_providers import (
    AlphaVantageAdapter as _AV,
    CoinGeckoAdapter as _CG,
    EurostatAdapter as _EU,
)


@_patch("gossamer.research_providers.httpx.get")
def test_e2e_eurostat(mock_get):
    mock_get.return_value = _stub(EU_CUBE)
    out = _EU(delay=0.0).search("nama_10_gdp?geo=DE", max_results=5)
    assert out[0]["id"] == "nama_10_gdp:DE/2022"
    assert out[0]["title"] == "GDP: Germany \u00b7 2022 = 100.0"
    assert out[0]["fields"] == {"dataset": "nama_10_gdp", "geo": "DE",
                                "time": "2022", "value": 100.0}
    assert json.loads(out[0]["raw"])["coords"][0] == ["geo", "DE", "Germany"]

    out = _EU(delay=0.0).fetch("nama_10_gdp")
    assert len(out) == 4 and out[0]["source"] == "eurostat"


@_patch("gossamer.research_providers.httpx.get")
def test_e2e_coingecko(mock_get):
    mock_get.return_value = _stub({"coins": [CG_COIN]})
    out = _CG(delay=0.0).search("bitcoin", max_results=5)
    assert out[0]["id"] == "bitcoin"
    assert out[0]["title"] == "Bitcoin (BTC)"
    assert out[0]["raw"] == json.dumps(CG_COIN)

    mock_get.return_value = _stub([CG_MKT])
    (rec,) = _CG(delay=0.0).fetch("Bitcoin")
    assert rec["title"] == "Bitcoin $97000.5"
    assert rec["fields"]["current_price_usd"] == 97000.5
    assert rec["raw"] == json.dumps(CG_MKT)

    mock_get.return_value = _stub([])
    assert _CG(delay=0.0).fetch("nope") == []


@_patch("gossamer.research_providers.httpx.get")
def test_e2e_alphavantage(mock_get):
    mock_get.return_value = _stub({"bestMatches": [AV_MATCH]})
    out = _AV(delay=0.0, api_key="K").search("IBM", max_results=5)
    assert out[0]["id"] == "IBM"
    assert out[0]["fields"]["ticker"] == "IBM"
    assert out[0]["raw"] == json.dumps(AV_MATCH)

    mock_get.return_value = _stub({"Note": "slow down"})
    out = _AV(delay=0.0, api_key="K").search("IBM", max_results=5)
    assert out[0]["id"] == "" and out[0]["title"] == "slow down"

    mock_get.return_value = _stub(AV_TS)
    (rec,) = _AV(delay=0.0, api_key="K").fetch("IBM")
    assert rec["title"] == "IBM daily close"
    assert rec["fields"]["close"] == "171.5"
    assert rec["raw"] == json.dumps(
        AV_TS["Time Series (Daily)"]["2024-06-01"])


# --- batch 6: scholarly kernels (v0.8.15) ---------------------------
# Vendored originals are verbatim copies of the retired
# `OpenAlexAdapter` search/fetch rows, `CrossrefAdapter` search/fetch
# rows, `OpenLibraryAdapter` search/fetch rows and the `DoajAdapter`
# search row (minus `raw`, which crosses the boundary separately).

OA_WORK = {
    "id": "https://openalex.org/W123",
    "title": "On things",
    "doi": "https://doi.org/10.1/x",
    "publication_date": "2024-01-01",
    "cited_by_count": 42,
    "authorships": [
        {"author": {"display_name": "Doe, J."}},
        {"author": {"display_name": "Smith, K."}},
    ],
}


def _v_oa_row(w, full=True):
    rec = {
        "source": "openalex",
        "id": w.get("id", ""),
        "title": w.get("title") or "",
        "url": w.get("doi") or w.get("id"),
        "doi": w.get("doi", ""),
        "published": w.get("publication_date", ""),
    }
    if full:
        authors = [
            a.get("author", {}).get("display_name", "")
            for a in w.get("authorships", [])
        ]
        authors = [a for a in authors if a]
        rec["authors"] = ", ".join(authors)
        rec["citations"] = w.get("cited_by_count", 0)
        rec["snippet"] = ", ".join(authors)
    return rec


def _v_oa_search(body, max_results=5):
    return [_v_oa_row(w) for w in body.get("results", [])[:max_results]]


def _v_oa_fetch(body):
    return _v_oa_row(body, full=False)


def _rs_oa_search(body, max_results=5):
    return json.loads(_core.openalex_parse_search(
        json.dumps(body), max_results))


def _rs_oa_fetch(body):
    return json.loads(_core.openalex_parse_fetch(json.dumps(body)))


OA_SEARCH_BODIES = [
    {"results": [OA_WORK]},
    {"results": [OA_WORK, {"id": "W2"}]},
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
    {"results": [{"id": None, "title": None, "doi": None}]},
    {"results": [{"title": 0, "doi": "", "authorships": None}]},
    {"results": [{"authorships": [{"author": None}]}]},
    {"results": [{"authorships": [{"author": {"display_name": 5}}]}]},
    {"results": [{"authorships": [{"author": {}}]}]},
    {"results": [{"authorships": [None]}]},
    {"results": [{"authorships": ["ab"]}]},
    {"results": [{"authorships": {"a": 1}}]},
    {"results": [{"authorships": [{}]}]},
    {"results": [{"cited_by_count": None, "doi": 0}]},
]


@pytest.mark.parametrize("body", OA_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_oa_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_oa_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_oa_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


OA_FETCH_BODIES = [
    OA_WORK,
    {},
    {"doi": "https://doi.org/10.1/y"},
    {"title": ["T"]},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", OA_FETCH_BODIES)
def test_oa_fetch_parity(body):
    py_raised, py_val = _outcome(_v_oa_fetch, body)
    rs_raised, rs_val = _outcome(_rs_oa_fetch, body)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


CR_WORK = {
    "DOI": "10.1/xyz",
    "title": ["A paper title"],
    "URL": "https://doi.org/10.1/xyz",
    "published": {"date-parts": [[2024, 5, 1]]},
    "author": [{"family": "Doe", "given": "J."},
               {"name": "Smith, K."}],
    "abstract": "<p>An abstract.</p>",
}


def _v_cr_row(w, fallback_id=""):
    title = (w.get("title") or [""])[0]
    authors = [a.get("family", a.get("name", "")) for a in w.get("author", [])]
    return {
        "source": "crossref",
        "id": w.get("DOI", fallback_id),
        "title": title,
        "url": w.get("URL"),
        "doi": w.get("DOI", ""),
        "published": (w.get("published", {}) or {}).get("date-parts", [[""]])[0],
        "authors": ", ".join(a for a in authors if a),
        "snippet": (w.get("abstract") or "")[:240],
    }


def _v_cr_search(body, max_results=5):
    msg = body.get("message", {})
    return [_v_cr_row(w) for w in msg.get("items", [])[:max_results]]


def _v_cr_fetch(body, fallback_id=""):
    return _v_cr_row(body["message"], fallback_id)


def _rs_cr_search(body, max_results=5):
    return json.loads(_core.crossref_parse_search(
        json.dumps(body), max_results))


def _rs_cr_fetch(body, fallback):
    return json.loads(_core.crossref_parse_fetch(
        json.dumps(body), json.dumps(fallback)))


CR_SEARCH_BODIES = [
    {"message": {"items": [CR_WORK]}},
    {"message": {"items": [CR_WORK, {"DOI": "10.1/y"}]}},
    {},
    {"message": None},
    {"message": []},
    {"message": ""},
    {"message": 0},
    {"message": "ab"},
    {"message": 5},
    {"message": {"items": None}},
    {"message": {"items": []}},
    {"message": {"items": ""}},
    {"message": {"items": 0}},
    {"message": {"items": {}}},
    {"message": {"items": "ab"}},
    {"message": {"items": 5}},
    {"message": {"items": [None]}},
    {"message": {"items": ["x"]}},
    {"message": {"items": [5]}},
    {"message": {"items": [{}]}},
    {"message": {"items": [{"title": None, "author": None,
                            "published": None, "abstract": None}]}},
    {"message": {"items": [{"title": "Abc", "author": "xy",
                            "published": {"date-parts": None}}]}},
    {"message": {"items": [{"title": 5, "author": {"a": 1},
                            "published": {"date-parts": []}}]}},
    {"message": {"items": [{"title": [], "author": [None],
                            "published": {"date-parts": ""}}]}},
    {"message": {"items": [{"title": [None, "x"],
                            "author": [{"family": None}],
                            "published": {"date-parts": [None]},
                            "abstract": ["A", "B"]}]}},
    {"message": {"items": [{"title": [{"t": 1}],
                            "author": [{"family": 5}],
                            "published": {"date-parts": {}},
                            "abstract": {"a": 1}}]}},
    {"message": {"items": [{"DOI": 0, "URL": 0, "abstract": 0}]}},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", CR_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_cr_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_cr_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_cr_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


CR_FETCH_BODIES = [
    {"message": CR_WORK},
    {"message": {}},
    {"message": None},
    {"message": 5},
    {"message": "x"},
    {},
    {"other": 1},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", CR_FETCH_BODIES)
@pytest.mark.parametrize("fallback", ["10.1/abc", "", 5, None])
def test_cr_fetch_parity(body, fallback):
    py_raised, py_val = _outcome(_v_cr_fetch, body, fallback)
    rs_raised, rs_val = _outcome(_rs_cr_fetch, body, fallback)
    assert (py_raised, rs_raised) == (py_raised, py_raised), (body, fallback)
    assert rs_val == py_val, (body, fallback)


OL_DOC = {
    "key": "/works/OL1W",
    "title": "A book",
    "author": ["Doe, J.", "Smith, K."],
    "isbn": ["978-0-1-2", "978-0-1-3"],
    "first_publish_year": 1999,
}
OL_BASE = "https://openlibrary.org"


def _v_ol_row(d):
    key = d.get("key", "")
    return {
        "source": "openlibrary",
        "id": key,
        "title": d.get("title", ""),
        "url": f"{OL_BASE}{key}" if key else "",
        "published": d.get("first_publish_year", ""),
        "authors": ", ".join(d.get("author", []) or []),
        "snippet": ", ".join(d.get("isbn", []) or [])[:120],
    }


def _v_ol_search(body, max_results=5):
    return [_v_ol_row(d) for d in body.get("docs", [])[:max_results]]


def _v_ol_fetch(body, key):
    book = body
    fkey = book.get("key", key)
    authors = []
    for a in book.get("authors", []) or []:
        if isinstance(a, str):
            authors.append(a)
        elif isinstance(a, dict):
            if "name" in a:
                authors.append(a["name"])
            elif isinstance(a.get("author"), dict):
                authors.append(a["author"].get("key", ""))
    return {
        "source": "openlibrary",
        "id": fkey,
        "title": book.get("title", ""),
        "url": f"{OL_BASE}{fkey}",
        "published": (
            book.get("first_publish_year")
            or book.get("first_publish_date")
            or book.get("publish_date")
            or ""
        ),
        "authors": ", ".join(a for a in authors if a),
    }


def _rs_ol_search(body, max_results=5):
    return json.loads(_core.openlibrary_parse_search(
        json.dumps(body), max_results, OL_BASE))


def _rs_ol_fetch(body, key):
    return json.loads(_core.openlibrary_parse_fetch(
        json.dumps(body), key, OL_BASE))


OL_SEARCH_BODIES = [
    {"docs": [OL_DOC]},
    {"docs": [OL_DOC, {"key": "/books/OL2M"}]},
    {},
    {"docs": None},
    {"docs": []},
    {"docs": ""},
    {"docs": 0},
    {"docs": {}},
    {"docs": "ab"},
    {"docs": 5},
    {"docs": [None]},
    {"docs": ["x"]},
    {"docs": [5]},
    {"docs": [{}]},
    {"docs": [{"key": None, "author": None, "isbn": None}]},
    {"docs": [{"key": 0, "author": "", "isbn": 0}]},
    {"docs": [{"key": ["k"], "author": {"a": "b"}, "isbn": "978"}]},
    {"docs": [{"key": {"k": 1}, "author": [None, 5], "isbn": [None]}]},
    {"docs": [{"key": "", "author": [""], "isbn": [["x"]]}]},
    {"docs": [{"key": 0.0, "author": [{"a": 1}], "isbn": [{"i": 1}]}]},
    {"docs": [{"title": ["T"], "first_publish_year": None}]},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", OL_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_ol_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_ol_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_ol_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


OL_FETCH_BODIES = [
    ({"key": "/works/OL1W", "title": "W",
      "authors": [{"name": "Doe, J."},
                  {"author": {"key": "/authors/OL2A"}},
                  "Plain, P.",
                  {"author": "notadict"},
                  {"other": 1},
                  None, 5,
                  {"name": None},
                  {"author": {"other": 1}}]}, "/works/OL1W"),
    ({}, "/books/OL9M"),
    ({"key": None}, "/books/OL9M"),
    ({"authors": None}, "/books/OL9M"),
    ({"authors": ""}, "/books/OL9M"),
    ({"authors": "ab"}, "/books/OL9M"),
    ({"authors": {"a": "b"}}, "/books/OL9M"),
    ({"authors": 5}, "/books/OL9M"),
    ({"authors": [{"name": 5}]}, "/books/OL9M"),
    ({"first_publish_year": None, "first_publish_date": "May 2000"},
     "/books/OL9M"),
    ({"first_publish_year": 0, "publish_date": "2001"}, "/books/OL9M"),
    ([], "/books/OL9M"),
    ("x", "/books/OL9M"),
    (5, "/books/OL9M"),
    (None, "/books/OL9M"),
]


@pytest.mark.parametrize("body,key", OL_FETCH_BODIES)
def test_ol_fetch_parity(body, key):
    py_raised, py_val = _outcome(_v_ol_fetch, body, key)
    rs_raised, rs_val = _outcome(_rs_ol_fetch, body, key)
    assert (py_raised, rs_raised) == (py_raised, py_raised), (body, key)
    assert rs_val == py_val, (body, key)


DOAJ_RESULT = {
    "id": "abc123",
    "bibjson": {
        "title": "Open article",
        "author": [{"name": "Doe, J."}, {"name": "Smith, K."}],
        "identifier": [
            {"type": "doi", "id": "10.1/oa"},
            {"type": "issn", "id": "1234-5678"},
        ],
        "abstract": "An abstract.",
    },
}


def _v_doaj_row(r):
    bib = r.get("bibjson", {}) or {}
    dois = [
        i.get("id")
        for i in bib.get("identifier", [])
        if isinstance(i, dict) and i.get("type") == "doi"
    ]
    authors = bib.get("author", []) if isinstance(bib.get("author"), list) else []
    return {
        "source": "doaj",
        "id": r.get("id", ""),
        "title": (bib.get("title") or ""),
        "url": f"https://doaj.org/article/{r.get('id', '')}",
        "doi": dois[0] if dois else "",
        "authors": ", ".join(
            a.get("name", "") if isinstance(a, dict) else str(a)
            for a in authors),
        "snippet": (bib.get("abstract") or "")[:240],
    }


def _v_doaj_search(body, max_results=5):
    return [_v_doaj_row(r) for r in body.get("results", [])[:max_results]]


def _rs_doaj_search(body, max_results=5):
    return json.loads(_core.doaj_parse_search(
        json.dumps(body), max_results))


DOAJ_BODIES = [
    {"results": [DOAJ_RESULT]},
    {"results": [DOAJ_RESULT, {"id": "x2"}]},
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
    {"results": [{"id": None, "bibjson": None}]},
    {"results": [{"id": 5, "bibjson": ""}]},
    {"results": [{"id": ["i"], "bibjson": "ab"}]},
    {"results": [{"id": {"i": 1}, "bibjson": 5}]},
    {"results": [{"bibjson": {"identifier": None}}]},
    {"results": [{"bibjson": {"identifier": "ab"}}]},
    {"results": [{"bibjson": {"identifier": {"t": "doi"}}}]},
    {"results": [{"bibjson": {"identifier": 5}}]},
    {"results": [{"bibjson": {"identifier": ["x", 5, None, {}]}}]},
    {"results": [{"bibjson": {"identifier": [{"type": "doi"}]}}]},
    {"results": [{"bibjson": {"identifier": [{"type": "doi", "id": None}]}}]},
    {"results": [{"bibjson": {"identifier": [{"type": 5, "id": "x"}]}}]},
    {"results": [{"bibjson": {"identifier": [{"id": "x"}]}}]},
    {"results": [{"bibjson": {"author": None, "title": None,
                              "abstract": None}}]},
    {"results": [{"bibjson": {"author": "xy", "title": 0,
                              "abstract": 0}}]},
    {"results": [{"bibjson": {"author": [{"nick": "n"}], "title": ["T"],
                              "abstract": ["A", "B"]}}]},
    {"results": [{"bibjson": {"author": [5, None], "abstract": {"a": 1}}}]},
    {"results": [{"bibjson": {"author": [{"name": 5}]}}]},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", DOAJ_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_doaj_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_doaj_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_doaj_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


def _fuzz_json6(rng, depth=0):
    r = rng.random()
    if depth > 2 or r < 0.30:
        return rng.choice(
            [None, True, False, 0, 1, -3, 2.5, "", "ab", "https://x/y",
             "Doe, J.", "10.1/xyz", 2024])
    if r < 0.55:
        return [_fuzz_json6(rng, depth + 1) for _ in range(rng.randrange(4))]
    keys = ["id", "title", "doi", "author", "authors", "authorships",
            "bibjson", "message", "results", "items", "docs", "key",
            "name", "family", "identifier", "type", "abstract", "URL",
            "DOI", "published", "date-parts", "publication_date",
            "cited_by_count", "display_name", "isbn",
            "first_publish_year", "first_publish_date", "publish_date"]
    return {k: _fuzz_json6(rng, depth + 1)
            for k in rng.sample(keys, rng.randrange(5))}


def test_batch6_fuzz():
    import random
    rng = random.Random(20260907)
    n_checked = 0
    for trial in range(400):
        body = _fuzz_json6(rng)
        max_results = rng.choice([0, 1, 3, 5, -1, 100])
        for v_fn, r_fn in (
            (_v_oa_search, _rs_oa_search),
            (_v_cr_search, _rs_cr_search),
            (_v_ol_search, _rs_ol_search),
            (_v_doaj_search, _rs_doaj_search),
        ):
            py_raised, py_val = _outcome(v_fn, body, max_results)
            rs_raised, rs_val = _outcome(r_fn, body, max_results)
            assert (py_raised, rs_raised) == (py_raised, py_raised), (trial, body)
            assert rs_val == py_val, (trial, body)
            n_checked += 1
        for v_fn, r_fn, extra in (
            (_v_oa_fetch, _rs_oa_fetch, ()),
            (_v_ol_fetch, _rs_ol_fetch, ("/books/OL9M",)),
        ):
            py_raised, py_val = _outcome(v_fn, body, *extra)
            rs_raised, rs_val = _outcome(r_fn, body, *extra)
            assert (py_raised, rs_raised) == (py_raised, py_raised), (trial, body)
            assert rs_val == py_val, (trial, body)
            n_checked += 1
        fb = rng.choice(["10.1/f", "", 5, None, ["l"], {"d": 1}])
        py_raised, py_val = _outcome(_v_cr_fetch, body, fb)
        rs_raised, rs_val = _outcome(_rs_cr_fetch, body, fb)
        assert (py_raised, rs_raised) == (py_raised, py_raised), (trial, body)
        assert rs_val == py_val, (trial, body, fb)
        n_checked += 1
    assert n_checked == 400 * 7


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _run_with_body(monkeypatch, adapter_fn, body, *args):
    import httpx
    monkeypatch.setattr(httpx, "get",
                        lambda *a, **k: _FakeResp(body))
    return adapter_fn(*args)


def _expected_hostile(fn, body, oa, cr, ol, dj):
    # Pre-port semantics via the vendored rows (raises like the
    # original, including the `raw` re-attachment reads).
    if fn in (oa._search_impl,):
        return [{**_v_oa_row(w), "raw": json.dumps(w)}
                for w in body.get("results", [])[:5]]
    if fn in (cr._search_impl,):
        msg = body.get("message", {})
        return [{**_v_cr_row(w), "raw": json.dumps(w)}
                for w in msg.get("items", [])[:5]]
    if fn in (ol._search_impl,):
        return [{**_v_ol_row(d), "raw": json.dumps(d)}
                for d in body.get("docs", [])[:5]]
    if fn in (dj._search_impl,):
        return [{**_v_doaj_row(r), "raw": json.dumps(r)}
                for r in body.get("results", [])[:5]]
    if fn == oa.fetch:
        return [{**_v_oa_fetch(body), "raw": json.dumps(body)}]
    if fn == cr.fetch:
        return [{**_v_cr_fetch(body, "q"),
                 "raw": json.dumps(body["message"])}]
    return [{**_v_ol_fetch(body, "/books/q"), "raw": json.dumps(body)}]


def test_batch6_hostile_wrappers(monkeypatch):
    from gossamer.research_providers import (
        OpenAlexAdapter, CrossrefAdapter, OpenLibraryAdapter, DoajAdapter)
    oa, cr, ol, dj = (OpenAlexAdapter(delay=0), CrossrefAdapter(delay=0),
                      OpenLibraryAdapter(delay=0), DoajAdapter(delay=0))
    # Exotic-but-plausible payloads through the real methods.
    hostile = [
        (oa._search_impl, {"results": None}),
        (oa._search_impl, {"results": []}),
        (oa._search_impl, {"results": [{"id": "W1"}]}),
        (oa._search_impl, {"results": "ab"}),
        (cr._search_impl, {"message": {"items": [{"DOI": "10.1/a"}]}}),
        (cr._search_impl, {"message": {"items": []}}),
        (ol._search_impl, {"docs": [{"key": "/books/OL1M"}]}),
        (ol._search_impl, {"docs": []}),
        (dj._search_impl, {"results": [{"id": "r1"}]}),
        (dj._search_impl, {"results": []}),
        (oa.fetch, {"id": "W1", "title": "T"}),
        (cr.fetch, {"message": {"DOI": "10.1/a"}}),
        (ol.fetch, {"key": "/works/OL1W", "authors": [{"name": "N."}]}),
    ]
    for fn, body in hostile:
        exp_raised, exp = _outcome(_expected_hostile, fn, body, oa, cr, ol, dj)
        try:
            got = _run_with_body(monkeypatch, fn, body, "q")
            got_raised, got = False, got
        except Exception as e:  # noqa: BLE001 - compared below
            got_raised, got = True, f"{type(e).__name__}: {e}"
        assert (got_raised, exp_raised) == (exp_raised, exp_raised), (fn, body)
        assert got == exp, (fn, body)
        if not got_raised:
            assert got == exp, (fn, body)
            for rec in got:
                assert rec["raw"] == json.dumps(
                    json.loads(rec["raw"])), (fn, body)


# --- batch 7: misc/geo/financial kernels (v0.8.17) -------------------
# Vendored originals are verbatim copies of the retired row builders
# (minus `raw`, which crosses the boundary separately).

def _v_wb_fetch(payload, record_id, data_url):
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
    return {
        "source": "worldbank",
        "id": series_id,
        "title": title,
        "url": f"{data_url}/{record_id}",
        "snippet": f"{len(points)} observations; recent: {recent}",
        "fields": {
            "worldbank": {"observations": points, "pagination": pagination}
        },
    }


def _rs_wb_fetch(payload, record_id, data_url="https://api.worldbank.org/v2/country/all/indicator"):
    from gossamer.research_providers import _json_fallback
    rid, fj = _json_fallback(record_id)
    return json.loads(_core.worldbank_parse_fetch(
        json.dumps(payload), fj or json.dumps(rid),
        f"{data_url}/{record_id}"))


WB_POINT = {"indicator": {"id": "SP.POP.TOTL", "value": "Population, total"},
            "country": {"id": "1W", "value": "World"},
            "date": "2023", "value": 8000000000}
WB_BODIES = [
    [{"page": 1}, [WB_POINT]],
    [{"page": 1}, [WB_POINT, {"date": "2022", "value": None}]],
    [{"page": 1}, []],
    [{"page": 1}],
    [{}, None],
    [{}, 5],
    [{}, "ab"],
    [{}, {}],
    [{}, []],
    [[], [WB_POINT]],
    [{}, [None, 5, "x", {}, WB_POINT]],
    [{}, [{"indicator": None}]],
    [{}, [{"indicator": 5}]],
    [{}, [{"indicator": {}}]],
    [{}, [{"indicator": {"id": 0, "value": ""}}]],
    [{}, [{"indicator": {"id": None}}]],
    [{}, ["x"]],
    [{}, [5]],
    [{}, "ab"],
    [{}, 5],
    [{}, None],
    [{}, {}],
    {},
    {"a": 1},
    [],
    [{}],
    [None, 5],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", WB_BODIES)
@pytest.mark.parametrize("record_id", ["SP.POP.TOTL", "", 5, None])
def test_wb_fetch_parity(body, record_id):
    py_raised, py_val = _outcome(_v_wb_fetch, body, record_id, "D")
    rs_raised, rs_val = _outcome(_rs_wb_fetch, body, record_id, "D")
    assert (py_raised, rs_raised) == (py_raised, py_raised), (body, record_id)
    assert rs_val == py_val, (body, record_id)


def test_wb_note_parity():
    rec = json.loads(_core.worldbank_note())
    assert rec["source"] == "worldbank"
    assert rec["title"] == "World Bank keyword search unavailable"
    assert "raw" not in rec


def _v_fred_official(data, record_id):
    obs = [
        {"date": o.get("date", ""), "value": o.get("value", "")}
        for o in data.get("observations", [])
    ]
    return _v_fred_record(record_id, obs)


def _v_fred_record(record_id, obs):
    points = [f"{o.get('date', '')}={o.get('value', '')}" for o in obs[-10:]]
    return {
        "source": "fred",
        "id": record_id,
        "title": f"FRED series {record_id}",
        "url": f"https://fred.stlouisfed.org/series/{record_id}",
        "snippet": f"{len(obs)} observations; last: {points[-1] if points else 'n/a'}",
        "fields": {"fred": {"observations": obs[-50:]}},
    }


def _v_fred_csv(text, record_id):
    lines = text.strip().splitlines()
    obs = []
    for line in lines[1:]:
        date, _, value = line.partition(",")
        date, value = date.strip(), value.strip()
        if date and value:
            obs.append({"date": date, "value": value})
    rec = _v_fred_record(record_id, obs)
    rec["raw"] = "\n".join(lines[:51])
    return rec


def _rs_fred_official(data, record_id):
    from gossamer.research_providers import _json_fallback
    rid, fj = _json_fallback(record_id)
    return json.loads(_core.fred_parse_official(
        json.dumps(data), fj or json.dumps(rid)))


def _rs_fred_csv(text, record_id):
    from gossamer.research_providers import _json_fallback
    rid, fj = _json_fallback(record_id)
    return json.loads(_core.fred_parse_csv(
        text, fj or json.dumps(rid)))


FRED_OFFICIAL_BODIES = [
    {"observations": [{"date": "2024-01-01", "value": "1.5"}]},
    {"observations": [{"date": "2024-01-01", "value": "1.5"},
                      {"date": "2024-02-01", "value": "."}]},
    {},
    {"observations": None},
    {"observations": []},
    {"observations": ""},
    {"observations": "ab"},
    {"observations": {}},
    {"observations": {"a": 1}},
    {"observations": 5},
    {"observations": [None]},
    {"observations": ["x"]},
    {"observations": [5]},
    {"observations": [{}]},
    {"observations": [{"date": None, "value": None}]},
    {"observations": [{"date": 5, "value": 1.5}]},
    {"observations": [{"date": ["d"], "value": {"v": 1}}]},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", FRED_OFFICIAL_BODIES)
@pytest.mark.parametrize("record_id", ["GDP", "", 5, None])
def test_fred_official_parity(body, record_id):
    py_raised, py_val = _outcome(_v_fred_official, body, record_id)
    rs_raised, rs_val = _outcome(_rs_fred_official, body, record_id)
    assert (py_raised, rs_raised) == (py_raised, py_raised), (body, record_id)
    assert rs_val == py_val, (body, record_id)


FRED_CSV_TEXTS = [
    "DATE,VALUE\n2024-01-01,1.5\n2024-02-01,1.6\n",
    "DATE,VALUE\n",
    "DATE,VALUE",
    "",
    "   \n  ",
    "DATE,VALUE\n2024-01-01,\n,1.5\n,\nno-comma-line\n2024-03-01,  2.0  \n",
    "DATE,VALUE\r\n2024-01-01,1.5\r\n",
    "DATE,VALUE\x0b2024-01-01,1.5\x0c2024-02-01,1.6\x85end",
    "DATE,VALUE 2024-01-01,1.5 tail",
    "DATE,VALUE\x1c2024-01-01,9.9",
    "H1,H2\n" + "\n".join(f"2024-01-{i:02d},{i}.0" for i in range(1, 70)),
    "DATE,VALUE\na,b,c\nd,e",
]


@pytest.mark.parametrize("text", FRED_CSV_TEXTS)
@pytest.mark.parametrize("record_id", ["GDP", "", 5, None])
def test_fred_csv_parity(text, record_id):
    py_raised, py_val = _outcome(_v_fred_csv, text, record_id)
    rs_raised, rs_val = _outcome(_rs_fred_csv, text, record_id)
    assert (py_raised, rs_raised) == (py_raised, py_raised), (text, record_id)
    assert rs_val == py_val, (text, record_id)


GH_REPO = {
    "id": 123,
    "full_name": "o/r",
    "html_url": "https://github.com/o/r",
    "url": "https://api.github.com/repos/o/r",
    "description": "A repo.",
    "language": "Python",
    "stargazers_count": 42,
}


def _v_gh_search_row(r):
    return {
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
    }


def _v_gh_fetch_row(repo, record_id):
    return {
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
    }


def _v_gh_search(body, max_results=5):
    return [_v_gh_search_row(r) for r in body.get("items", [])[:max_results]]


def _rs_gh_search(body, max_results=5):
    return json.loads(_core.github_parse_search(
        json.dumps(body), max_results))


def _rs_gh_fetch(body, fallback):
    return json.loads(_core.github_parse_fetch(
        json.dumps(body), json.dumps(fallback)))


GH_SEARCH_BODIES = [
    {"items": [GH_REPO]},
    {"items": [GH_REPO, {"id": "x"}]},
    {},
    {"items": None},
    {"items": []},
    {"items": ""},
    {"items": 0},
    {"items": {}},
    {"items": "ab"},
    {"items": 5},
    {"items": [None]},
    {"items": ["x"]},
    {"items": [5]},
    {"items": [{}]},
    {"items": [{"id": None, "html_url": None, "url": None,
                "description": None}]},
    {"items": [{"id": True, "html_url": "", "url": "u",
                "description": ["d"]}]},
    {"items": [{"id": 1.5, "html_url": 0, "description": {"d": 1}}]},
    {"items": [{"id": [1], "description": 0}]},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", GH_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_gh_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_gh_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_gh_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


GH_FETCH_BODIES = [
    GH_REPO,
    {},
    {"html_url": None},
    {"description": 5},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", GH_FETCH_BODIES)
@pytest.mark.parametrize("fallback", ["o/r", "", 5, None, ["l"]])
def test_gh_fetch_parity(body, fallback):
    py_raised, py_val = _outcome(_v_gh_fetch_row, body, fallback)
    rs_raised, rs_val = _outcome(_rs_gh_fetch, body, fallback)
    assert (py_raised, rs_raised) == (py_raised, py_raised), (body, fallback)
    assert rs_val == py_val, (body, fallback)


CG_MEMBER = {
    "cgi_id": "C001",
    "display_name": "Doe, J.",
    "url": "https://api.data.gov/x",
    "title": "Senator",
    "party": "D",
    "state": "CA",
    "district": "",
    "chamber": "Senate",
}


def _v_cgr_row(r, fallback=""):
    return {
        "source": "congress",
        "id": r.get("cgi_id", fallback),
        "title": r.get("display_name", fallback),
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
    }


def _v_cgr_search(body, max_results=5):
    return [_v_cgr_row(r) for r in body.get("results", [])[:max_results]]


def _rs_cgr_search(body, max_results=5):
    return json.loads(_core.congress_parse_search(
        json.dumps(body), max_results))


def _rs_cgr_fetch(body, fallback):
    return json.loads(_core.congress_parse_fetch(
        json.dumps(body), json.dumps(fallback)))


CGR_SEARCH_BODIES = [
    {"results": [CG_MEMBER]},
    {"results": [CG_MEMBER, {"cgi_id": "C002"}]},
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
    {"results": [{"title": None, "party": None, "state": None,
                  "district": None}]},
    {"results": [{"title": ["T"], "party": 5, "state": 0,
                  "district": {"d": 1}}]},
    {"results": [{"cgi_id": None, "display_name": ["N"], "url": 5,
                  "chamber": None}]},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", CGR_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_cgr_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_cgr_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_cgr_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


CGR_FETCH_BODIES = [
    CG_MEMBER,
    {},
    {"title": 5, "district": None},
    [],
    "x",
    5,
    None,
]


@pytest.mark.parametrize("body", CGR_FETCH_BODIES)
@pytest.mark.parametrize("fallback", ["C009", "", 5, None, ["l"]])
def test_cgr_fetch_parity(body, fallback):
    py_raised, py_val = _outcome(_v_cgr_row, body, fallback)
    rs_raised, rs_val = _outcome(_rs_cgr_fetch, body, fallback)
    assert (py_raised, rs_raised) == (py_raised, py_raised), (body, fallback)
    assert rs_val == py_val, (body, fallback)
