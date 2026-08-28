"""Prompt-injection annotation layer (review item 7).

A pluggable :class:`Guard` protocol with a default implementation backed by
the optional ``jailguard`` detector (``pip install stitch-web-researcher[guard]``).
Off by default: when disabled there is zero import, zero latency, and zero
payload change. When enabled, the guard scans the configured output scopes for
prompt-injection-like text, in **chunks** (the detector truncates input to
~256 tokens, so a whole page is never seen at once), and attaches an additive
``guard`` block to the result.

Default mode is ``annotate``: content passes through untouched and the
consuming model is told, via the ``guard`` block plus an explicit
untrusted-content marker, that the content is untrusted. ``redact`` replaces
flagged chunks with a placeholder; ``block`` withholds the content (opt-in,
high threshold).
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Scope names: which output fields may be scanned.
KNOWN_SCOPES = frozenset(
    {
        "page_markdown",      # markdown returned by inspect_html_page
        "page_metadata",      # title/description/og:* / json-ld values
        "follow_up_titles",   # anchor texts offered to the model
        "document_text",      # extract_document / _structured output
        "search_results",     # provider titles + snippets
    }
)
_SCOPE_SHORTHANDS = {
    "all": frozenset(KNOWN_SCOPES),
    "none": frozenset(),
    "off": frozenset(),
}
_DEFAULT_SCOPES = frozenset({"page_markdown", "document_text"})
_VALID_MODES = ("annotate", "redact", "block")
_UNTRUSTED_OPEN = '<untrusted-web-content source="{url}">'
_UNTRUSTED_CLOSE = "</untrusted-web-content>"


def _normalize_scopes(scopes) -> frozenset:
    """Accept a frozenset, a set/list, or a comma string; validate it.

    Shorthands ``all`` / ``none`` / ``off`` expand or clear the set. Unknown
    scope names raise (fail fast at config time, not mid-call).
    """
    if scopes is None:
        return set(_DEFAULT_SCOPES)
    if isinstance(scopes, str):
        scopes = [s for s in (p.strip() for p in scopes.split(",")) if s]
    out = set()
    for item in scopes:
        key = str(item).strip().lower()
        if not key:
            continue
        if key in _SCOPE_SHORTHANDS:
            out |= set(_SCOPE_SHORTHANDS[key])
        elif key in KNOWN_SCOPES:
            out.add(key)
        else:
            raise ValueError(
                f"unknown guard scope: {item!r} "
                f"(valid: {sorted(KNOWN_SCOPES) + ['all', 'none']})"
            )
    return frozenset(out)


@dataclass
class GuardConfig:
    """Tunable for the guard layer. Off by default."""

    # -- on/off ------------------------------------------------
    enabled: bool = False  # default off: zero import, zero latency

    # -- what gets checked -------------------------------------
    scopes: frozenset = _DEFAULT_SCOPES
    #   page_markdown / page_metadata / follow_up_titles /
    #   document_text / search_results, or the "all"/"none" shorthands.

    # -- behavior ----------------------------------------------
    mode: str = "annotate"  # annotate | redact | block
    threshold: float = 0.7  # score at/above which a chunk is flagged
    fail_open: bool = True  # detector error => pass through + log

    # -- cost control ------------------------------------------
    chunk_chars: int = 900  # ~256 tokens, the detector's real limit
    chunk_overlap: int = 120  # so a payload cannot hide on a seam
    max_chunks: int = 40  # hard latency ceiling per call
    cache_verdicts: bool = True  # keyed by sha256(chunk); hits rescan nothing
    timing: bool = True  # feeds the measurement hooks in get_stats()

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(
                f"guard mode must be one of {list(_VALID_MODES)}, got {self.mode!r}"
            )
        if not (0.0 < self.threshold <= 1.0):
            raise ValueError(
                f"guard threshold must be in (0, 1], got {self.threshold!r}"
            )
        if self.chunk_chars <= 0:
            raise ValueError("guard chunk_chars must be > 0")
        if self.chunk_overlap < 0 or self.chunk_overlap >= self.chunk_chars:
            raise ValueError("guard chunk_overlap must be in [0, chunk_chars)")
        if self.max_chunks <= 0:
            raise ValueError("guard max_chunks must be > 0")
        self.scopes = _normalize_scopes(self.scopes)


@dataclass
class GuardFlag:
    """One flagged chunk: where it was, how risky, and a short excerpt."""

    scope: str
    offset: int
    length: int
    score: float
    risk: str
    excerpt: str

    def to_dict(self) -> dict:
        return {
            "scope": self.scope,
            "offset": self.offset,
            "score": round(self.score, 4),
            "risk": self.risk,
            "excerpt": self.excerpt,
        }


@dataclass
class GuardReport:
    """Result of scanning a single scope's text."""

    scanned: bool
    scopes: list
    max_score: float
    risk: str
    flagged: list
    chunks_scanned: int
    elapsed_ms: float
    action: str

    def to_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "scopes": list(self.scopes),
            "max_score": round(self.max_score, 4),
            "risk": self.risk,
            "flagged": [f.to_dict() for f in self.flagged],
            "chunks_scanned": self.chunks_scanned,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "action": self.action,
        }


@dataclass
class GuardStats:
    """Cumulative counters feeding the get_stats() ``guard`` section.

    Sample retention is bounded (M7) so a long-lived process cannot grow
    without limit while still giving stable p50/p95 estimates.
    """

    enabled: bool
    calls: int = 0
    chunks_scanned: int = 0
    total_ms: float = 0.0
    flagged: int = 0
    cache_hits: int = 0
    model_load_ms: float = 0.0
    _call_ms: list = field(default_factory=list, repr=False)

    def record(self, elapsed_ms: float, chunks: int, n_flagged: int, cache_hits: int) -> None:
        self.calls += 1
        self.chunks_scanned += chunks
        self.total_ms += elapsed_ms
        self.flagged += n_flagged
        self.cache_hits += cache_hits
        if self.calls <= 2000:
            self._call_ms.append(elapsed_ms)

    def _percentile(self, q: float) -> float:
        if not self._call_ms:
            return 0.0
        ordered = sorted(self._call_ms)
        idx = min(len(ordered) - 1, int(round(q / 100.0 * (len(ordered) - 1))))
        return ordered[idx]

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "calls": self.calls,
            "chunks_scanned": self.chunks_scanned,
            "total_ms": round(self.total_ms, 1),
            "p50_ms": round(self._percentile(50), 1),
            "p95_ms": round(self._percentile(95), 1),
            "flagged": self.flagged,
            "flag_rate": round(self.flagged / self.calls, 4) if self.calls else 0.0,
            "cache_hits": self.cache_hits,
            "model_load_ms": round(self.model_load_ms, 1),
        }


def chunk_text(text: str, chunk_chars: int, overlap: int, max_chunks: int) -> list:
    """Split *text* into overlapping windows the detector can actually see.

    Returns a list of ``(start_offset, chunk)``. The first window starts at 0;
    each subsequent one starts ``chunk_chars - overlap`` characters later so a
    payload straddling a seam lands in two windows. At most ``max_chunks``
    windows are returned (a hard latency ceiling) — text beyond that is not
    scanned.
    """
    if not text:
        return []
    step = max(1, chunk_chars - overlap)
    chunks = []
    start = 0
    n = len(text)
    while start < n and len(chunks) < max_chunks:
        window = text[start : start + chunk_chars]
        chunks.append((start, window))
        if start + chunk_chars >= n:
            break
        start += step
    return chunks


@runtime_checkable
class Guard(Protocol):
    """A prompt-injection scanner.

    ``scan`` inspects one scope's text and returns a report; ``stats`` returns
    cumulative counters. A future ensemble or regex prefilter can implement
    this without touching call sites.
    """

    def scan(self, scope: str, text: str) -> GuardReport:  # pragma: no cover
        ...

    @property
    def stats(self) -> GuardStats:  # pragma: no cover
        ...


class NoopGuard:
    """Disabled guard: no imports, no scanning, no cost."""

    def __init__(self, config: Optional[GuardConfig] = None) -> None:
        self._config = config or GuardConfig()
        self._stats = GuardStats(enabled=False)

    def scan(self, scope: str, text: str) -> GuardReport:
        return GuardReport(
            scanned=False,
            scopes=[],
            max_score=0.0,
            risk="None",
            flagged=[],
            chunks_scanned=0,
            elapsed_ms=0.0,
            action="disabled",
        )

    @property
    def stats(self) -> GuardStats:
        return self._stats


class JailGuardGuard:
    """Guard implementation backed by the optional ``jailguard`` detector.

    The import is lazy and the model is downloaded once (``download_model``),
    so enabling costs nothing until the first scan. On any detector error,
    ``fail_open`` (the default) lets content pass with a logged note rather
    than failing the call.
    """

    def __init__(self, config: GuardConfig) -> None:
        self._config = config
        self._stats = GuardStats(enabled=True)
        self._verdicts: dict = {}  # sha256(chunk) -> (score, risk, injected)
        self._jg = None
        self._load_error: Optional[str] = None

    def _ensure(self) -> None:
        """Import jailguard and download the model once (fail-open on error)."""
        if self._jg is not None or self._load_error is not None:
            return
        try:
            import jailguard as jg  # type: ignore[import-untyped]
        except ImportError as exc:
            self._load_error = (
                f"jailguard not importable ({exc}); install "
                "stitch-web-researcher[guard]"
            )
            logger.warning("guard disabled at runtime: %s", self._load_error)
            return
        t0 = time.perf_counter()
        try:
            jg.download_model()
        except Exception as exc:  # noqa: BLE001 - fail-open by design
            self._load_error = f"jailguard model unavailable: {exc}"
            logger.warning("guard disabled at runtime: %s", self._load_error)
            return
        self._stats.model_load_ms = (time.perf_counter() - t0) * 1000.0
        self._jg = jg

    def preload(self) -> None:
        """Download/load the model now (construction time), not on first scan."""
        self._ensure()

    @staticmethod
    def _field(res, name: str, default):
        val = getattr(res, name, None)
        if val is None and isinstance(res, dict):
            val = res.get(name, default)
        return default if val is None else val

    @staticmethod
    def _risk_name(val) -> str:
        """Render a detector risk level as a bare word.

        ``jailguard``'s ``.risk`` is an enum, and ``str()`` of a stdlib
        ``Enum`` renders as ``"RiskLevel.High"`` -- an implementation
        detail of the detector leaking into a field the consuming model
        reads. Prefer ``.name``/``.value``, and otherwise strip a
        ``ClassName.`` prefix from the string form.
        """
        for attr in ("name", "value"):
            inner = getattr(val, attr, None)
            if isinstance(inner, str) and inner:
                return inner
        text = str(val)
        head, sep, tail = text.rpartition(".")
        # Only strip a real "Class.MEMBER" form: a dotted numeric score or
        # a sentence must survive untouched.
        if sep and head.isidentifier() and tail.isidentifier():
            return tail
        return text

    def _score_chunk(self, chunk: str):
        """Return (score, risk, injected, cache_hit) for one chunk."""
        key = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        if self._config.cache_verdicts and key in self._verdicts:
            score, risk, injected = self._verdicts[key]
            return score, risk, injected, True
        self._ensure()
        if self._jg is None:  # fail-open: no detector available
            return 0.0, "None", False, False
        res = self._jg.detect(chunk)
        score = float(self._field(res, "score", 0.0))
        risk = self._risk_name(self._field(res, "risk", "None"))
        injected = bool(self._field(res, "is_injection", score >= self._config.threshold))
        if self._config.cache_verdicts:
            self._verdicts[key] = (score, risk, injected)
        return score, risk, injected, False

    def scan(self, scope: str, text: str) -> GuardReport:
        cfg = self._config
        t0 = time.perf_counter()
        if not text:
            return GuardReport(
                scanned=False, scopes=[scope], max_score=0.0, risk="None",
                flagged=[], chunks_scanned=0, elapsed_ms=0.0, action=cfg.mode,
            )
        flagged = []
        max_score = 0.0
        max_risk = "None"
        cache_hits = 0
        chunks = chunk_text(text, cfg.chunk_chars, cfg.chunk_overlap, cfg.max_chunks)
        for start, window in chunks:
            score, risk, _injected, hit = self._score_chunk(window)
            if hit:
                cache_hits += 1
            if score > max_score:
                max_score = score
                max_risk = risk
            if score >= cfg.threshold:
                flagged.append(
                    GuardFlag(
                        scope=scope,
                        offset=start,
                        length=len(window),
                        score=score,
                        risk=risk,
                        excerpt=window[:160].replace("\n", " ").strip(),
                    )
                )
        elapsed = (time.perf_counter() - t0) * 1000.0
        self._stats.record(elapsed, len(chunks), len(flagged), cache_hits)
        return GuardReport(
            scanned=True,
            scopes=[scope],
            max_score=max_score,
            risk=max_risk,
            flagged=flagged,
            chunks_scanned=len(chunks),
            elapsed_ms=elapsed,
            action=cfg.mode,
        )

    @property
    def stats(self) -> GuardStats:
        return self._stats


def build_guard(config: Optional[GuardConfig]) -> Guard:
    """Return a Guard for *config*.

    ``None`` or ``enabled=False`` gives a :class:`NoopGuard` (zero cost);
    enabled gives a :class:`JailGuardGuard`.
    """
    if config is None or not config.enabled:
        return NoopGuard(config)
    return JailGuardGuard(config)


def _guard_active(guard: Guard) -> bool:
    try:
        return bool(guard.stats.enabled)
    except AttributeError:
        return False


def merge_reports(reports: list) -> dict:
    """Merge per-scope :class:`GuardReport`s into one additive ``guard`` block.

    ``risk`` is the risk of the highest-scoring chunk (avoids needing an
    ordering over the detector's risk vocabulary).
    """
    flagged = []
    scopes = []
    max_score = 0.0
    risk = "None"
    for rep in reports:
        for flag in rep.flagged:
            flagged.append(flag)
        for s in rep.scopes:
            if s not in scopes:
                scopes.append(s)
        if rep.max_score > max_score:
            max_score = rep.max_score
            risk = rep.risk
    flagged.sort(key=lambda f: f.offset)
    return {
        "scanned": True,
        "scopes": scopes,
        "max_score": round(max_score, 4),
        "risk": risk,
        "flagged": [f.to_dict() for f in flagged],
        "chunks_scanned": sum(r.chunks_scanned for r in reports),
        "elapsed_ms": round(sum(r.elapsed_ms for r in reports), 1),
        "action": reports[0].action if reports else "annotate",
    }


def _redact_spans(text: str, flagged: list) -> str:
    """Replace each flagged span with a redaction placeholder (deduped)."""
    spans = []
    for flag in flagged:
        end = min(len(text), flag.offset + flag.length)
        if flag.offset < end:
            spans.append((flag.offset, end, flag.score))
    if not spans:
        return text
    spans.sort()
    out = []
    last = 0
    for start, end, score in spans:
        if start < last:  # overlapping span: already covered
            continue
        out.append(text[last:start])
        out.append(f"[redacted: possible prompt injection - score {score:.2f}]")
        last = end
    out.append(text[last:])
    return "".join(out)


def evaluate(
    guard: Guard,
    scope_texts: list,
    main_scope: str,
) -> tuple:
    """Run *guard* over the enabled ``(scope, text)`` pairs.

    Returns ``(guard_block, transformed_main, withheld)``:
      guard_block: the additive ``guard`` dict, or None when nothing scanned.
      transformed_main: redacted main-scope text, or None when unchanged.
      withheld: True when mode=block flagged the content (caller withholds).
    Only the main scope's text is eligible for redact/block transformation.
    """
    if not _guard_active(guard):
        return None, None, False
    cfg = getattr(guard, "_config", None)
    if cfg is None:
        return None, None, False
    mode = cfg.mode
    reports = []
    main_text = None
    for scope, text in scope_texts:
        if scope not in cfg.scopes:
            continue
        if not text:
            continue
        reports.append(guard.scan(scope, text))
        if scope == main_scope:
            main_text = text
    scanned = [r for r in reports if r.scanned]
    if not scanned:
        return None, None, False
    block = merge_reports(scanned)
    if mode == "block" and block["flagged"]:
        block["withheld"] = True
        return block, None, True
    if mode == "redact" and block["flagged"]:
        main_report = next((r for r in scanned if main_scope in r.scopes), None)
        if main_report is not None and main_text is not None:
            block["withheld"] = False
            return block, _redact_spans(main_text, main_report.flagged), False
    return block, None, False


def wrap_untrusted(markdown: str, source_url: str) -> str:
    """Wrap delivered content in an explicit untrusted-content marker.

    This is the framing layer: it tells the consuming model the content is
    fetched web data (not instructions) and names the source. Only applied
    when the guard is enabled, so default (off) output is byte-identical.
    """
    return (
        _UNTRUSTED_OPEN.format(url=source_url)
        + "\n"
        + markdown
        + "\n"
        + _UNTRUSTED_CLOSE
    )
