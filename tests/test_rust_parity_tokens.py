"""Parity: ``token_budget`` (v0.8.5) vs ``src/tokens.rs``.

Covers model→encoding resolution (live tiktoken map vs vendored
registry + local table — a tiktoken upgrade that retargets a model
fails here, which is the drift alarm working), BPE counts (exact),
truncation (exact strings), packing, and the special-token error
surface (exact messages). ``gpt2`` resolves by name on both sides but
counts through the Python fallback (not embedded in Rust) — the
delegation contract, tested in ``test_gpt2_falls_back``.
"""

import pytest
import tiktoken

from gossamer import _core


# ── vendored originals (v0.8.5, encoder-present paths) ────────────

_V_TABLE = {
    "gpt-4": "cl100k_base", "gpt-4-0314": "cl100k_base",
    "gpt-4-0613": "cl100k_base", "gpt-4-32k": "cl100k_base",
    "gpt-4-32k-0314": "cl100k_base", "gpt-4-32k-0613": "cl100k_base",
    "gpt-4-0125-preview": "cl100k_base", "gpt-4-1106-preview": "cl100k_base",
    "gpt-4-turbo": "cl100k_base", "gpt-4-turbo-2024-04-09": "cl100k_base",
    "gpt-4o": "o200k_base", "gpt-4o-2024-05-13": "o200k_base",
    "gpt-4o-mini": "o200k_base", "gpt-4o-mini-2024-07-18": "o200k_base",
    "gpt-3.5-turbo": "cl100k_base", "gpt-3.5-turbo-0301": "p50k_base",
    "gpt-3.5-turbo-0613": "cl100k_base", "gpt-3.5-turbo-16k": "cl100k_base",
    "claude-3-opus": "cl100k_base", "claude-3-sonnet": "cl100k_base",
    "claude-3-haiku": "cl100k_base", "claude-3.5-sonnet": "cl100k_base",
    "claude-3.5-haiku": "cl100k_base",
}
_V_DEFAULT_ELLIPSIS = "\n\n... [truncated for token budget]"


def _v_resolve(model_name):
    key = model_name.lower().strip()
    try:
        return tiktoken.encoding_for_model(key).name
    except Exception:
        pass
    if key in _V_TABLE:
        return _V_TABLE[key]
    for known in sorted(_V_TABLE, key=len, reverse=True):
        if key.startswith(known):
            return _V_TABLE[known]
    return "cl100k_base"


def _v_count(text, model):
    return len(tiktoken.get_encoding(_v_resolve(model)).encode(text))


def _v_truncate(text, max_tokens, model, ellipsis=_V_DEFAULT_ELLIPSIS):
    if not text:
        return text
    enc = tiktoken.get_encoding(_v_resolve(model))
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return text
    reserve = max(1, len(enc.encode(ellipsis)))
    return enc.decode(tokens[: max(0, max_tokens - reserve)]) + ellipsis


def _v_fit(pieces, max_tokens, model):
    enc = tiktoken.get_encoding(_v_resolve(model))
    result, remaining = [], max_tokens
    for piece in pieces:
        if not piece:
            continue
        n = len(enc.encode(piece))
        if n <= remaining:
            result.append(piece)
            remaining -= n
        elif remaining > 10:
            result.append(_v_truncate(piece, remaining, model))
            break
        else:
            break
    return result


def _outcome(fn, *args):
    try:
        return False, fn(*args)
    except Exception as e:  # noqa: BLE001
        return True, f"{type(e).__name__}: {e}"


MODELS = [
    "gpt-4o", "GPT-4o ", "gpt-4o-2024-05-13", "gpt-4o-2024-08-06",
    "gpt-4o-mini", "gpt-4", "gpt-4-turbo", "gpt-4-32k",
    "gpt-3.5-turbo", "gpt-3.5-turbo-0301", "gpt-3.5-turbo-0613",
    "claude-3-sonnet", "claude-3.5-sonnet", "claude-3-haiku",
    "o1", "o1-mini", "o3", "gpt-4.1", "gpt-4.1-mini", "gpt-4.5-preview",
    "text-davinci-003", "gpt2", "gpt2-xl", "unknown-xyz", "",
    "davinci", "curie", "ft:gpt-4o:personal:abc123",
]


@pytest.mark.parametrize("model", MODELS)
def test_resolve_parity(model):
    assert _core.resolve_encoding(model) == _v_resolve(model), model


TEXTS = [
    "",
    "hello world",
    "The quick brown fox jumps over the lazy dog. " * 20,
    "héllo wörld ✓✓ naïve café",
    "日本語テストです。漢字とひらがな。",
    "Hello 👋🌍 emoji test αβγ",
    "# Markdown\n\n- list *item* [link](https://example.com)\n\n```code();```\n",
    "a" * 5000,
    "x\ny\tz\rcarriage",
    "Ünïcödé “quotes” — dashes…",
]


@pytest.mark.parametrize("model", ["gpt-4o", "gpt-4", "gpt-3.5-turbo-0301",
                                   "claude-3-sonnet"])
@pytest.mark.parametrize("text", TEXTS)
def test_count_exact_parity(model, text):
    py_raised, py_val = _outcome(_v_count, text, model)
    rs_raised, rs_val = _outcome(_core.count_tokens, text, model)
    assert (py_raised, rs_raised) == (False, False), (model, text[:30])
    assert rs_val == py_val, (model, text[:30])


SPECIALS = [
    "a <|endoftext|> b",
    "<|fim_prefix|>code<|fim_suffix|> (cl100k)",
    "x <|fim_prefix|> y <|endoftext|> z",
    "o200k <|endofprompt|> here",
    "not special <|nonsense|> text",
]


@pytest.mark.parametrize("text", SPECIALS)
def test_special_token_errors_parity(text):
    # Outcome comparison (raise + message), not blanket-raise: whether a
    # `<|…|>` sequence is special depends on the encoding (o200k has no
    # fim tokens; `<|nonsense|>` is special nowhere).
    for model in ("gpt-4o", "gpt-4"):
        py_raised, py_val = _outcome(_v_count, text, model)
        rs_raised, rs_val = _outcome(_core.count_tokens, text, model)
        assert (py_raised, rs_raised) == (py_raised, py_raised), (model, text)
        assert rs_val == py_val, (model, text)
    # …but the genuinely-special cases must raise on both sides.
    assert _outcome(_v_count, "a <|endoftext|> b", "gpt-4o")[0]
    assert _outcome(_core.count_tokens, "a <|endoftext|> b", "gpt-4o")[0]
    assert _outcome(_v_count, "<|fim_prefix|>x", "gpt-4")[0]
    assert _outcome(_core.count_tokens, "<|fim_prefix|>x", "gpt-4")[0]


@pytest.mark.parametrize("model", ["gpt-4o", "gpt-4", "gpt-3.5-turbo-0301"])
@pytest.mark.parametrize("budget", [0, 1, 2, 5, 50, 10000])
def test_truncate_exact_parity(model, budget):
    text = ("hello world, this is a longer test sentence for truncation. " * 8
            + "日本語テスト 👋")
    assert _core.truncate_to_tokens(text, budget, model) == _v_truncate(
        text, budget, model
    ), (model, budget)


def test_truncate_custom_ellipsis_and_empty():
    assert _core.truncate_to_tokens("", 5, "gpt-4o") == ""
    assert _core.truncate_to_tokens("hi", 0, "gpt-4o") == _v_truncate("hi", 0, "gpt-4o")
    assert _core.truncate_to_tokens(
        "hello world foo bar", 3, "gpt-4", "…"
    ) == _v_truncate("hello world foo bar", 3, "gpt-4", "…")


@pytest.mark.parametrize("model", ["gpt-4o", "gpt-4"])
@pytest.mark.parametrize("budget", [0, 5, 11, 50, 1000])
def test_fit_parity(model, budget):
    pieces = ["", "short", "a medium-length piece of text here",
              "hello world, this is a longer test sentence. " * 5,
              "日本語テストです"]
    assert _core.fit_context_window(pieces, budget, model) == _v_fit(
        pieces, budget, model
    ), (model, budget)


def test_gpt2_falls_back_to_python_tiktoken():
    # Resolves by name on both sides; counts flow through Python tiktoken
    # (not embedded in Rust) after delegation — values must still agree
    # with the pure-Python original.
    from gossamer import token_budget as tb

    assert _core.resolve_encoding("gpt2") == "gpt2"
    assert "gpt2" not in _core.embedded_encodings()
    text = "hello world, gpt2 fallback path"
    assert tb.count_tokens(text, "gpt2") == _v_count(text, "gpt2")
    assert tb.truncate_to_tokens(text, 3, "gpt2") == _v_truncate(text, 3, "gpt2")
