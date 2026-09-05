# Security Policy

`gossamer` fetches URLs that are frequently **LLM-supplied or
web-derived**, and ships two security-sensitive subsystems:

- the **SSRF guard** (`ssrf.py` + Rust re-check on redirects) — blocks
  private/internal ranges, unresolvable hosts, and `.local`-style suffixes;
- the optional **prompt-injection guard** (`[guard]` extra, JailGuard-based,
  fail-open by design).

A bug in either is a security issue for every agent built on this package,
so please report it even if you think it is minor.

## Reporting a vulnerability

**Prefer GitHub's private vulnerability reporting** (repo → *Security &
privacy* → *Private vulnerability reports* → *Report a vulnerability*).
This keeps the flaw out of the public issue tracker until it is fixed.

If private reporting is unavailable, open a **regular issue labelled
`security`** and keep it as short as possible: what subsystem, which
input, what you expected the guard to do. Please do **not** attach
screenshots of real deployments or credentials.

## What counts as a vulnerability here

- SSRF guard bypass (reaching private/internal network addresses through
  any fetch path: page, batch, document download, sitemap probe, revalidation)
- Cache poisoning (one URL's content served under another URL's cache key)
- Prompt-injection guard bypass that would mislead a fail-closed
  configuration, or a denial of service in the guard
- Arbitrary code execution via crafted input to any parser
- Supply-chain: a dependency pulling in compromised code (we run
  `pip-audit` + `cargo audit` in CI, but we'd still like to hear about it)

## Response

- Acknowledgement: within 2 business days.
- We fix on `dev`, verify with a regression test named after the report,
  and ship in the next version bump.
- Credit in the release notes unless you ask for anonymity.

## Scope notes (what is *not* a vulnerability)

- The injection guard is **opt-in and fail-open by design** (`[guard]`
  extra, `enabled=False` default). Absence of detection when the guard is
  disabled or unavailable is intended behaviour, not a bug.
- The documented DNS-rebind TOCTOU limitation of the SSRF guard (resolved
  address validated, fetch re-resolves) is a known, accepted residual risk
  for this project's threat model; reports that only re-derive it without a
  working bypass will be closed as wontfix.
- Robustness of *third-party* sites against aggressive crawling is out of
  scope; the toolbox is rate-limited and robots-compliant by default.
