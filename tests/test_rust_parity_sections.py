"""Parity: vendored pure-Python ``sections`` (v0.8.2) vs ``src/sections.rs``.

Covers heading splitting (ATX + Setext incl. the lookahead guards),
tokenization (ASCII/CJK/stopwords), BM25 scores (exact float equality —
same op order in f64; any libm ``log`` ulp would fail loudly here), and
end-to-end selection outcomes, over hand-picked cases plus a seeded
markdown fuzzer built to stress the guard rails (list items, rules,
table separators, exotic whitespace, CJK).
"""

import math
import random
import re
from dataclasses import dataclass

import pytest

from gossamer import _core


# ── vendored originals (v0.8.2) ──────────────────────────────────

_V_HEADING_RE = re.compile(
    r"^\#{1,6}[ \t]+(?P<atx>.+?)(?:[ \t]+\#+)?[ \t]*$"
    r"|"
    r"^(?P<setext>(?![ \t]*$)(?![ \t]*[-=]+[ \t]*$)(?![-*+][ \t])[^\n]+?)"
    r"[ \t]*\n[ \t]*(?:=|-){2,}[ \t]*$",
    re.MULTILINE,
)
_V_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")
_V_CJK_RUN_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]+")
_V_STOPWORDS = frozenset(
    """
    a an and are as at be been but by can could did do does for from had
    has have he her his i if in into is it its just me my no not of on
    one only or our she so that the their them then these they this to
    too was we were what when which who will with you your
    """.split()
)


@dataclass(frozen=True)
class _VSection:
    anchor: str
    text: str
    offset: int


@dataclass(frozen=True)
class _VSelection:
    markdown: str
    total_sections: int
    selected_count: int
    anchors: tuple


def _v_split_sections(markdown):
    if not markdown or not markdown.strip():
        return []
    matches = list(_V_HEADING_RE.finditer(markdown))
    if not matches:
        return [_VSection("(intro)", markdown, 0)]
    sections = []
    preamble_end = matches[0].start()
    if markdown[:preamble_end].strip():
        sections.append(_VSection("(intro)", markdown[:preamble_end], 0))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        title = m.group("atx") or m.group("setext") or ""
        sections.append(_VSection(title.strip(), markdown[m.start():end], m.start()))
    return sections


def _v_tokenize_text(text):
    text = text.lower()
    tokens = [
        t for t in _V_ASCII_TOKEN_RE.findall(text) if len(t) > 1 and t not in _V_STOPWORDS
    ]
    for run in _V_CJK_RUN_RE.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def _v_bm25_scores(query_tokens, docs, k1=1.5, b=0.75):
    n = len(docs)
    if n == 0 or not query_tokens:
        return [0.0] * n
    doc_tokens = [_v_tokenize_text(d) for d in docs]
    avgdl = sum(len(d) for d in doc_tokens) / n or 1.0
    df = {}
    for d in doc_tokens:
        for term in set(d):
            df[term] = df.get(term, 0) + 1
    query_terms = [t for t in dict.fromkeys(query_tokens) if t in df]
    if not query_terms:
        return [0.0] * n
    scores = []
    for d in doc_tokens:
        tf = {}
        for term in d:
            tf[term] = tf.get(term, 0) + 1
        score = 0.0
        for q in query_terms:
            f = tf.get(q, 0)
            if f == 0:
                continue
            n_q = df[q]
            idf = math.log(1.0 + (n - n_q + 0.5) / (n_q + 0.5))
            score += idf * (f * (k1 + 1)) / (f + k1 * (1.0 - b + b * len(d) / avgdl))
        scores.append(max(score, 0.0))
    return scores


def _v_select(markdown, query, max_chars):
    if max_chars <= 0 or not markdown:
        return None
    if len(markdown) <= max_chars:
        return None
    query_tokens = _v_tokenize_text(query)
    if not query_tokens:
        return None
    sections = _v_split_sections(markdown)
    if len(sections) <= 1:
        return None
    scores = _v_bm25_scores(query_tokens, [s.text for s in sections])
    if max(scores) <= 0.0:
        return None
    order = sorted(range(len(sections)), key=lambda i: (-scores[i], i))
    picked = []
    remaining = max_chars
    for i in order:
        if scores[i] <= 0.0:
            break
        text = sections[i].text
        if len(text) <= remaining:
            picked.append((i, text))
            remaining -= len(text)
        elif remaining > 0:
            picked.append((i, text[:remaining]))
            remaining = 0
            break
    if not picked:
        return None
    picked.sort(key=lambda t: t[0])
    selected = "\n\n".join(text.strip() for _, text in picked).strip()
    if not selected:
        return None
    return _VSelection(
        markdown=selected,
        total_sections=len(sections),
        selected_count=len(picked),
        anchors=tuple(sections[i].anchor for i, _ in picked),
    )


# ── comparisons ──────────────────────────────────────────────────

DOCS = [
    "",
    "   \n  ",
    "just text\n",
    "intro line\n\n# Alpha\n\nbody A\n\n## Beta\nbody B",
    "Title\n=====\n\nbody\n\nSub\n---\n\nmore\n",
    "# A #\n\n## B ## extra\n\n###C###\n",
    "- item\n---\n\n# Real\n\ntext\n",
    "---\n\nfoo\n===\n",
    "| a | b |\n|---|---|\n| 1 | 2 |\n\n# T\n\nx\n",
    "> # not a heading\n\n# Yes\n\nbody\n",
    "# Ünïcödé\n\ntête 中文テスト\n\n## 日本語\n\n本文です\n",
    "pre\x1camble\n\n# H\n\nbody\x85tail\n",
    "# One\n\napple apple\n\n# Two\n\nquantum lattice\n",
    "### Deep\n\n" + "filler text here. " * 50 + "\n\n### Deeper\n\nquantum stuff\n",
]


@pytest.mark.parametrize("md", DOCS)
def test_split_parity(md):
    want = [(s.anchor, s.text, s.offset) for s in _v_split_sections(md)]
    got = [(s.anchor, s.text, s.offset) for s in _core.split_sections(md)]
    assert got == want


@pytest.mark.parametrize(
    "text",
    ["The Quick Brown Fox", "a b c xray", "日本語テスト", "UPPER lower MiXeD",
     "don't stop believin'", "", "123 45 6", "it's café naïve"],
)
def test_tokenize_parity(text):
    assert _core.tokenize_text(text) == _v_tokenize_text(text)


@pytest.mark.parametrize("md", DOCS[3:9])
def test_bm25_exact_float_parity(md):
    docs = [s.text for s in _v_split_sections(md)] or [md]
    for query in ["quantum lattice", "body text here", "zzz-nope", "the"]:
        want = _v_bm25_scores(_v_tokenize_text(query), docs)
        got = _core.bm25_scores(_core.tokenize_text(query), docs)
        assert got == want, f"float divergence for {query!r}:\n{want}\n{got}"


QUERIES = ["quantum lattice", "body", "zzz-nope", "the and of", "",
           "日本語", "filler", "Alpha Beta"]


@pytest.mark.parametrize("md", DOCS)
@pytest.mark.parametrize("query", QUERIES)
@pytest.mark.parametrize("budget", [10, 60, 400, 100000])
def test_select_parity(md, query, budget):
    want = _v_select(md, query, budget)
    got = _core.select_relevant_sections(md, query, budget)
    if want is None:
        assert got is None, f"expected None for {query!r}@{budget}"
    else:
        assert got is not None, f"expected selection for {query!r}@{budget}"
        assert got.markdown == want.markdown
        assert got.total_sections == want.total_sections
        assert got.selected_count == want.selected_count
        assert tuple(got.anchors) == want.anchors


def _fuzz_markdown(rng):
    titles = ["Alpha", "Beta Gamma", "日本語タイトル", "Ünïcödé", "- item",
              "---", "===", "", "  spaced  ", "a" * 120, "x"]
    paras = ["filler text here. ", "quantum lattice calibration. ",
             "日本語の本文です。", "Body & soul <tags>. ", "a b c. ",
             " democrat.*+? special | pipes | here ", "line1\nline2\n"]
    parts = []
    if rng.random() < 0.4:
        parts.append(rng.choice(paras) * rng.randint(0, 3))
    for _ in range(rng.randint(0, 5)):
        style = rng.random()
        title = rng.choice(titles)
        if style < 0.5:
            level = "#" * rng.randint(1, 6)
            close = (" " + level) if rng.random() < 0.3 else ""
            parts.append(f"{level} {title}{close}\n")
        elif style < 0.75:
            under = "=" * rng.randint(2, 6) if rng.random() < 0.5 else "-" * rng.randint(2, 6)
            parts.append(f"{title}\n{under}\n")
        else:
            parts.append("| a | b |\n|---|---|\n")
        parts.append(rng.choice(paras) * rng.randint(0, 4) + "\n")
    text = "\n".join(parts)
    if rng.random() < 0.2:
        text += rng.choice(["\x1c", "\x85", " "])
    return text


def test_fuzz_sections_parity():
    rng = random.Random(20260905)
    queries = ["quantum", "filler text", "zzz", "日本語", "the", "Alpha",
               "a", "", "body soul"]
    for _ in range(150):
        md = _fuzz_markdown(rng)
        want_split = [(s.anchor, s.text, s.offset) for s in _v_split_sections(md)]
        got_split = [(s.anchor, s.text, s.offset) for s in _core.split_sections(md)]
        assert got_split == want_split, f"split divergence:\n{md!r}\n{want_split}\n{got_split}"
        want_tok = _v_tokenize_text(md)
        assert _core.tokenize_text(md) == want_tok
        docs = [s.text for s in _v_split_sections(md)] or [md]
        for query in rng.sample(queries, 3):
            budget = rng.choice([10, 60, 400])
            want = _v_select(md, query, budget)
            got = _core.select_relevant_sections(md, query, budget)
            if want is None:
                assert got is None, f"select divergence (want None):\n{md!r}\n{query!r}"
            else:
                assert got is not None
                assert (got.markdown, got.total_sections, got.selected_count,
                        tuple(got.anchors)) == (
                        want.markdown, want.total_sections, want.selected_count,
                        want.anchors), f"select divergence:\n{md!r}\n{query!r}"
