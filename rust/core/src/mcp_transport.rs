//! Deterministic MCP transport envelopes for the frozen 12-tool surface.

use serde_json::{Value, json};

/// Exact server instructions from the Python MCP server.
pub const INSTRUCTIONS: &str = include_str!("mcp_instructions.txt");
const RESOURCE_DESCRIPTION: &str = include_str!("mcp_resource_description.txt");
const SKILL_TEXT: &str = include_str!("mcp_skill.md");

/// `initialize` result matching MCP SDK 2.3.0.
pub fn initialize_result() -> Value {
    json!({
        "capabilities": {
            "prompts": {"listChanged": false},
            "resources": {"listChanged": false, "subscribe": false},
            "tools": {"listChanged": false},
        },
        "instructions": INSTRUCTIONS,
        "protocolVersion": "2024-11-05",
        "serverInfo": {"name": "gossamer", "version": ""},
    })
}

/// `tools/list` result built from the frozen schema.
pub fn list_tools_result() -> Value {
    let tools: Value = serde_json::from_str(crate::mcp::TOOL_SCHEMA_JSON)
        .unwrap_or_else(|error| panic!("frozen MCP schema must be JSON: {error}"));
    json!({"tools": tools})
}

/// `resources/list` result for the bundled skill pack.
pub fn list_resources_result() -> Value {
    json!({"resources": [{
        "description": RESOURCE_DESCRIPTION,
        "mimeType": "text/markdown",
        "name": "gossamer",
        "uri": "skills://gossamer",
    }]})
}

/// `resources/read` result, or `None` for an unknown URI.
pub fn read_resource(uri: &str) -> Option<Value> {
    if uri != "skills://gossamer" {
        return None;
    }
    Some(json!({"contents": [{
        "mimeType": "text/markdown",
        "text": SKILL_TEXT,
        "uri": uri,
    }]}))
}

/// Wrap a tool's returned JSON string in the MCP `tools/call` result shape.
pub fn tool_call_result(text: &str, is_error: bool) -> Value {
    json!({
        "content": [{"text": text, "type": "text"}],
        "isError": is_error,
    })
}

/// Build a complete JSON-RPC response line.
pub fn response(id: &Value, result: Value) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "result": result})
}

/// Build a complete JSON-RPC error line.
pub fn error_response(id: &Value, code: i64, message: &str) -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": id,
        "error": {"code": code, "message": message},
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn protocol_shapes() {
        assert_eq!(initialize_result()["protocolVersion"], json!("2024-11-05"));
        assert_eq!(
            list_tools_result()["tools"].as_array().map_or(0, Vec::len),
            12
        );
        assert_eq!(
            list_resources_result()["resources"][0]["uri"],
            json!("skills://gossamer")
        );
        assert!(read_resource("skills://gossamer").is_some());
        assert!(read_resource("skills://missing").is_none());
    }
}
