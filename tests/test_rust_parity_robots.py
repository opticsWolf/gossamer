"""Parity: robots parsing/matching (v0.8.8) vs ``src/robots.rs``.

Covers path extraction (incl. empty path, query, fragments,
schemeless), rule compilation (wildcards, anchors, escaping),
group selection (exact/substring/star, ties, implicit groups,
delay fallback), and longest-match-wins evaluation — over
hand-picked files plus a seeded fuzzer. The stateful
`RobotsChecker` (cache/TTL/threading/fetch) stays Python.
"""

import random
import re

import pytest

from gossamer import _core


# ── vendored originals (v0.8.8) ──────────────────────────────────

from urllib.parse import urlsplit


def _v_url_path(url):
    parts = urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return path


def _v_path_regex(path):
    anchored = path.endswith("$")
    if anchored:
        path = path[:-1]
    pattern = "".join(".*" if ch == "*" else re.escape(ch) for ch in path)
    if anchored:
        return re.compile(f"^{pattern}$")
    return re.compile(f"^{pattern}")


def _v_parse_robots(text, user_agent):
    groups = []
    current = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        directive, _, value = line.partition(":")
        directive = directive.strip().lower()
        value = value.strip()
        if directive == "user-agent":
            current = {"agents": [value], "rules": [], "crawl_delay": None}
            groups.append(current)
            continue
        if current is None:
            current = {"agents": ["*"], "rules": [], "crawl_delay": None}
            groups.append(current)
        if directive in ("disallow", "allow"):
            if value:
                current["rules"].append((directive == "allow", value))
        elif directive in ("crawl-delay", "crawl delay", "crawler-delay"):
            try:
                current["crawl_delay"] = max(0.0, float(value))
            except ValueError:
                pass
    if not groups:
        return [], None
    ua = user_agent.lower()

    def _score(group):
        for agent in group["agents"]:
            a = agent.strip().lower()
            if a == ua:
                return 3
            if a in ua:
                return 2
        return 0

    best_score = 0
    chosen = None
    for group in groups:
        score = _score(group)
        if score > best_score:
            best_score, chosen = score, group
    if chosen is None:
        for group in groups:
            if any(a.strip().lower() == "*" for a in group["agents"]):
                chosen = group
                break
    if chosen is None:
        return [], None
    delay = chosen["crawl_delay"]
    if delay is None:
        for group in groups:
            if (group["crawl_delay"] is not None
                    and any(a.strip().lower() == "*" for a in group["agents"])):
                delay = group["crawl_delay"]
                break
    return chosen["rules"], delay


def _v_match(rules, path):
    best = None
    for allow, rule_path in rules:
        rx = _v_path_regex(rule_path)
        if rx.match(path):
            if best is None:
                best = (rule_path, allow)
            elif len(rule_path) > len(best[0]):
                best = (rule_path, allow)
            elif len(rule_path) == len(best[0]) and allow and not best[1]:
                best = (rule_path, allow)
    return True if best is None else best[1]


# ── comparisons ──────────────────────────────────────────────────

URLS = [
    "https://example.com/a?x=1#f", "https://example.com/",
    "https://example.com", "http://h:8080", "https://h/p?",
    "notaurl", "notaurl?a=b#c", "/rel/path?q=1",
    "https://example.com/ü?q=ü#f", "HTTPS://H.X/A?B=C",
    "https://user@h/a", "http://h/a;b", "https://h//double//",
]


@pytest.mark.parametrize("url", URLS)
def test_path_parity(url):
    assert _core.robots_url_path(url) == _v_url_path(url)


FILES = [
    "",
    "# only a comment\n",
    "User-agent: *\nDisallow: /private\n",
    "User-agent: *\nDisallow:\n",
    "Disallow: /implicit\n",
    "User-agent: bot\nDisallow: /a\nUser-agent: *\nDisallow: /b\n",
    "User-agent: *\nDisallow: /tmp\nAllow: /tmp/public\n",
    "User-agent: *\nDisallow: /*.pdf$\nAllow: /docs/*.pdf\n",
    "User-agent: *\nCrawl-delay: 5\nDisallow: /x\n",
    "User-agent: *\nCrawl-delay: abc\nCrawl-delay: 2.5\n",
    "User-agent: *\nCrawl delay: 3\nCrawler-delay: 4\n",
    "User-agent: *\nDisallow: /a\nCrawl-delay: 7\nUser-agent: other\nDisallow: /b\n",
    "USER-AGENT: BOT\n  DISALLOW:   /Caps  \n",
    "User-agent: *\nDisallow: /a?b=c&d=e\n",
    "garbage without colon\nUser-agent: *\n:empty-directive\nDisallow /no-colon\n",
    "User-agent: mybot\nUser-agent: *\nDisallow: /m\n",
    "User-agent: *\nDisallow: /ü?q=ü\n",
    "User-agent:\nDisallow: /empty-ua\n",
    "line1\r\nUser-agent: *\r\nDisallow: /crlf\r\n",
    "User-agent: *\nDisallow: /a\n\n\n# gap\nAllow: /a/b\n",
]

UAS = ["gossamer/1.0", "bot", "mybot/2.0", "MYBOT", "*", "other", ""]


@pytest.mark.parametrize("text", FILES)
@pytest.mark.parametrize("ua", UAS)
def test_parse_parity(text, ua):
    want_rules, want_delay = _v_parse_robots(text, ua)
    got_rules, got_delay = _core.robots_parse(text, ua)
    assert [(a, p) for a, p in got_rules] == [(a, p) for a, p in want_rules]
    assert got_delay == want_delay


PATHS = ["/", "/a", "/a/", "/private", "/private/x", "/tmp", "/tmp/public",
         "/tmp/public/x", "/docs/f.pdf", "/docs/f.pdf/x", "/x.pdf",
         "/Caps", "/caps", "/a?b=c&d=e", "/a?b=X", "/ü?q=ü", "/crlf",
         "/m", "/b", "/implicit", "/empty-ua", "/other"]


@pytest.mark.parametrize("text", FILES)
@pytest.mark.parametrize("path", PATHS)
def test_match_parity(text, path):
    rules, _ = _v_parse_robots(text, "mybot/2.0")
    assert _core.robots_match_url(rules, path) == _v_match(rules, path)


def test_fuzz_robots_parity():
    rng = random.Random(20260905)
    uas = ["bot", "mybot", "crawler", "*", "gossamer-test"]
    directives = ["User-agent", "Disallow", "Allow", "Crawl-delay",
                  "Sitemap", "Bogus-Directive", "user-AGENT"]
    values = ["*", "bot", "/a", "/a/b?c=d", "/*.pdf$", "/tmp/*", "",
              "3", "abc", "2.5", "  /spaced  ", "#c", "/ü", "/a$b", "0"]
    paths = ["/", "/a", "/a/b?c=d", "/x.pdf", "/tmp/x", "/ü", "/a$b",
             "/spaced", "/other/deep?q=1#f"]
    for _ in range(120):
        lines = []
        for _ in range(rng.randint(0, 8)):
            d = rng.choice(directives)
            v = rng.choice(values)
            sep = rng.choice([":", ":", " : ", ":  "])
            lines.append(f"{d}{sep}{v}")
            if rng.random() < 0.15:
                lines.append(rng.choice(["", "# comment", "garbage"]))
        text = "\n".join(lines)
        if rng.random() < 0.2:
            text = text.replace("\n", "\r\n")
        ua = rng.choice(uas)
        want_rules, want_delay = _v_parse_robots(text, ua)
        got_rules, got_delay = _core.robots_parse(text, ua)
        assert [(a, p) for a, p in got_rules] == [(a, p) for a, p in want_rules], text
        assert got_delay == want_delay, text
        for path in rng.sample(paths, 3):
            assert _core.robots_match_url(want_rules, path) == _v_match(
                want_rules, path), (text, path)
        for url in rng.sample(
            ["https://h" + p for p in paths] + ["notaurl?a=b"], 2
        ):
            assert _core.robots_url_path(url) == _v_url_path(url), url
