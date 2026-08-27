# ───────────────────────────────
# P8: Tool registry — single source of truth
# ───────────────────────────────
# Every surface (LLM function-calling definitions, MCP server tools, and
# the execute_tool dispatcher) is generated from TOOL_REGISTRY, so the
# tool surface cannot drift across entry points.

_MISSING = object()  # sentinel: parameter is required (no default)


class ToolParam:
    """One parameter of a registry tool."""

    __slots__ = ("name", "type", "default", "description", "enum")

    def __init__(self, name, type, default=_MISSING, description="", enum=None):
        self.name = name
        self.type = type  # Python annotation: str / int / bool / list[str]
        self.default = default  # _MISSING when required
        self.description = description
        self.enum = enum  # optional list of allowed values

    @property
    def required(self) -> bool:
        return self.default is _MISSING

    @property
    def json_schema(self) -> dict:
        if self.type is str:
            schema = {"type": "string"}
        elif self.type is int:
            schema = {"type": "integer"}
        elif self.type is bool:
            schema = {"type": "boolean"}
        else:  # list[str]
            schema = {"type": "array", "items": {"type": "string"}}
        if self.enum is not None:
            schema["enum"] = self.enum
        if self.description:
            schema["description"] = self.description
        if not self.required:
            schema["default"] = self.default
        return schema


class ToolSpec:
    """One registry tool: surface description plus the
    ``WebResearcherToolbox`` method it dispatches to."""

    __slots__ = ("name", "description", "method", "params")

    def __init__(self, name, description, method, params):
        self.name = name
        self.description = description
        self.method = method
        self.params = tuple(params)

    def llm_definition(self) -> dict:
        """Function-calling definition for LLM consumers."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {p.name: p.json_schema for p in self.params},
                    "required": [p.name for p in self.params if p.required],
                },
            },
        }

    def kwargs(self, arguments: Optional[dict] = None) -> dict:
        """Registry defaults plus caller-supplied arguments."""
        merged = {p.name: p.default for p in self.params if not p.required}
        merged.update(arguments or {})
        return merged


TOOL_REGISTRY = (
    ToolSpec(
        "search_web",
        "Search the web using one or more search providers. Set provider to choose a specific engine; falls back through others on failure.",
        "search_web",
        (
            ToolParam("query", str, description="The search query"),
            ToolParam(
                "max_results",
                int,
                5,
                "Maximum number of results to return (default: 5)",
            ),
            ToolParam(
                "provider",
                str,
                "duckduckgo",
                "Search engine to prefer. Falls back through other providers on failure.",
                enum=["duckduckgo", "google", "bing", "exa"],
            ),
        ),
    ),
    ToolSpec(
        "inspect_html_page",
        "Fetch and extract markdown content from a web page. Set use_smart=True for JS-rendered pages (SPA, anti-bot). Returns markdown text and follow-up links.",
        "inspect_html_page",
        (
            ToolParam("url", str, description="The URL to inspect"),
            ToolParam(
                "use_smart",
                bool,
                False,
                "If true, use headless browser rendering (browser_oxide) for JS-heavy pages",
            ),
        ),
    ),
    ToolSpec(
        "batch_inspect_pages",
        "Fetch multiple web pages concurrently. Returns markdown and links for each.",
        "batch_inspect_pages",
        (
            ToolParam("urls", list[str], description="List of URLs to inspect"),
        ),
    ),
    ToolSpec(
        "extract_document",
        "Extract text content from PDF, DOCX, or XLSX documents via URL or local path.",
        "extract_document",
        (
            ToolParam(
                "source",
                str,
                description="URL or local file path to the document",
            ),
        ),
    ),
    ToolSpec(
        "extract_document_structured",
        "Extract structured content (metadata, pages, tables) from PDF, DOCX, XLSX, or PPTX documents via URL or local path. Returns a validated ParsedDocumentPayload as JSON.",
        "extract_document_structured",
        (
            ToolParam(
                "source",
                str,
                description="URL or local file path to the document",
            ),
        ),
    ),
    ToolSpec(
        "inspect_html_structured",
        "Fetch a web page and return it as a structured ParsedDocumentPayload with metadata (OG, Twitter, JSON-LD), markdown content, and links. Set use_smart=True for JS-rendered pages.",
        "inspect_html_structured",
        (
            ToolParam("url", str, description="The URL to inspect"),
            ToolParam(
                "use_smart",
                bool,
                False,
                "If true, use headless browser rendering (browser_oxide) for JS-heavy pages",
            ),
        ),
    ),
    ToolSpec(
        "clear_cache",
        "Clear both the in-memory and disk research caches and the visited-URL set. Use when you want to force fresh fetches (e.g., starting a new research session or suspecting stale content). Returns confirmation with post-clear statistics.",
        "clear_cache",
        (),
    ),
    ToolSpec(
        "reset_visited",
        "Forget all previously visited URLs so they can be fetched again (caches are NOT cleared). Use after a fetch failure you want to retry, or when starting a new research session on the same pages.",
        "reset_visited",
        (),
    ),
    ToolSpec(
        "get_stats",
        "Return toolbox statistics: visited URLs, cache hit rate and size, token budget settings.",
        "get_stats",
        (),
    ),
)

# Module-level LLM function-calling tool definitions — derived from the
# registry (P8) so the LLM surface can never drift from it.
_LLM_TOOL_DEFINITIONS = tuple(spec.llm_definition() for spec in TOOL_REGISTRY)
