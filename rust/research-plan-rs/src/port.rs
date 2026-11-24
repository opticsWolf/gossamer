#![forbid(unsafe_code)]
use gossamer::service::research::research_plan;
use serde_json::Value;
fn depth_value(value: Option<&Value>) -> i64 {
    match value {
        Some(Value::Number(number)) => number.as_i64().unwrap_or(5),
        Some(Value::String(text)) => text.parse::<i64>().unwrap_or(5),
        _ => 5,
    }
}
pub fn service_research_plan(
    topic: String,
    depth: Option<Value>,
    max_results: Option<Value>,
    max_pages: i64,
) -> Result<Value, String> {
    let results = match max_results {
        Some(Value::Number(number)) => number.as_i64(),
        Some(Value::String(text)) => text.parse::<i64>().ok(),
        _ => None,
    };
    Ok(research_plan(
        &topic,
        depth_value(depth.as_ref()),
        results,
        max_pages,
    ))
}
