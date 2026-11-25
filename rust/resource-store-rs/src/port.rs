#![forbid(unsafe_code)]
use gossamer::resourcestore::detect_ext_from_magic;
use serde_json::Value;

pub fn service_detect_ext_from_magic(data: Value) -> Result<Option<String>, String> {
    let bytes = data
        .as_array()
        .or_else(|| data.get("$bytes").and_then(Value::as_array));
    let bytes = bytes.ok_or_else(|| "TypeError: expected bytes".to_string())?;
    let values: Vec<u8> = bytes
        .iter()
        .filter_map(Value::as_u64)
        .map(|value| value.min(255) as u8)
        .collect();
    Ok(detect_ext_from_magic(&values).map(str::to_string))
}
