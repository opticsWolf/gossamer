"""Identifier-based merge for explicitly requested scholarly providers."""

from __future__ import annotations

import json
import re
from typing import Iterable
from urllib.parse import unquote

from gossamer.open_access import normalize_doi

_ARXIV_VERSION = re.compile(r"v\d+$", re.IGNORECASE)
_CONFLICT_FIELDS = ("title", "authors", "published", "url", "citations")


def _raw_record(record: dict) -> dict:
    raw = record.get("raw")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
            return decoded if isinstance(decoded, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}
    return {}


def _arxiv_id(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    ident = unquote(value.strip())
    lowered = ident.lower()
    for prefix in (
        "https://arxiv.org/abs/",
        "http://arxiv.org/abs/",
        "https://arxiv.org/pdf/",
        "http://arxiv.org/pdf/",
        "arxiv:",
    ):
        if lowered.startswith(prefix):
            ident = ident[len(prefix):]
            break
    ident = ident.rsplit("/", 1)[-1]
    if ident.lower().endswith(".pdf"):
        ident = ident[:-4]
    ident = _ARXIV_VERSION.sub("", ident)
    ident = ident.strip().lower()
    return ident or None


def _identifier_key(record: dict) -> str | None:
    doi = record.get("doi") or record.get("DOI")
    if not doi:
        doi = record.get("url")
    if doi:
        try:
            return f"doi:{normalize_doi(str(doi))}"
        except ValueError:
            pass

    raw = _raw_record(record)
    external_ids = raw.get("externalIds")
    if isinstance(external_ids, dict):
        arxiv = external_ids.get("ArXiv") or external_ids.get("arxiv")
        normalized = _arxiv_id(arxiv)
        if normalized:
            return f"arxiv:{normalized}"
    ids = raw.get("ids")
    if isinstance(ids, dict):
        normalized = _arxiv_id(ids.get("arxiv"))
        if normalized:
            return f"arxiv:{normalized}"

    if str(record.get("source", "")).lower() == "arxiv":
        normalized = _arxiv_id(record.get("id") or record.get("url"))
        if normalized:
            return f"arxiv:{normalized}"
    return None


def merge_scholarly_records(
    provider_records: Iterable[tuple[str, list[dict]]],
) -> list[dict]:
    """Merge records on DOI/arXiv IDs only, preserving all source records.

    Provider order is priority order for the canonical record. Records without
    a DOI or arXiv identifier are intentionally kept separate; titles are not
    used as a fuzzy merge key.
    """
    groups: dict[str, dict] = {}
    ordered: list[dict] = []
    for provider, records in provider_records:
        for record in records:
            key = _identifier_key(record)
            if key is None:
                ordered.append({
                    "key": None,
                    "canonical": dict(record),
                    "sources": [provider],
                    "source_records": [{"provider": provider, "record": dict(record)}],
                    "conflicts": {},
                })
                continue
            merged = groups.get(key)
            if merged is None:
                merged = {
                    "key": key,
                    "canonical": dict(record),
                    "sources": [],
                    "source_records": [],
                    "conflicts": {},
                }
                groups[key] = merged
                ordered.append(merged)
            if provider not in merged["sources"]:
                merged["sources"].append(provider)
            merged["source_records"].append({
                "provider": provider,
                "record": dict(record),
            })

    for merged in ordered:
        by_field: dict[str, list[dict]] = {}
        for source_record in merged["source_records"]:
            provider = source_record["provider"]
            record = source_record["record"]
            for field in _CONFLICT_FIELDS:
                value = record.get(field)
                if value is None or value == "":
                    continue
                entries = by_field.setdefault(field, [])
                if not any(entry["value"] == value for entry in entries):
                    entries.append({"provider": provider, "value": value})
        merged["conflicts"] = {
            field: values for field, values in by_field.items() if len(values) > 1
        }
    return ordered
