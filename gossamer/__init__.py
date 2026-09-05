"""
High-Performance Web Researcher – Rust core + Oxide extractors.
"""

from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("gossamer")
except Exception:  # pragma: no cover - fallback when installed without metadata
    __version__ = "0.0.0"

from gossamer._core import fetch_and_extract, batch_research
from gossamer._core import extract_main_content_markdown
from gossamer._core import fetch_html_full
from gossamer.agent_tools import (
    WebResearcherToolbox,
    ToolboxConfig,
    fetch_smart_page,
)
from gossamer.structured_parser import (
    StructuredOxideParser,
    ParsedDocumentPayload,
    DocumentMetadata,
    ExtractedPage,
    ExtractedTable,
)
from gossamer.token_budget import (
    count_tokens,
    truncate_to_tokens,
    fit_context_window,
    estimate_markdown_tokens,
    resolve_encoding,
)
from gossamer.search_providers import (
    SearchProvider,
    ResourceAdapter,
    DuckDuckGoProvider,
    GoogleProvider,
    BingProvider,
    ExaProvider,
    BrowserOxideSearchProvider,
    RateLimit,
    RateState,
    QuotaExhaustedError,
    get_default_providers,
    resolve_provider_name,
)
from gossamer import meta_extractor
from gossamer.meta_extractor import (
    extract_all,
    extract_meta,
    extract_opengraph,
    extract_twitter,
    extract_jsonld,
    merge_into_document_metadata,
)
from gossamer.cache import Cache
from gossamer.robots import RobotsChecker
from gossamer.ssrf import SsrfBlockedError, validate_public_url
from gossamer.dedup import dedupe, content_hash
from gossamer.liveness import check_liveness
from gossamer.citations import format_citations
from gossamer.research_categories import (
    CATEGORIES,
    DEFAULT_CATEGORY,
    classify,
    search_category,
)
from gossamer.research_providers import (
    OldpAdapter,
    HudocAdapter,
    GovInfoAdapter,
    FrankfurterAdapter,
    EurostatAdapter,
    BundesbankAdapter,
    BisAdapter,
    CoinGeckoAdapter,
)

__all__ = [
    "__version__",
    # Rust core
    "fetch_and_extract",
    "batch_research",
    "fetch_smart_page",
    "extract_main_content_markdown",
    "fetch_html_full",
    # Toolbox
    "WebResearcherToolbox",
    "ToolboxConfig",
    # Document parsing
    "StructuredOxideParser",
    "ParsedDocumentPayload",
    "DocumentMetadata",
    "ExtractedPage",
    "ExtractedTable",
    # Token budgeting
    "count_tokens",
    "truncate_to_tokens",
    "fit_context_window",
    "estimate_markdown_tokens",
    "resolve_encoding",
    # Unified adapter interface + search providers
    "ResourceAdapter",
    "SearchProvider",
    "DuckDuckGoProvider",
    "GoogleProvider",
    "BingProvider",
    "ExaProvider",
    "BrowserOxideSearchProvider",
    "RateLimit",
    "RateState",
    "QuotaExhaustedError",
    "get_default_providers",
    "resolve_provider_name",
    # HTML metadata extraction
    "meta_extractor",
    "extract_all",
    "extract_meta",
    "extract_opengraph",
    "extract_twitter",
    "extract_jsonld",
    "merge_into_document_metadata",
    # Caching
    "Cache",
    # robots.txt compliance (S4)
    "RobotsChecker",
    # SSRF guard (S1)
    "SsrfBlockedError",
    "validate_public_url",
    # Workstream 2 helpers (also reachable as toolbox tools)
    "dedupe",
    "content_hash",
    "check_liveness",
    "format_citations",
    # Category routing (also reachable via research_by_category)
    "CATEGORIES",
    "DEFAULT_CATEGORY",
    "classify",
    "search_category",
    # Wave-3 domain adapters (verified live; see docs)
    "OldpAdapter",
    "HudocAdapter",
    "GovInfoAdapter",
    "FrankfurterAdapter",
    "EurostatAdapter",
    "BundesbankAdapter",
    "BisAdapter",
    "CoinGeckoAdapter",
]
