"""
High-Performance Web Researcher – Rust core + Oxide extractors.
"""

from stitch_web_researcher._core import fetch_and_extract, batch_research
from stitch_web_researcher.agent_tools import WebResearcherToolbox, fetch_smart_page
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
    DuckDuckGoProvider,
    GoogleProvider,
    BingProvider,
    ExaProvider,
    BrowserOxideSearchProvider,
    RateLimit,
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

__all__ = [
    # Rust core
    "fetch_and_extract",
    "batch_research",
    "fetch_smart_page",
    # Toolbox
    "WebResearcherToolbox",
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
    # Search providers
    "SearchProvider",
    "DuckDuckGoProvider",
    "GoogleProvider",
    "BingProvider",
    "ExaProvider",
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
]
