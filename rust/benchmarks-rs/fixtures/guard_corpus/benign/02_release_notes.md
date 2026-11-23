# Release notes — 4.2.0

## Added
- Sitemap-aware discovery: `discover_resources()` reads `robots.txt` and any
  declared sitemap before falling back to link scraping.
- HTML table extraction now returns a header row separately from the body.

## Changed
- The batch engine returns the source HTML alongside the markdown so metadata
  extraction produces the same fields as a single-page read.
- Section selection understands Setext headings.

## Fixed
- Serialized JSON is no longer cut at the character budget; oversized payloads
  are shrunk field-wise and stay parseable.
- A bare filename such as `report.pdf` is rejected as a path instead of being
  promoted to a hostname.

## Deprecated
- Passing keyword arguments to the toolbox constructor. Use the config object.

## Upgrade notes
No migration is required. The batch tuple gained a field; code that unpacked
three elements needs a fourth.
