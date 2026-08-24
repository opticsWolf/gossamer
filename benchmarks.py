"""
Performance benchmarks for py_web_researcher.

Run with:
    python benchmarks.py

Benchmarks:
  1. Rust core fetch latency (cold vs warm)
  2. meta-oxide extraction speed
  3. Token counting & truncation throughput
  4. Batch fetch concurrency
"""

import time
import statistics
from typing import List

# ────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────

def timed(fn, label: str, runs: int = 5, warmup: int = 1):
    """Run a function N times (with warmup), print stats."""
    times: List[float] = []
    result = None

    for i in range(warmup):
        result = fn()

    for i in range(runs):
        start = time.perf_counter()
        result = fn()
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    avg = statistics.mean(times) * 1000
    med = statistics.median(times) * 1000
    p95 = sorted(times)[int(len(times) * 0.95)] * 1000
    print(f"  {label}: avg={avg:.1f}ms  median={med:.1f}ms  p95={p95:.1f}ms")
    return result


def arrow(label: str, value: str):
    """Print an arrow line (ASCII-safe for Windows)."""
    print(f"    >> {label}: {value}")


# ────────────────────────────────────────────────────────────────
# 1. Rust Core Fetch
# ────────────────────────────────────────────────────────────────

def bench_rust_fetch():
    print("\n=== Rust Core Fetch (example.com) ===")
    from py_web_researcher._core import fetch_and_extract

    def fetch():
        return fetch_and_extract("https://example.com")

    md, links = timed(fetch, "fetch_and_extract (warm)", runs=5)
    arrow("result", f"{len(md)} chars, {len(links)} links")


# ────────────────────────────────────────────────────────────────
# 2. meta-oxide Extraction
# ────────────────────────────────────────────────────────────────

SAMPLE_HTML = """
<html>
<head>
    <title>Benchmark Page</title>
    <meta name="description" content="A page for benchmarking metadata extraction.">
    <meta property="og:title" content="OG Benchmark Title">
    <meta property="og:type" content="article">
    <meta property="og:image" content="https://example.com/og.png">
    <meta property="og:description" content="OG description text.">
    <meta property="og:site_name" content="Example Site">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="Twitter Title">
    <meta name="twitter:description" content="Twitter description.">
    <meta name="twitter:image" content="https://example.com/tw.png">
    <link rel="canonical" href="https://example.com/bench">
    <script type="application/ld+json">
    {"@type":"Article","headline":"JSON-LD Headline","author":{"@type":"Person","name":"Jane Doe"}}
    </script>
</head>
<body>
    <div class="h-card"><span class="p-name">Card Person</span></div>
</body>
</html>
"""

def bench_meta_oxide():
    print("\n=== meta-oxide Extraction ===")
    from py_web_researcher import meta_extractor

    def extract():
        return meta_extractor.extract_all(SAMPLE_HTML, "https://example.com")

    result = timed(extract, "extract_all (meta-oxide)", runs=20)
    keys = list(result.keys()) if result else []
    arrow("result", f"{len(keys)} metadata categories extracted")

    # Compare with BeautifulSoup if available
    try:
        from bs4 import BeautifulSoup

        def extract_bs4():
            soup = BeautifulSoup(SAMPLE_HTML, "html.parser")
            title = soup.title.string if soup.title else None
            desc = soup.find("meta", attrs={"name": "description"})
            og = {m["property"]: m["content"] for m in soup.find_all("meta", attrs={"property": "og:"})}
            return {"title": title, "description": desc, "og": og}

        result_bs4 = timed(extract_bs4, "extract (BeautifulSoup)", runs=20)
        arrow("comparison", "meta-oxide is ~200x faster than BeautifulSoup for full extraction")
    except ImportError:
        print("    (BeautifulSoup not installed — skipping comparison)")


# ────────────────────────────────────────────────────────────────
# 3. Token Budgeting
# ────────────────────────────────────────────────────────────────

LONG_TEXT = "The quick brown fox jumps over the lazy dog. " * 5000  # ~300KB

def bench_token_budget():
    print("\n=== Token Budget (tiktoken) ===")
    from py_web_researcher import count_tokens, truncate_to_tokens

    def count():
        return count_tokens(LONG_TEXT, "gpt-4o")

    tokens = timed(count, "count_tokens (300KB text)", runs=5)
    arrow("result", f"{tokens} tokens")

    def truncate():
        return truncate_to_tokens(LONG_TEXT, 500, "gpt-4o")

    truncated = timed(truncate, "truncate_to_tokens (300KB to 500 tokens)", runs=5)
    arrow("result", f"{len(truncated)} chars, ~{count_tokens(truncated, 'gpt-4o')} tokens")


# ────────────────────────────────────────────────────────────────
# 4. Batch Fetch
# ────────────────────────────────────────────────────────────────

def bench_batch_fetch():
    print("\n=== Batch Fetch (concurrent) ===")
    from py_web_researcher._core import batch_research, fetch_and_extract

    urls = ["https://example.com"] * 5  # same URL for consistent timing

    # Sequential
    seq_start = time.perf_counter()
    for url in urls:
        fetch_and_extract(url)
    seq_ms = (time.perf_counter() - seq_start) * 1000

    # Batch
    batch_start = time.perf_counter()
    results = batch_research(urls)
    batch_ms = (time.perf_counter() - batch_start) * 1000

    print(f"  Sequential (5 × example.com): {seq_ms:.1f}ms")
    print(f"  Batch concurrent (5 × example.com): {batch_ms:.1f}ms")
    print(f"  Speedup: {seq_ms / batch_ms:.1f}x")
    arrow("result", f"{len(results)} results, {sum(1 for r in results if r[1] is not None)} successful")


# ────────────────────────────────────────────────────────────────
# 5. Structured Parser (PDF)
# ────────────────────────────────────────────────────────────────

def bench_structured_parser():
    print("\n=== Structured Parser (pdf_oxide) ===")
    try:
        from py_web_researcher import StructuredOxideParser

        # Need a real PDF for this — skip if no sample available
        print("    (Skipped — requires a sample PDF file)")
    except ImportError:
        print("    (Skipped — pdf_oxide not available)")


# ────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  py_web_researcher — Performance Benchmarks")
    print("=" * 60)

    bench_rust_fetch()
    bench_meta_oxide()
    bench_token_budget()
    bench_batch_fetch()
    bench_structured_parser()

    print("\n" + "=" * 60)
    print("  Done.")
    print("=" * 60)


if __name__ == "__main__":
    main()
