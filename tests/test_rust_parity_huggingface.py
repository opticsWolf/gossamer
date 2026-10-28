"""Parity: HuggingFace Hub parse kernels (models + papers).

Covers ``huggingface_parse_search`` / ``huggingface_parse_fetch`` /
``huggingface_papers_parse_search`` in ``src/adapters/scholar.rs`` over
vendored live fixtures (``tests/fixtures/huggingface_*.json``) plus hostile
shapes (nulls, wrong types, empties, exotic scalars) with exact error
comparison. URL building, HTTP, auth, card-body fetch and retry stay Python —
the httpx-mock tests cover that seam unchanged.
"""

import json
from pathlib import Path

import pytest

from gossamer import _core

FIX = Path(__file__).parent / "fixtures"
MODELS_SEARCH = json.loads((FIX / "huggingface_models_search.json").read_text())
MODEL_FETCH = json.loads((FIX / "huggingface_model_fetch.json").read_text())
PAPERS_SEARCH = json.loads((FIX / "huggingface_papers_search.json").read_text())


def _outcome(fn, *args):
    try:
        return False, fn(*args)
    except Exception as e:  # noqa: BLE103
        return True, f"{type(e).__name__}: {e}"


def _jtype(v):
    if v is None:
        return "NoneType"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "list"
    return "dict"


def _attr(t):
    return AttributeError(f"'{t}' object has no attribute 'get'")


def _slice(items, max_results):
    n = len(items)
    end = max(0, n + max_results) if max_results < 0 else min(max_results, n)
    return items[:end]


def _subscript(body, max_results):
    """Mirror ``subscript_hits``: missing → empty; lists sliced; strings
    sliced then failed per character; dicts raise the interpreter-age
    slice error; anything else raises TypeError."""
    if isinstance(body, list):
        return _slice(body, max_results)
    if isinstance(body, str):
        chars = list(body)
        end = (
            max(0, len(chars) + max_results)
            if max_results < 0
            else min(max_results, len(chars))
        )
        if end == 0:
            return []
        raise _attr("str")
    if isinstance(body, dict):
        # Perform the real slice so the interpreter raises its own spelling
        # (KeyError with a slice-object arg on 3.12+, TypeError before).
        return body[:max_results]
    raise TypeError(f"'{_jtype(body)}' object is not subscriptable")


def _hf_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _hf_tags(item, prefix):
    tags = item.get("tags")
    if not isinstance(tags, list):
        return []
    return [t[len(prefix):] for t in tags if isinstance(t, str) and t.startswith(prefix)]


def _v_model(item):
    if not isinstance(item, dict):
        raise _attr(_jtype(item))
    model_id = _hf_text(item.get("id"))
    url = f"https://huggingface.co/{model_id}" if model_id else ""
    pipeline_tag = _hf_text(item.get("pipeline_tag"))
    library_name = _hf_text(item.get("library_name"))
    parts = [p for p in (pipeline_tag, library_name) if p]
    likes = item.get("likes")
    if isinstance(likes, (int, float)) and not isinstance(likes, bool):
        parts.append(f"likes {likes}")
    downloads = item.get("downloads")
    if isinstance(downloads, (int, float)) and not isinstance(downloads, bool):
        parts.append(f"downloads {downloads}")
    tags = item.get("tags")
    tags = list(tags) if isinstance(tags, list) else []
    arxiv_ids = _hf_tags(item, "arxiv:")
    datasets = [t[len("dataset:"):] for t in tags if isinstance(t, str) and t.startswith("dataset:")]
    lic = next(
        (t[len("license:"):] for t in tags if isinstance(t, str) and t.startswith("license:")),
        "",
    )
    record = {
        "source": "huggingface",
        "id": model_id,
        "title": model_id,
        "url": url,
        "doi": "",
        "published": _hf_text(item.get("lastModified")),
        "authors": _hf_text(item.get("author")),
        "citations": downloads if isinstance(downloads, (int, float)) and not isinstance(downloads, bool) else 0,
        "snippet": "; ".join(parts),
        "fields": {
            "huggingface": {
                "likes": likes if likes is not None else None,
                "downloads": downloads if downloads is not None else None,
                "pipeline_tag": pipeline_tag,
                "library_name": library_name,
                "tags": tags,
                "private": item.get("private"),
                "gated": item.get("gated"),
                "disabled": item.get("disabled"),
                "sha": _hf_text(item.get("sha")),
                "arxiv_ids": arxiv_ids,
                "datasets": datasets,
                "license": lic,
            }
        },
    }
    return record


def _v_model_fetch(body):
    record = _v_model(body)
    if not isinstance(body, dict):
        return record  # unreachable: _v_model already raised
    namespaced = record["fields"]["huggingface"]
    namespaced["card_data"] = body.get("cardData")
    siblings = body.get("siblings")
    names = []
    if isinstance(siblings, list):
        for entry in siblings:
            if isinstance(entry, dict) and isinstance(entry.get("rfilename"), str):
                names.append(entry["rfilename"])
    namespaced["siblings"] = names
    return record


def _v_paper(item):
    if not isinstance(item, dict):
        raise _attr(_jtype(item))
    paper = item.get("paper")
    paper = paper if isinstance(paper, dict) else item
    arxiv_id = _hf_text(paper.get("id"))
    url = f"https://huggingface.co/papers/{arxiv_id}" if arxiv_id else ""
    authors = paper.get("authors")
    if isinstance(authors, list):
        names = []
        for author in authors:
            if isinstance(author, dict):
                name = _hf_text(author.get("name"))
                if name:
                    names.append(name)
        authors = ", ".join(names)
    else:
        authors = ""
    summary = _hf_text(paper.get("summary"))
    published = _hf_text(paper.get("publishedAt")) or _hf_text(item.get("publishedAt"))
    upvotes = paper.get("upvotes")
    upvotes = upvotes if isinstance(upvotes, (int, float)) and not isinstance(upvotes, bool) else 0
    return {
        "source": "huggingface-papers",
        "id": arxiv_id,
        "arxiv_id": arxiv_id,
        "title": _hf_text(paper.get("title")),
        "url": url,
        "doi": "",
        "published": published,
        "authors": authors,
        "citations": 0,
        "snippet": summary[:240],
        "fields": {"huggingface-papers": {"upvotes": upvotes}},
    }


def _rs_model_search(body, max_results=5):
    return json.loads(_core.huggingface_parse_search(json.dumps(body), max_results))


def _v_model_search(body, max_results=5):
    return [_v_model(item) for item in _subscript(body, max_results)]


def _rs_model_fetch(body):
    return json.loads(_core.huggingface_parse_fetch(json.dumps(body)))


def _rs_papers_search(body, max_results=5):
    return json.loads(_core.huggingface_papers_parse_search(json.dumps(body), max_results))


def _v_papers_search(body, max_results=5):
    return [_v_paper(item) for item in _subscript(body, max_results)]


MODEL_SEARCH_BODIES = [
    MODELS_SEARCH,
    MODELS_SEARCH[:1],
    [],
    [{}],
    [None],
    ["x"],
    [5],
    [True],
    [{"id": None, "likes": None, "tags": None}],
    [{"id": 7, "likes": "many", "downloads": "lots", "tags": "abc"}],
    [{"id": "a/b", "likes": True, "downloads": 1.5, "tags": [1, None, "arxiv:1"]}],
    [{"id": "a/b", "pipeline_tag": None, "library_name": 5}],
    {},
    "",
    "ab",
    0,
    5,
    None,
    True,
]


@pytest.mark.parametrize("body", MODEL_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_huggingface_model_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_model_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_model_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


MODEL_FETCH_BODIES = [
    MODEL_FETCH,
    {},
    {"id": "a/b"},
    {"id": "a/b", "siblings": None, "cardData": None},
    {"id": "a/b", "siblings": "x", "cardData": [1]},
    {"id": "a/b", "siblings": [None, "x", {}, {"rfilename": 5}, {"rfilename": "README.md"}]},
    [],
    [MODEL_FETCH],
    "",
    "ab",
    0,
    5,
    None,
    True,
]


@pytest.mark.parametrize("body", MODEL_FETCH_BODIES)
def test_huggingface_model_fetch_parity(body):
    py_raised, py_val = _outcome(_v_model_fetch, body)
    rs_raised, rs_val = _outcome(_rs_model_fetch, body)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body


PAPERS_SEARCH_BODIES = [
    PAPERS_SEARCH,
    PAPERS_SEARCH[:1],
    [],
    [{}],
    [None],
    ["x"],
    [5],
    [{"paper": None}],
    [{"paper": "x"}],
    [{"paper": {}}],
    [{"id": "1.2", "title": "Bare", "summary": "s" * 500, "upvotes": 3,
      "authors": [{"name": "A"}, {"x": 1}, "s", None], "publishedAt": "2024-01-01"}],
    [{"paper": {"id": 7, "upvotes": "many", "authors": "ab", "summary": None}}],
    {},
    "",
    "ab",
    0,
    5,
    None,
    True,
]


@pytest.mark.parametrize("body", PAPERS_SEARCH_BODIES)
@pytest.mark.parametrize("max_results", [1, 5, -1, 0, 100])
def test_huggingface_papers_search_parity(body, max_results):
    py_raised, py_val = _outcome(_v_papers_search, body, max_results)
    rs_raised, rs_val = _outcome(_rs_papers_search, body, max_results)
    assert (py_raised, rs_raised) == (py_raised, py_raised), body
    assert rs_val == py_val, body