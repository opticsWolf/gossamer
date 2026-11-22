# Research: mcp (Model Context Protocol Python SDK)

Date: 2026-11-22.

## Sources
- PyPI metadata: https://pypi.org/pypi/mcp/json (version 2.3.0 at research time)
- Docs: https://py.sdk.modelcontextprotocol.io/
- Source: https://github.com/modelcontextprotocol/python-sdk
- Rust SDK candidate: https://docs.rs/rmcp/latest/rmcp/
- Local evidence: `gossamer/mcp_server.py`, `gossamer/agent_tools.py:TOOL_REGISTRY`

## What the original uses
Optional `mcp = ["mcp>=2.0"]` extra. `mcp_server.py` imports
`mcp.server.mcpserver.MCPServer`, builds tool wrappers from `TOOL_REGISTRY`, and exposes
stdio transport. The 12 tool schemas and instruction text are frozen by ADR-005 and tested by
`test_skill_drift.py`.

## Upstream facts
PyPI summary: "Model Context Protocol SDK", MIT, Python >=3.10. It depends on anyio, httpx,
jsonschema, pydantic, pyjwt, starlette/sse-starlette, and others. The Rust `rmcp` crate provides
an MCP SDK implementation but is not the same library.

## Rust options
1. Keep the Python MCP server as the compatibility surface until schema replay is complete.
2. Implement the stdio server in Rust with `rmcp`, generating schemas from the same frozen
   registry data and comparing exact JSON in app cases.
3. Do not add new tools or change descriptions without a new ADR.

## Fit and gaps
**Fit: medium.** Transport and tool registration are portable; exact schema text is the risk.
This should be ported after the core CLI paths and only with a full schema-parity suite.
