//! Deterministic request-planning kernels for live provider calls.
//!
//! Concrete Python adapters still perform the HTTP request, but every
//! auth/header/query mutation can be described as policy data and evaluated
//! here. This keeps secrets out of the core: values arrive in `context` and
//! are only copied into the returned request plan.

use serde_json::{Map, Value, json};

fn type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(_) => "float",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

fn object(value: &Value, name: &str) -> Result<Map<String, Value>, String> {
    match value {
        Value::Null => Ok(Map::new()),
        Value::Object(map) => Ok(map.clone()),
        other => Err(format!(
            "TypeError: {name} must be an object, got {}",
            type_name(other)
        )),
    }
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().is_none_or(|number| number != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(items) => !items.is_empty(),
    }
}

fn string_value(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Null => String::new(),
        Value::Bool(flag) => if *flag { "True" } else { "False" }.to_string(),
        Value::Number(number) => number.to_string(),
        Value::Array(_) | Value::Object(_) => value.to_string(),
    }
}

fn render(template: &str, context: &Map<String, Value>) -> String {
    let mut rendered = template.to_string();
    for (key, value) in context {
        rendered = rendered.replace(&format!("{{{key}}}"), &string_value(value));
    }
    rendered
}

fn rule_value(rule: &Value, context: &Map<String, Value>) -> Option<String> {
    if let Some(from) = rule.get("from").and_then(Value::as_str) {
        let value = context.get(from)?;
        return truthy(value).then(|| string_value(value));
    }
    if let Some(template) = rule.get("template").and_then(Value::as_str) {
        return Some(render(template, context));
    }
    rule.get("value").map(string_value)
}

fn apply_rules(
    target: &mut Map<String, Value>,
    rules: &Value,
    context: &Map<String, Value>,
    field: &str,
) -> Result<(), String> {
    let Some(items) = rules.as_array() else {
        if rules.is_null() {
            return Ok(());
        }
        return Err(format!(
            "TypeError: {field} policy must be a list, got {}",
            type_name(rules)
        ));
    };
    for (index, rule) in items.iter().enumerate() {
        let Value::Object(rule) = rule else {
            return Err(format!(
                "TypeError: {field} policy[{index}] must be an object, got {}",
                type_name(rule)
            ));
        };
        let Some(key) = rule.get("key").and_then(Value::as_str) else {
            return Err(format!("ValueError: {field} policy[{index}] has no key"));
        };
        let when = rule.get("when").and_then(Value::as_str).unwrap_or("always");
        if when != "always" && !context.get(when).is_some_and(truthy) {
            continue;
        }
        let Some(value) = rule_value(&Value::Object(rule.clone()), context) else {
            continue;
        };
        let overwrite = rule.get("mode").and_then(Value::as_str) == Some("overwrite");
        if overwrite || !target.contains_key(key) {
            target.insert(key.to_string(), Value::String(value));
        }
    }
    Ok(())
}

/// Build a provider request plan from a declarative auth policy.
///
/// `policy` accepts `headers` and `params` rule lists. Each rule has a `key`,
/// a `when` context gate (`always` by default), a `mode` of `setdefault`
/// (default) or `overwrite`, and a value from `from`, `template`, or literal
/// `value`. The returned object preserves input insertion order so it can be
/// handed directly to an HTTP client.
pub fn provider_request_plan(
    url: &str,
    params: &Value,
    headers: &Value,
    policy: &Value,
    context: &Value,
) -> Result<Value, String> {
    let mut out_params = object(params, "params")?;
    let mut out_headers = object(headers, "headers")?;
    let context = object(context, "context")?;
    let policy = object(policy, "policy")?;
    apply_rules(
        &mut out_headers,
        policy.get("headers").unwrap_or(&Value::Null),
        &context,
        "headers",
    )?;
    apply_rules(
        &mut out_params,
        policy.get("params").unwrap_or(&Value::Null),
        &context,
        "params",
    )?;
    Ok(json!({
        "url": url,
        "params": Value::Object(out_params),
        "headers": Value::Object(out_headers),
    }))
}

#[cfg(test)]
mod tests {
    use super::provider_request_plan;
    use serde_json::json;

    #[test]
    fn openalex_policy_matches_python() {
        let policy = json!({
            "headers": [
                {"key": "Contact-Agent", "template": "{ua} (mailto:{email})", "when": "email"},
                {"key": "User-Agent", "template": "{ua} (mailto:{email})", "when": "email"},
                {"key": "User-Agent", "template": "{ua}"}
            ],
            "params": [
                {"key": "mailto", "from": "email", "when": "email"},
                {"key": "api_key", "from": "api_key", "when": "api_key"}
            ]
        });
        let context = json!({"ua": "gossamer/1", "email": "dev@example.test", "api_key": ""});
        let plan = provider_request_plan(
            "https://api.openalex.org/works",
            &json!({"filter": "x"}),
            &json!({"Accept": "application/json"}),
            &policy,
            &context,
        )
        .expect("openalex plan");
        assert_eq!(
            plan["headers"]["User-Agent"],
            "gossamer/1 (mailto:dev@example.test)"
        );
        assert_eq!(
            plan["headers"]["Contact-Agent"],
            "gossamer/1 (mailto:dev@example.test)"
        );
        assert_eq!(plan["params"]["mailto"], "dev@example.test");
        assert!(plan["params"].get("api_key").is_none());
    }

    #[test]
    fn setdefault_preserves_caller_values() {
        let policy = json!({
            "headers": [{"key": "User-Agent", "value": "rust", "mode": "overwrite"}],
            "params": [{"key": "q", "value": "default", "mode": "setdefault"}]
        });
        let plan = provider_request_plan(
            "https://example.test",
            &json!({"q": "caller"}),
            &json!({"User-Agent": "caller"}),
            &policy,
            &json!({}),
        )
        .expect("plan");
        assert_eq!(plan["headers"]["User-Agent"], "rust");
        assert_eq!(plan["params"]["q"], "caller");
    }

    #[test]
    fn rejects_non_object_inputs() {
        let error = provider_request_plan(
            "https://example.test",
            &json!([]),
            &json!({}),
            &json!({}),
            &json!({}),
        )
        .expect_err("params must be an object");
        assert_eq!(error, "TypeError: params must be an object, got list");
    }
}
