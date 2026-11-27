#![forbid(unsafe_code)]
use gossamer::resourcestore::{
    detect_ext_from_magic, embedded_figures, image_refs, rewrite_image_refs, store_url_decision,
};
use serde_json::{Value, json};

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

fn bytes_from_value(data: Value) -> Result<Vec<u8>, String> {
    let bytes = data
        .as_array()
        .or_else(|| data.get("$bytes").and_then(Value::as_array))
        .ok_or_else(|| "TypeError: expected bytes".to_string())?;
    Ok(bytes
        .iter()
        .filter_map(Value::as_u64)
        .map(|value| value.min(255) as u8)
        .collect())
}

pub fn service_store_url_decision(
    url: String,
    content_type: String,
    data: Value,
    max_bytes: usize,
    seen_filenames: Vec<String>,
) -> Result<Option<Value>, String> {
    let data = bytes_from_value(data)?;
    Ok(
        store_url_decision(&url, &content_type, &data, max_bytes, &seen_filenames).map(
            |decision| {
                json!({
                    "filename": decision.filename,
                    "digest": decision.digest,
                })
            },
        ),
    )
}

pub fn service_write_resource_bytes(
    files_dir: String,
    filename: String,
    data: Value,
) -> Result<(), String> {
    let data = bytes_from_value(data)?;
    let dir = std::path::PathBuf::from(files_dir);
    std::fs::create_dir_all(&dir)
        .and_then(|_| std::fs::write(dir.join(filename), data))
        .map_err(|error| format!("OSError: {error}"))
}

pub fn service_extract_image_refs(
    markdown: String,
    base_url: String,
) -> Result<Vec<Value>, String> {
    Ok(image_refs(&markdown, &base_url))
}

pub fn service_rewrite_image_refs(
    refs: Vec<Value>,
    markdown: String,
    stem: String,
    resolutions: Value,
) -> Result<Value, String> {
    Ok(rewrite_image_refs(
        &Value::Array(refs),
        &markdown,
        &stem,
        &resolutions,
    ))
}

pub fn service_embedded_figures(
    markdown: String,
    stem: String,
    images: Value,
) -> Result<Value, String> {
    let images = images.as_array().cloned().unwrap_or_default();
    Ok(embedded_figures(&markdown, &stem, &images))
}
