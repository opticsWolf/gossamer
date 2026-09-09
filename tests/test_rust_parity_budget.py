"""Parity: budget kernels (v0.8.9) vs ``src/budget.rs``.

Covers two-pass truncation, JSON-fit testing, research shrinking
(incl. the crash paths and tail-dropping), payload shrinking, and
`json.dumps(indent=2, ensure_ascii=True)`-compatible serialization —
over hand-picked cases plus a seeded fuzzer over hostile JSON shapes
and Unicode text. `_fit_json` (Python build-callback) stays Python.
"""

import copy
import json
import random

import pytest

from gossamer import _core


# ── vendored originals (v0.8.9) ──────────────────────────────────

_V_TRUNC_MARK = "\n\n... [truncated]"
_V_SNIP_SUFFIX = "..."


def _v_truncate(text, char_limit, token_limit, model):
    from gossamer.token_budget import truncate_to_tokens

    if token_limit > 0:
        text = truncate_to_tokens(text, token_limit, model)
    if len(text) > char_limit:
        text = text[:char_limit] + _V_TRUNC_MARK
    return text


def _v_fits(text, char_limit, token_limit, model):
    from gossamer.token_budget import count_tokens

    if char_limit and len(text) > char_limit:
        return False
    if token_limit and count_tokens(text, model) > token_limit:
        return False
    return True


def _v_shrink_research(result, budget):
    out = copy.deepcopy(result)
    if budget is None:
        return json.dumps(out, indent=2)
    for source in out.get("sources", []):
        page = source.get("result")
        if isinstance(page, dict):
            md = page.get("markdown")
            if isinstance(md, str) and len(md) > budget:
                page["markdown"] = md[:budget] + _V_TRUNC_MARK
            if isinstance(page.get("follow_up_links"), list):
                page["follow_up_links"] = page["follow_up_links"][:5]
        snippet = source.get("snippet")
        if isinstance(snippet, str) and len(snippet) > budget:
            source["snippet"] = snippet[:budget] + _V_SNIP_SUFFIX
    keep = max(1, budget // 120)
    if len(out.get("sources", [])) > keep:
        out["sources_omitted"] = len(out["sources"]) - keep
        out["sources"] = out["sources"][:keep]
    return json.dumps(out, indent=2)


def _v_shrink_payload(payload_json, budget):
    if budget is None:
        return payload_json
    from gossamer.structured_parser import ParsedDocumentPayload

    payload = ParsedDocumentPayload.model_validate_json(payload_json)
    for page in payload.pages:
        if len(page.raw_text) > budget:
            page.raw_text = page.raw_text[:budget] + _V_TRUNC_MARK
        if len(page.markdown) > budget:
            page.markdown = page.markdown[:budget] + _V_TRUNC_MARK
    return payload.to_json()


def _outcome(fn, *args):
    try:
        return False, fn(*args)
    except Exception as e:  # noqa: BLE001
        return True, f"{type(e).__name__}: {e}"


TEXTS = [
    "", "short", "hello world foo bar " * 30,
    "héllo wörld ✓✓ 日本語テスト 👋" * 10,
    "a" * 5000, "line1\nline2\nline3\n",
]


@pytest.mark.parametrize("text", TEXTS)
@pytest.mark.parametrize("chars", [0, 5, 100, 100000])
@pytest.mark.parametrize("tokens", [0, 3, 50])
def test_truncate_parity(text, chars, tokens):
    assert _core.budget_truncate(text, chars, tokens, "gpt-4o") == _v_truncate(
        text, chars, tokens, "gpt-4o"
    )


@pytest.mark.parametrize("text", TEXTS)
@pytest.mark.parametrize("chars", [0, 5, 100, 100000])
@pytest.mark.parametrize("tokens", [0, 3, 50])
def test_fits_parity(text, chars, tokens):
    assert _core.budget_json_fits(text, chars, tokens, "gpt-4o") == _v_fits(
        text, chars, tokens, "gpt-4o"
    )


def test_content_split():
    assert _core.budget_content_split(1000, 500, 0.2) == (800, 400)
    assert _core.budget_content_split(1000, 0, 0.2) == (800, 0)


def _sources(n_md=20, n_snip=20, links=7):
    return {
        "query": "q",
        "sources": [
            {"result": {"markdown": "m" * n_md,
                        "follow_up_links": list(range(links))},
             "snippet": "s" * n_snip},
        ],
    }


RESEARCH_CASES = [
    _sources(),
    {"query": "q", "sources": []},
    {"query": "q"},
    {"query": "q", "sources": None},
    {"query": "q", "sources": "abc"},
    {"query": "q", "sources": {"a": 1}},
    {"query": "q", "sources": [None]},
    {"query": "q", "sources": [{"result": None, "snippet": None}]},
    {"query": "q", "sources": [{"result": {"markdown": 123}}]},
    {"query": "q", "sources": [{"snippet": "x" * 500}]},
    {"query": "q ünïcödé ✓", "sources": [
        {"result": {"markdown": "日本語" * 100}, "snippet": "é" * 100}]},
    [1, 2],
    "just a string",
    42,
    {"sources": [{"result": {"markdown": "m" * 2000}}] * 30},
]


@pytest.mark.parametrize("budget", [None, 0, 8, 100])
@pytest.mark.parametrize("doc", RESEARCH_CASES)
def test_shrink_research_parity(budget, doc):
    snap = json.dumps(doc)
    py_raised, py_val = _outcome(_v_shrink_research, doc, budget)
    rs_raised, rs_val = _outcome(_core.shrink_research_json, snap, budget)
    assert (py_raised, rs_raised) == (py_raised, py_raised), (budget, snap[:80])
    if not py_raised:
        assert rs_val == py_val, (budget, snap[:80])
    else:
        assert rs_val == py_val, (budget, snap[:80])


def _payload(n_pages=2, size=50):
    from gossamer.structured_parser import ParsedDocumentPayload

    pages = [
        {"page_number": i + 1,
         "raw_text": f"raw-{i}-" + "x" * size,
         "markdown": f"# Md-{i}\n" + "y" * size}
        for i in range(n_pages)
    ]
    return ParsedDocumentPayload(
        metadata={"source": "parity", "title": "t Ünïcödé ✓"},
        pages=pages,
    ).to_json()


@pytest.mark.parametrize("budget", [None, 0, 10, 10000])
def test_shrink_payload_parity(budget):
    snap = _payload()
    assert _core.shrink_payload_json(snap, budget) == _v_shrink_payload(snap, budget)


def test_fuzz_budget():
    rng = random.Random(20260905)
    texts = ["", "hi", "héllo ✓ 日本語", "a" * 2000,
             "mixed 123 !@# \n\t text"]
    for _ in range(60):
        text = rng.choice(texts) + "".join(
            rng.choice("ab ü✓") for _ in range(rng.randint(0, 100)))
        chars = rng.choice([0, 1, 5, 100, 100000])
        tokens = rng.choice([0, 1, 7, 200])
        assert _core.budget_truncate(text, chars, tokens, "gpt-4o") == _v_truncate(
            text, chars, tokens, "gpt-4o")
        assert _core.budget_json_fits(text, chars, tokens, "gpt-4o") == _v_fits(
            text, chars, tokens, "gpt-4o")
    for _ in range(60):
        n = rng.randint(0, 5)
        doc = {"sources": [
            {"result": {"markdown": rng.choice(texts) * rng.randint(0, 3)},
             "snippet": rng.choice(texts)}
            for _ in range(n)
        ]}
        budget = rng.choice([None, 0, 5, 64])
        snap = json.dumps(doc)
        assert _core.shrink_research_json(snap, budget) == _v_shrink_research(
            doc, budget)
