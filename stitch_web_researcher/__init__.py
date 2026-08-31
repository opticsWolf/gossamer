"""
High-Performance Web Researcher – Rust core + Oxide extractors.
"""

from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("stitch-web-researcher")
except Exception:  # pragma: no cover - fallback when installed without metadata
    __version__ = "0.0.0"

from stitch_web_researcher._core import fetch_and_extract, batch_research
from stitch_web_researcher._core import extract_main_content_markdown
from stitch_web_researcher._core import fetch_html_full
from stitch_web_researcher.agent_tools import (
    WebResearcherToolbox,
    ToolboxConfig,
    fetch_smart_page,
)
from stitch_web_researcher.structured_parser import (
    StructuredOxideParser,
    ParsedDocumentPayload,
    DocumentMetadata,
    ExtractedPage,
    ExtractedTable,
)
from stitch_web_researcher.token_budget import (
    count_tokens,
    truncate_to_tokens,
    fit_context_window,
    estimate_markdown_tokens,
    resolve_encoding,
)
from stitch_web_researcher.search_providers import (
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
from stitch_web_researcher import meta_extractor
from stitch_web_researcher.meta_extractor import (
    extract_all,
    extract_meta,
    extract_opengraph,
    extract_twitter,
    extract_jsonld,
    merge_into_document_metadata,
)
from stitch_web_researcher.cache import Cache
from stitch_web_researcher.robots import RobotsChecker
from stitch_web_researcher.ssrf import SsrfBlockedError, validate_public_url

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
]
