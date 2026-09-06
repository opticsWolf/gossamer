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
