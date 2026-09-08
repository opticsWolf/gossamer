# Gossamer vs pdf-oxide: Image Extraction Findings

Date: 2026-09-07
Scope: `D:/User/Documents/Python/stitch-web-researcher` (`gossamer==0.8.15`, `pdf-oxide==0.3.77`)
Trigger: `D:/User/Documents/Python/Macrame_docs/literature/s42488-026-00162-x.pdf` (26 pages) → `.md` + pics

## TL;DR

Image extraction is **not missing in pdf-oxide** — it is **unused by gossamer**.
`pdf-oxide 0.3.77` supports `extract_images()`, `extract_image_bytes()`, and
`to_markdown(..., include_images=True, image_output_dir=..., embed_images=...)`.
Gossamer calls `to_markdown_all()` with defaults (`include_images=False`) and
documents the old limitation in comments, so PDFs store as text-only with an
empty resource manifest.

## 1. What pdf-oxide actually supports

Installed: `.venv/Lib/site-packages/pdf_oxide/__init__.py`, `VERSION = 0.3.77`.

From `help(PdfDocument)` + https://pdf.oxide.fyi/python/docs/getting-started:

```python
from pdf_oxide import PdfDocument
doc = PdfDocument("input.pdf")

doc.extract_images(page, region=None)
# -> list[ImageInfo], incl. images in content streams + nested Form XObjects

doc.extract_image_bytes(page)
# -> list[{width:int, height:int, data:bytes, format:'png'|'jpeg'}]
for i, img in enumerate(images):
    open(f"image_{i}.{img['format']}", "wb").write(img["data"])

doc.to_markdown(page, preserve_layout=False, detect_headings=True,
                include_images=False, image_output_dir=None, embed_images=True,
                include_form_fields=True, include_artifacts=True)
doc.to_markdown_all(...)  # same flags
```

Other relevant methods: `page_images(page)`, `render_page()`, `render_pixmap()`,
new `PdfPage` API since v0.3.34 (`page.text`, `page.images`, `page.markdown()`, `page.render()`).

Official snippets (PyPI `pdf-oxide/0.2.4`, `pdf.oxide.fyi/docs/comparison/python`):

```rust
// Rust
ConversionOptions { detect_headings: true, include_images: true,
  preserve_layout: false, image_output_dir: Some("./extracted_images".into()) }
doc.to_markdown(0, options)
```

## 2. What gossamer does today

* `gossamer/structured_parser.py`: `require_pdf_oxide().from_bytes(data).to_markdown_all()` — no `include_images`.
* `gossamer/document.py:_extract_from_bytes()`: same, `doc.to_markdown_all()` for `.pdf`.
* `gossamer/document.py:_store_resources()` docstring:
  > “PDF and office converters drop images from the extracted markdown, and the bundled pdf_oxide detects images but cannot extract their bytes, so embedded PDF images are not currently retrievable”
* `gossamer/resource_store.py` header:
  > “PDF / office converters drop images entirely — the markdown has no image refs at all”
  `ResourceStore.extract()` only rewrites existing `![...](url)` refs → empty manifest for PDFs.
  `ResourceStore.extract_embedded(images, ...)` already exists for “formats whose converter dropped the images” but has no PDF caller.

Result: `extract_document(..., store=True)` writes correct `.md` text + empty `.files/` manifest.

## 3. Verification on s42488-026-00162-x.pdf

```python
doc = PdfDocument("s42488-026-00162-x.pdf")
len(doc)  # 26
doc.extract_images(4)       # 1 item (0-based p.4 = Fig.1)
doc.extract_image_bytes(4)  # 1 PNG
doc.to_markdown(4, include_images=True, image_output_dir=td, embed_images=False)
# -> writes page5_1.png, appends ![Image 1 from page 5](.../page5_1.png)
```

Scan all pages: images on 1-based pages `5, 8, 10, 11, 12, 13, 15, 17` (1–2 each), rest 0. Pages 0/3/5 (0-based) correctly return `[]` — figures there are vector, not raster.

## 4. Fix proposal (small)

1. Thread flags through `extract_document(source, ..., include_images=False, image_output_dir=None)` → `to_markdown_all(...)`.
2. When `store=True + include_images=True`: call `extract_image_bytes(i)` per page (or `to_markdown(..., include_images=True, image_output_dir=<stem>.files)`), then `ResourceStore.extract_embedded()` to link them.
3. Update stale comments in `document.py` + `resource_store.py` (bytes *are* retrievable since ≥0.3.x).
4. Optional: expose `extract_images` metadata (bbox via `page_images()`) for figure-caption alignment.

## 5. Repro

```
D:/User/Documents/Python/stitch-web-researcher/.venv/Scripts/python.exe -c "from pdf_oxide import PdfDocument; d=PdfDocument('D:/User/Documents/Python/Macrame_docs/literature/s42488-026-00162-x.pdf'); print(len(d), d.extract_images(4))"
```

## 6. Skill definition gap (`SKILL.md`)

Source: `C:/Users/Main/.pi/agent/skills/gossamer/SKILL.md` (2026-09-07).

Current Auth section states only:

> `API keys live in the keystore (~/.gossamer/keys.json; python -m gossamer.keystore --init)`

This is true as a fallback but misleading in practice:

1. `~/.gossamer/` is keys/config only and lazily created (see `gossamer/settings.py:_home_dir()`,
   `keystore.py:init_keystore()`). Local PDF + public search needs no keys, so the dir
   never exists — `ls: cannot access 'C:/Users/Main/.gossamer/': No such file` is normal,
   not a broken install.
2. Cache is a different path: default `./.gossamer_cache` (`ToolboxConfig.cache_dir`), here
   overridden to `D:/User/Documents/Python/stitch-web-researcher/.gossamer_cache` via
   `GOSSAMER_CACHE_DIR` in `C:/Users/Main/.pi/agent/mcp.json` (`mcp_server.py:_config_from_env()`).
3. No precedence documented. Actual order (`env.py:getenv()`, `settings.py`):
   `GOSSAMER_* env > legacy STITCH_* env > keystore file > gossamer.json:keys > default`;
   config `explicit > $GOSSAMER_CONFIG > ./gossamer.json > ~/.gossamer/config.json`;
   keystore `explicit > $GOSSAMER_KEYSTORE > gossamer.json:keystore > ~/.gossamer/keys.json`.
4. Skill also omits PDF image limits: `extract_document` is text-only by default
   (`to_markdown_all(include_images=False)`), `store=True` yields empty `.files/` manifest,
   large PDFs need `pages=` ranges — all relevant when users ask for “md incl pics”.

Proposed 3-line patch for `SKILL.md` (Auth/Cache):

```md
## Config / cache (where stuff actually is)
- Cache: `GOSSAMER_CACHE_DIR` (see `mcp.json`) > `gossamer.json:cache_dir` > `./.gossamer_cache`.
- Keys: `$GOSSAMER_KEYSTORE` > `gossamer.json:keystore` > `~/.gossamer/keys.json` (created only via `keystore --init`, absent = normal).
- Check effective paths in `mcp.json` + `gossamer keystore --check`, not `~/.gossamer`.
```
