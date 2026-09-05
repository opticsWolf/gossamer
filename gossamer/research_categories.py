"""Category-aware provider routing for the ``research_by_category`` tool.

``research_by_category`` is a thin, provider-specific overlay on top of the
generic ``web_search`` toolbox: given a free-form query it classifies the query
into a domain *category* and triggers the provider best suited to that
category.

Categories are declared as data (``CATEGORIES``) so a new domain can be added
without touching the facade -- only this module changes. Each category groups
one or more providers; the caller may pass ``provider=`` to :func:`search_category`
to call any of them separately. There is **no implicit fallback chain** -- the
model controls which source it queries.

  * ``scholarly`` -> ``openalex`` / ``crossref`` / ``arxiv`` / ``zenodo``
  * ``legal``     -> ``courtlistener`` / ``ecfr`` / ``federalregister`` /
                     ``oldp`` / ``hudoc`` / ``govinfo``
                     (``eurlex`` / ``german`` were retired — no public
                     endpoint; see the provider docs)
  * ``patent``      -> ``epo`` / ``kipris`` / ``patentsview``
                     (all key-gated — no keyless patent API remains)
  * ``financial`` -> ``yahoo`` / ``frankfurter`` / ``eurostat`` /
                     ``bundesbank`` / ``bis`` / ``coingecko`` / ``alphavantage``
  * ``geo``       -> ``open-meteo`` (place/coordinate lookup) / ``overpass``
  * ``general``   -> ``duckduckgo`` (generic search-engine fallback)

Routing rules:

  * ``kind == "adapter"`` -> instantiate the domain adapter and call its
    ``search()`` directly. The domain adapters are *not* part of the default
    search provider registry, so routing through them keeps ``web_search``'s
    default behaviour untouched.
  * ``kind == "engine"``  -> delegate to the toolbox's normal search path
    (``tb.search_web``), which owns caching, dedupe and budgeting.

Classification is keyword based with word boundaries, so a query such as
"late breaking news" does not match the bare token ``lat``. The ``general``
category carries no trigger keywords and is always the implicit fallback.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

__all__ = [
    "Category",
    "CATEGORIES",
    "DEFAULT_CATEGORY",
    "classify",
    "resolve",
    "search_category",
]


@dataclass(frozen=True)
class Category:
    """One domain category.

    A category groups one or more *providers*. The agent may call any of them
    separately (pass ``provider=`` to :func:`search_category`) rather than being
    auto-routed to a single provider -- there is no implicit fallback chain, so
    the model keeps control of which source it queries.
    """

    name: str
    description: str
    keywords: Tuple[str, ...]
    providers: Tuple[str, ...]  # ordered; any may be called separately
    kind: str  # "adapter" | "engine"

    @property
    def default_provider(self) -> str:
        """Provider used when the caller does not pick one (first listed)."""
        return self.providers[0]

    @property
    def is_fallback(self) -> bool:
        """True for the ``general`` category (no trigger keywords)."""
        return not self.keywords

    def has_provider(self, provider: str) -> bool:
        """True if *provider* is callable for this category."""
        return provider in self.providers


# Academic works / papers / citations / DOIs / journals.
_SCHOLARLY: Tuple[str, ...] = (
    "paper", "papers", "citation", "citations", "journal",
    "peer-reviewed", "peer reviewed", "arxiv", "e-print", "doi",
    "scholar", "academic", "academia", "publication", "publications",
    "research paper", "professor", "university", "thesis", "theses",
    "abstract", "semanticscholar", "refereed", "conference paper",
    "open access",
)

# Case law, statutes, regulations, bills, government codes — plus EU /
# German case law (HUDOC/ECtHR, CJEU, BVerfG/BGH). Deliberately *no* bare
# "datenschutz" / "klage" (too broad — they pull generic privacy news and
# complaints into legal); the distinctive "dsgvo"/"gdpr" cover that need.
_LEGAL: Tuple[str, ...] = (
    "case law", "statute", "statutes", "regulation", "regulations",
    "code of federal regulations", "cfr", "congress bill", "bill",
    "eur-lex", "eu law", "court", "legislation", "legislature",
    "federal register", "ordinance", "precedent", "appeal",
    "supreme court", "government code",
    # EU / German case law (Part 2 provider research).
    "echr", "hudoc", "egmr", "menschenrechte", "eugh", "cjeu",
    "celex", "ecli", "gdpr", "dsgvo", "bverfg",
    "bundesverfassungsgericht", "bgh", "bundesgerichtshof",
    "rechtsprechung", "urteil", "urteile", "aktenzeichen",
)

# Stock quotes, market data, exchange rates, indices — plus Eurozone
# central-bank / macro statistics (Bundesbank, ECB, Eurostat, BIS). Deliberately
# *no* bare "bis" (German for "until") or "frankfurter" (sausage/newspaper)
# — both caused false positives in review; the specific forms below are safe.
_FINANCIAL: Tuple[str, ...] = (
    "stock", "quote", "quotes", "finance", "financial", "market",
    "exchange rate", "index", "indices", "share", "shares", "trading",
    "bull market", "bear market", "portfolio", "dividend", "ticker",
    "cryptocurrency", "crypto",
    # Eurozone / German macro & rates (Part 2 provider research).
    "bundesbank", "ecb", "ezb", "eurostat", "hicp", "hvpi",
    "leitzins", "leitzinsen", "geldpolitik", "zinssatz",
    "euribor", "eonia", "estr", "eurozone", "euro-zone",
    "euro area", "euroraum", "bip", "staatsverschuldung",
    "staatsschulden", "arbeitslosenquote",
    # German equity terms (DAX coverage gap found in review spot-checks).
    "aktie", "aktien", "aktienkurs", "dax", "dividende",
)

# Patents and prior art (new in 0.7.0 — all providers key-gated; there is no
# keyless patent search API left). Deliberately *no* bare "pct" (finance
# false positive: "pct of revenue") or "claims" (insurance) — the phrased
# forms below are unambiguous.
_PATENT: Tuple[str, ...] = (
    "patent", "patents", "patented", "patentability",
    "patent application", "patent search", "prior art",
    "uspto", "epo", "espacenet", "jpo", "kipo", "kipris",
    "cnipa", "dpma", "depatisnet", "pct application", "patent office",
)

# Weather / climate / coordinates / place lookups.
_GEO: Tuple[str, ...] = (
    "weather", "climate", "temperature", "forecast", "forecasts",
    "coordinates", "coordinate", "latitude", "longitude", "geocod",
    "hurricane", "tropical storm", "heat wave", "cold snap",
    "open-meteo", "place name", "zip code", "zipcode", "postal code",
    "rainfall", "snowfall",
)

# --- category table (order matters: tie-break for equal hit counts) ------
CATEGORIES: Tuple[Category, ...] = (
    Category(
        name="scholarly",
        description="Academic works, papers, citations, DOIs, journals.",
        keywords=_SCHOLARLY,
        providers=("openalex", "crossref", "arxiv", "zenodo"),
        kind="adapter",
    ),
    Category(
        name="legal",
        description=(
            "Case law, statutes, regulations, bills, government codes; "
            "ECHR/HUDOC, CJEU/CELEX and German case law "
            "(BVerfG, BGH, Rechtsprechung)."
        ),
        keywords=_LEGAL,
        providers=("courtlistener", "ecfr", "federalregister",
                   "oldp", "hudoc", "govinfo"),
        kind="adapter",
    ),
    Category(
        name="patent",
        description=(
            "Patents and prior art (USPTO, EPO, KIPO, JPO, CNIPA, DPMA). "
            "All providers key-gated — no keyless patent API remains."
        ),
        keywords=_PATENT,
        providers=("epo", "kipris", "patentsview"),
        kind="adapter",
    ),
    Category(
        name="financial",
        description=(
            "Stock quotes, market data, exchange rates, indices; "
            "ECB/Eurozone rates and EU macro statistics "
            "(Bundesbank, Eurostat, HICP, Euribor)."
        ),
        keywords=_FINANCIAL,
        # Keyless first: alphavantage needs a key.
        providers=("yahoo", "frankfurter", "eurostat", "bundesbank",
                   "bis", "coingecko", "alphavantage"),
        kind="adapter",
    ),
    Category(
        name="geo",
        description="Weather, climate, coordinates and place lookups.",
        keywords=_GEO,
        providers=("open-meteo", "overpass"),
        kind="adapter",
    ),
    Category(
        name="general",
        description="General web search (fallback when no domain matches).",
        keywords=(),
        providers=("duckduckgo",),
        kind="engine",
    ),
)

DEFAULT_CATEGORY: Category = CATEGORIES[-1]

# provider id -> display name used in the LLM-facing description.
_PROVIDER_DISPLAY: Dict[str, str] = {
    "openalex": "OpenAlex",
    "crossref": "Crossref",
    "arxiv": "ArXiv",
    "courtlistener": "CourtListener",
    "ecfr": "eCFR",
    "federalregister": "Federal Register",
    "alphavantage": "AlphaVantage",
    "yahoo": "Yahoo Finance",
    "frankfurter": "Frankfurter",
    "eurostat": "Eurostat",
    "bundesbank": "Bundesbank",
    "bis": "BIS",
    "coingecko": "CoinGecko",
    "zenodo": "Zenodo",
    "overpass": "Overpass",
    "oldp": "Open Legal Data",
    "hudoc": "HUDOC (ECtHR)",
    "govinfo": "GovInfo",
    "epo": "EPO OPS",
    "kipris": "KIPRIS",
    "patentsview": "PatentsView",
    "open-meteo": "Open-Meteo",
    "duckduckgo": "DuckDuckGo",
}


def _display(provider: str) -> str:
    return _PROVIDER_DISPLAY.get(provider, provider)


def describe_categories() -> str:
    """LLM-facing tool description, auto-generated from ``CATEGORIES``.

    Derived from the single source of truth so the ``research_by_category``
    tool description can never drift from the actual routing table -- add a
    category to ``CATEGORIES`` and this description updates automatically.
    Only category *names* are listed here to keep the static description small;
    the full taxonomy (descriptions and provider ids) is returned on demand by
    calling ``research_by_category`` with no query.
    """
    names = ", ".join(c.name for c in CATEGORIES)
    return (
        "Category-aware, provider-specific search. Classifies the query into a "
        f"domain category ({names}) and calls that category's default provider. "
        "Omit the query to return the full taxonomy (category descriptions and "
        "provider ids) as JSON. Pass provider=<id> to call a specific source; "
        "pass category=<name> to skip classification. No automatic fallback "
        "between providers -- the caller chooses. Returns the chosen category, "
        "provider, and results as JSON."
    )

# provider id -> adapter factory (imported lazily so this module stays
# importable even if research_providers is unavailable in a slim runtime).
_ADAPTER_FACTORIES: Dict[str, Callable[[], object]] = {
    "openalex": "gossamer.research_providers.OpenAlexAdapter",
    "crossref": "gossamer.research_providers.CrossrefAdapter",
    "arxiv": "gossamer.research_providers.ArxivAdapter",
    "courtlistener": "gossamer.research_providers.CourtListenerAdapter",
    "ecfr": "gossamer.research_providers.EcfrAdapter",
    "federalregister": "gossamer.research_providers.FederalRegisterAdapter",
    "alphavantage": "gossamer.research_providers.AlphaVantageAdapter",
    "yahoo": "gossamer.research_providers.YahooFinanceAdapter",
    "frankfurter": "gossamer.research_providers.FrankfurterAdapter",
    "eurostat": "gossamer.research_providers.EurostatAdapter",
    "bundesbank": "gossamer.research_providers.BundesbankAdapter",
    "bis": "gossamer.research_providers.BisAdapter",
    "coingecko": "gossamer.research_providers.CoinGeckoAdapter",
    "zenodo": "gossamer.research_providers.ZenodoAdapter",
    "overpass": "gossamer.research_providers.OverpassAdapter",
    "oldp": "gossamer.research_providers.OldpAdapter",
    "hudoc": "gossamer.research_providers.HudocAdapter",
    "govinfo": "gossamer.research_providers.GovInfoAdapter",
    "epo": "gossamer.research_providers.EpoOpsAdapter",
    "kipris": "gossamer.research_providers.KiprisAdapter",
    "patentsview": "gossamer.research_providers.PatentsViewAdapter",
    "open-meteo": "gossamer.research_providers.OpenMeteoAdapter",
}


def _import_path(dotted: str) -> object:
    module_name, _, attr = dotted.rpartition(".")
    module = __import__(module_name, fromlist=[attr])
    return getattr(module, attr)


# Process-wide adapter instances, keyed by provider id. Adapter politeness
# (per-call gaps) and quota accounting live on the instance, so a fresh
# instance per call would make cross-call throttling a no-op (review B.4).
# The cache holds one instance per provider for the process lifetime;
# `reset_adapter_cache` drops them (tests, or after fork).
_ADAPTER_CACHE: Dict[str, object] = {}
_ADAPTER_CACHE_LOCK = threading.Lock()


def reset_adapter_cache() -> None:
    """Drop all cached adapter instances (see above)."""
    with _ADAPTER_CACHE_LOCK:
        _ADAPTER_CACHE.clear()


def _make_adapter(provider: str) -> object:
    """Return the shared domain adapter registered for *provider*.

    Instances are cached per provider id so rate-limit and quota state
    persists across calls instead of resetting on every query.
    """
    with _ADAPTER_CACHE_LOCK:
        cached = _ADAPTER_CACHE.get(provider)
        if cached is None:
            dotted = _ADAPTER_FACTORIES.get(provider)
            if dotted is None:
                raise ValueError(f"no adapter registered for provider {provider!r}")
            cached = _import_path(dotted)()
            _ADAPTER_CACHE[provider] = cached
        return cached


def classify(query: str) -> Category:
    """Return the category with the most trigger-keyword hits in *query*.

    Matching uses word boundaries; the score is the number of distinct
    keywords matched, so a mixed query ("stock photos of bill murray") goes
    to the dominant topic instead of the first table entry. Ties keep the
    table order (scholarly > legal > financial > geo), which preserves the
    old first-hit behavior for single-topic queries. The ``general``
    category (no keywords) is the implicit fallback and is never matched
    directly.
    """
    text = (query or "").lower()
    best = DEFAULT_CATEGORY
    best_score = 0
    for category in CATEGORIES:
        if category.is_fallback:
            continue
        score = sum(
            1 for kw in category.keywords
            if re.search(rf"\b{re.escape(kw)}\b", text)
        )
        if score > best_score:
            best, best_score = category, score
    return best


# Public alias -- "resolve a query to its category".
resolve = classify


def _find_category(name: str) -> Optional[Category]:
    """Return the category named *name* (case-insensitive), or None."""
    key = (name or "").strip().lower()
    for category in CATEGORIES:
        if category.name.lower() == key:
            return category
    return None


def _category_for_provider(provider: str) -> Optional[Category]:
    """Return the category that owns *provider*, or None if unknown."""
    for category in CATEGORIES:
        if provider in category.providers:
            return category
    return None


# Every provider named across all categories, for "unknown provider" errors.
_ALL_PROVIDERS: Tuple[str, ...] = tuple(
    provider for category in CATEGORIES for provider in category.providers
)


def _parse_engine_results(raw: object) -> object:
    """Best-effort parse of a toolbox ``search_web`` JSON reply."""
    if isinstance(raw, str):
        import json

        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {"raw": raw}
    return raw


def search_category(
    tb: object,
    query: str,
    category: Optional[str] = None,
    provider: Optional[str] = None,
    max_results: int = 5,
) -> dict:
    """Trigger **one** provider via *tb* for *query*.

    Resolution order (the caller keeps control -- there is no implicit
    fallback chain):

      1. ``category=<name>`` given            -> use that category directly.
      2. ``provider=<id>`` given (no category) -> use the category that owns
         that provider (reverse-resolved). The query is *not* reclassified.
      3. neither given                         -> classify *query* into a
         domain category and use its default provider.

    When a category is fixed, an explicit ``provider`` must belong to it;
    otherwise the category's default (first) provider is used.

    Parameters
    ----------
    tb : WebResearcherToolbox
        The toolbox facade; used for the engine search path.
    query : str
        The free-form search query / research topic.
    category : str, optional
        Force a domain category (e.g. ``scholarly``, ``legal``,
        ``financial``, ``geo``, ``general``). When given, the query is not
        reclassified.
    provider : str, optional
        Explicit provider to call. Given alone, its owning category is
        reverse-resolved; given with *category*, it must belong to that
        category. When omitted, the resolved category's default provider is
        used.
    max_results : int
        Maximum number of results to return.

    Returns
    -------
    dict
        A normalized, JSON-serialisable payload naming the chosen category,
        the provider actually called, its available providers, and results.
    """
    # Resolve the category: explicit, reverse-resolved from the provider, or
    # by classifying the query (only when the caller left both unspecified).
    if category is not None:
        category_obj = _find_category(category)
        if category_obj is None:
            return {
                "query": query,
                "category": category,
                "provider": provider,
                "available_categories": [c.name for c in CATEGORIES],
                "error": (
                    f"unknown category {category!r}; choose from: "
                    f"{', '.join(c.name for c in CATEGORIES)}"
                ),
                "results": [],
            }
    elif provider is not None:
        category_obj = _category_for_provider(provider)
        if category_obj is None:
            return {
                "query": query,
                "category": None,
                "provider": provider,
                "available_providers": list(_ALL_PROVIDERS),
                "error": (
                    f"unknown provider {provider!r}; choose from: "
                    f"{', '.join(_ALL_PROVIDERS)}"
                ),
                "results": [],
            }
    else:
        category_obj = classify(query)

    # Resolve the provider: explicit (validated) or the category default.
    if provider is None:
        provider = category_obj.default_provider
    elif not category_obj.has_provider(provider):
        return {
            "query": query,
            "category": category_obj.name,
            "provider": provider,
            "available_providers": list(category_obj.providers),
            "provider_kind": category_obj.kind,
            "error": (
                f"provider {provider!r} is not available for category "
                f"{category_obj.name!r}; choose from: "
                f"{', '.join(category_obj.providers)}"
            ),
            "results": [],
        }

    if category_obj.kind == "adapter":
        try:
            adapter = _make_adapter(provider)
            results: object = adapter.search(query, max_results=max_results)
        except Exception as exc:  # noqa: BLE001 - surface as result, never raise
            results = {"error": f"{provider} search failed: {exc}"}
    else:
        results = _parse_engine_results(
            tb.search_web(query, max_results=max_results, provider=provider)
        )

    return {
        "query": query,
        "category": category_obj.name,
        "provider": provider,
        "available_providers": list(category_obj.providers),
        "provider_kind": category_obj.kind,
        "description": category_obj.description,
        "results": results,
    }
