"""
Token-aware truncation utilities for LLM context windows.

Uses tiktoken (OpenAI's BPE tokenizer) to count and truncate text
to a precise token budget.  Falls back gracefully to character-based
truncation if tiktoken is unavailable or the model is unrecognized.

Supported model families
------------------------
- OpenAI: gpt-4o (o200k_base), gpt-4, gpt-3.5-turbo  (cl100k_base / p50k_base)
- Anthropic: claude-3-opus, claude-3-sonnet, claude-3-haiku  (cl100k_base)
- Any other model defaults to cl100k_base (safe overestimate)

When tiktoken is installed, resolve_encoding() prefers tiktoken's own
model→encoding map (kept in sync with OpenAI releases) and falls back
to the local table below for models tiktoken does not know (e.g.
Anthropic) or when tiktoken is unavailable. (M5)
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────
# 1. Model → encoding name mapping
# ────────────────────────────────────────────────────────────────

_MODEL_ENCODING: dict[str, str] = {
    # OpenAI cl100k_base models (GPT-4, GPT-3.5-turbo, etc.)
    "gpt-4": "cl100k_base",
    "gpt-4-0314": "cl100k_base",
    "gpt-4-0613": "cl100k_base",
    "gpt-4-32k": "cl100k_base",
    "gpt-4-32k-0314": "cl100k_base",
    "gpt-4-32k-0613": "cl100k_base",
    "gpt-4-0125-preview": "cl100k_base",
    "gpt-4-1106-preview": "cl100k_base",
    "gpt-4-turbo": "cl100k_base",
    "gpt-4-turbo-2024-04-09": "cl100k_base",
    # GPT-4o family uses o200k_base (M5: was wrongly cl100k_base)
    "gpt-4o": "o200k_base",
    "gpt-4o-2024-05-13": "o200k_base",
    "gpt-4o-mini": "o200k_base",
    "gpt-4o-mini-2024-07-18": "o200k_base",
    "gpt-3.5-turbo": "cl100k_base",
    "gpt-3.5-turbo-0301": "p50k_base",
    "gpt-3.5-turbo-0613": "cl100k_base",
    "gpt-3.5-turbo-16k": "cl100k_base",
    # Anthropic Claude 3 family shares cl100k_base with OpenAI
    "claude-3-opus": "cl100k_base",
    "claude-3-sonnet": "cl100k_base",
    "claude-3-haiku": "cl100k_base",
    "claude-3.5-sonnet": "cl100k_base",
    "claude-3.5-haiku": "cl100k_base",
}

# Default encoding used when the model name is unknown
_DEFAULT_ENCODING = "cl100k_base"

# ────────────────────────────────────────────────────────────────
# 2. Lazy encoder cache
# ────────────────────────────────────────────────────────────────

_encoders: dict[str, object] = {}
_tiktoken_available: bool = True

try:
    import tiktoken  # noqa: F401
except ImportError:
    _tiktoken_available = False
    logger.warning("tiktoken not installed — token counting will use char fallback")


def _get_encoder(encoding_name: str) -> Optional["tiktoken.Encoding"]:
    """Return a cached tiktoken Encoding, or None if unavailable."""
    if not _tiktoken_available:
        return None
    if encoding_name not in _encoders:
        import tiktoken

        _encoders[encoding_name] = tiktoken.get_encoding(encoding_name)
    return _encoders[encoding_name]  # type: ignore[return-value]


# ────────────────────────────────────────────────────────────────
# 3. Public API
# ────────────────────────────────────────────────────────────────

def resolve_encoding(model_name: str) -> str:
    """
    Map a model name to a tiktoken encoding name.

    Returns the encoding string (e.g. ``"cl100k_base"``).  When
    tiktoken is installed its own model map is authoritative (e.g.
    ``gpt-4o -> o200k_base``); otherwise the local table is used.  Unknown
    models fall back to ``cl100k_base``.
    """
    # Normalize
    key = model_name.lower().strip()
    # M5: prefer tiktoken's own model->encoding map; it tracks OpenAI
    # releases (the local table below was stale: gpt-4o was mapped to
    # cl100k_base, which over-counts tokens vs o200k_base).
    if _tiktoken_available:
        try:
            import tiktoken

            return tiktoken.encoding_for_model(key).name
        except Exception:
            pass  # model unknown to tiktoken -> fall back to the table
    # Exact match first
    if key in _MODEL_ENCODING:
        return _MODEL_ENCODING[key]
    # Longest prefix match (e.g. "gpt-4o-2024-08-06" must hit "gpt-4o",
    # not the shorter "gpt-4" key that precedes it in the table). (M5)
    for known in sorted(_MODEL_ENCODING, key=len, reverse=True):
        if key.startswith(known):
            return _MODEL_ENCODING[known]
    return _DEFAULT_ENCODING


def count_tokens(text: str, model_name: str = "gpt-4o") -> int:
    """
    Count the approximate number of tokens in *text* for a given model.

    Parameters
    ----------
    text : str
        Text to tokenize.
    model_name : str
        Target model (e.g. ``"gpt-4o"``, ``"claude-3-sonnet"``).

    Returns
    -------
    int
        Token count.  Falls back to ``len(text) / 4`` if tiktoken
        is unavailable.
    """
    encoding_name = resolve_encoding(model_name)
    encoder = _get_encoder(encoding_name)
    if encoder is not None:
        return len(encoder.encode(text))

    # Fallback: ~4 chars per token is a common heuristic
    return max(1, len(text) // 4) if text else 0


def truncate_to_tokens(
    text: str,
    max_tokens: int,
    model_name: str = "gpt-4o",
    ellipsis: str = "\n\n... [truncated for token budget]",
) -> str:
    """
    Truncate *text* so it fits within *max_tokens* tokens.

    Truncation cuts at token boundaries (not mid-token), then appends
    an *ellipsis* marker.  If the text already fits it is returned
    unchanged.

    Parameters
    ----------
    text : str
        Source text.
    max_tokens : int
        Maximum token budget.
    model_name : str
        Target model for tokenization.
    ellipsis : str
        Suffix appended when truncation occurs.

    Returns
    -------
    str
        Truncated text (or original if it fits).
    """
    if not text:
        return text

    encoding_name = resolve_encoding(model_name)
    encoder = _get_encoder(encoding_name)

    if encoder is not None:
        tokens = encoder.encode(text)
        if len(tokens) <= max_tokens:
            return text
        # Reserve tokens for the ellipsis
        ellipsis_tokens = len(encoder.encode(ellipsis))
        reserve = max(1, ellipsis_tokens)
        truncated_tokens = tokens[: max(0, max_tokens - reserve)]
        truncated = encoder.decode(truncated_tokens)
        return truncated + ellipsis

    # Fallback: char-based heuristic (~4 chars per token)
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    # Reserve room for the ellipsis; clamp to 0 so a budget smaller
    # than the ellipsis yields just the marker instead of a negative
    # slice (which would return almost the whole string). (M4)
    cut = max(0, max_chars - len(ellipsis))
    return text[:cut] + ellipsis


def fit_context_window(
    pieces: list[str],
    max_tokens: int,
    model_name: str = "gpt-4o",
) -> list[str]:
    """
    Greedily pack *pieces* into a list that fits within *max_tokens*.

    Iterates through *pieces* in order, keeping each piece only if it
    fits the remaining budget.  Returns the kept pieces (possibly
    with the last one truncated).

    Parameters
    ----------
    pieces : list[str]
        Text segments to pack (e.g. search results, page chunks).
    max_tokens : int
        Total token budget.
    model_name : str
        Target model.

    Returns
    -------
    list[str]
        Subset of pieces that fit, last item possibly truncated.
    """
    if not pieces:
        return []

    encoding_name = resolve_encoding(model_name)
    encoder = _get_encoder(encoding_name)

    result: list[str] = []
    remaining = max_tokens

    for i, piece in enumerate(pieces):
        if not piece:
            continue

        if encoder is not None:
            piece_tokens = len(encoder.encode(piece))
        else:
            piece_tokens = max(1, len(piece) // 4)

        if piece_tokens <= remaining:
            result.append(piece)
            remaining -= piece_tokens
        elif remaining > 10:
            # Truncate this piece to fit the remainder
            result.append(
                truncate_to_tokens(piece, remaining, model_name)
            )
            remaining = 0
            break
        else:
            # Not enough room even for a fragment
            break

    return result


def estimate_markdown_tokens(markdown: str, model_name: str = "gpt-4o") -> int:
    """
    Convenience wrapper: count tokens in a Markdown string.

    Markdown syntax characters (``#``, ``*``, ``[``) add a small
    overhead vs. plain text; this function accounts for that by
    counting the literal Markdown tokens.
    """
    return count_tokens(markdown, model_name)
