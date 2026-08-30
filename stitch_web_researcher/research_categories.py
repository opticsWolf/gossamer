"""Category-aware provider routing for the ``research_by_category`` tool.

``research_by_category`` is a thin, provider-specific overlay on top of the
generic ``web_search`` toolbox: given a free-form query it classifies the query
into a domain *category* and triggers the provider best suited to that
category.

Categories are declared as data (``CATEGORIES``) so a new domain can be added
without touching the facade -- only this module changes. Each category maps to
exactly one provider:

  * ``scholarly`` -> ``openalex``   (domain adapter: scholarly works)
  * ``geo``       -> ``open-meteo`` (domain adapter: place/coordinate lookup)
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
    """One domain category and the provider it routes to."""

    name: str
    description: str
    keywords: Tuple[str, ...]
    provider: str
    kind: str  # "adapter" | "engine"

    @property
    def is_fallback(self) -> bool:
        """True for the ``general`` category (no trigger keywords)."""
        return not self.keywords


# Academic works / papers / citations / DOIs / journals.
_SCHOLARLY: Tuple[str, ...] = (
    "paper", "papers", "citation", "citations", "journal",
    "peer-reviewed", "peer reviewed", "arxiv", "e-print", "doi",
    "scholar", "academic", "academia", "publication", "publications",
    "research paper", "professor", "university", "thesis", "theses",
    "abstract", "semanticscholar", "refereed", "conference paper",
    "open access",
)

# Weather / climate / coordinates / place lookups.
_GEO: Tuple[str, ...] = (
    "weather", "climate", "temperature", "forecast", "forecasts",
    "coordinates", "coordinate", "latitude", "longitude", "geocod",
    "hurricane", "tropical storm", "heat wave", "cold snap",
    "open-meteo", "place name", "zip code", "zipcode", "postal code",
    "rainfall", "snowfall",
)

# --- category table (order matters: first keyword hit wins) --------------
CATEGORIES: Tuple[Category, ...] = (
    Category(
        name="scholarly",
        description="Academic works, papers, citations, DOIs, journals.",
        keywords=_SCHOLARLY,
        provider="openalex",
        kind="adapter",
    ),
    Category(
        name="geo",
        description="Weather, climate, coordinates and place lookups.",
        keywords=_GEO,
        provider="open-meteo",
        kind="adapter",
    ),
    Category(
        name="general",
        description="General web search (fallback when no domain matches).",
        keywords=(),
        provider="duckduckgo",
        kind="engine",
    ),
)

DEFAULT_CATEGORY: Category = CATEGORIES[-1]

# provider id -> display name used in the LLM-facing description.
_PROVIDER_DISPLAY: Dict[str, str] = {
    "openalex": "OpenAlex",
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
    Every category (including the fallback) is listed by name so the model
    can see the full taxonomy.
    """
    category_map = ", ".join(
        f"{c.name} -> {_display(c.provider)} ({c.description})"
        for c in CATEGORIES
    )
    return (
        "Category-aware, provider-specific search. Classifies the query into a "
        f"domain category and triggers the matching provider: {category_map}. "
        "Queries matching no domain category fall back to the general provider. "
        "Returns the chosen category, provider and results as JSON."
    )

# provider id -> adapter factory (imported lazily so this module stays
# importable even if research_providers is unavailable in a slim runtime).
_ADAPTER_FACTORIES: Dict[str, Callable[[], object]] = {
    "openalex": "stitch_web_researcher.research_providers.OpenAlexAdapter",
    "open-meteo": "stitch_web_researcher.research_providers.OpenMeteoAdapter",
}


def _import_path(dotted: str) -> object:
    module_name, _, attr = dotted.rpartition(".")
    module = __import__(module_name, fromlist=[attr])
    return getattr(module, attr)


def _make_adapter(provider: str) -> object:
    """Instantiate the domain adapter registered for *provider*."""
    dotted = _ADAPTER_FACTORIES.get(provider)
    if dotted is None:
        raise ValueError(f"no adapter registered for provider {provider!r}")
    return _import_path(dotted)()


def classify(query: str) -> Category:
    """Return the first category whose trigger keywords appear in *query*.

    Matching uses word boundaries. The ``general`` category (no keywords) is
    the implicit fallback and is never matched directly.
    """
    text = (query or "").lower()
    for category in CATEGORIES:
        if category.is_fallback:
            continue
        for kw in category.keywords:
            if re.search(rf"\b{re.escape(kw)}\b", text):
                return category
    return DEFAULT_CATEGORY


# Public alias -- "resolve a query to its category".
resolve = classify


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
    tb: object, query: str, max_results: int = 5
) -> dict:
    """Classify *query* and trigger the category's provider via *tb*.

    Parameters
    ----------
    tb : WebResearcherToolbox
        The toolbox facade; used for the engine-fallback search path.
    query : str
        The free-form search query / research topic.
    max_results : int
        Maximum number of results to return.

    Returns
    -------
    dict
        A normalized, JSON-serialisable payload naming the chosen category,
        the provider, and the provider's ``results``.
    """
    category = classify(query)

    if category.kind == "adapter":
        try:
            adapter = _make_adapter(category.provider)
            results: object = adapter.search(query, max_results=max_results)
        except Exception as exc:  # noqa: BLE001 - surface as result, never raise
            results = {"error": f"{category.provider} search failed: {exc}"}
    else:
        results = _parse_engine_results(
            tb.search_web(query, max_results=max_results, provider=category.provider)
        )

    return {
        "query": query,
        "category": category.name,
        "provider": category.provider,
        "provider_kind": category.kind,
        "description": category.description,
        "results": results,
    }
