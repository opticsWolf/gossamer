//! Frozen MCP tool schema.
//!
//! The JSON snapshot is generated from `gossamer.mcp_server.build_server()` and is compared to the
//! checked-in fixture in Rust tests. Tool names, descriptions, enums, and required fields are part
//! of the external contract; changing them requires a new ADR.

/// Pretty-printed `list_tools()` payload with tool names, descriptions, and input schemas.
pub const TOOL_SCHEMA_JSON: &str = include_str!("mcp_schema_data.json");

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frozen_schema_matches_fixture() {
        let fixture = include_str!("../../fixtures/mcp_schema.json");
        assert_eq!(TOOL_SCHEMA_JSON, fixture);
    }

    #[test]
    fn schema_has_all_twelve_tools() {
        let parsed: serde_json::Value = serde_json::from_str(TOOL_SCHEMA_JSON)
            .unwrap_or_else(|e| panic!("frozen MCP schema must be JSON: {e}"));
        let tools = parsed
            .as_array()
            .unwrap_or_else(|| panic!("schema must be an array"));
        assert_eq!(tools.len(), 12);
        for tool in tools {
            assert!(
                tool.get("name")
                    .and_then(|v| v.as_str())
                    .is_some_and(|s| !s.is_empty())
            );
            assert!(
                tool.get("inputSchema")
                    .and_then(|v| v.as_object())
                    .is_some()
            );
        }
    }
}
