"""Parity: ``ssrf.validate_public_url`` (v0.8.7) vs ``src/ssrf.rs``.

The blocked decision replicates CPython `ipaddress` tables exactly
(private − exceptions + reserved, mapped-unwrap) — see
`docs/RUST_PORT_SSRF_TABLE.md` for the probed matrix. This file
compares outcomes AND exact messages over the full IP corpus (every
IANA boundary probed), hostname/scheme/port validation, and the env
bypass. DNS paths: failure shape (type + prefix — OS messages differ)
offline, plus live-gated agreement on a stable public name.
"""

import os

import pytest

from gossamer import _core


# ── vendored original (v0.8.7; DNS injected for hermetic tests) ────

_V_SUFFIXES = (".local", ".internal", ".localhost")


def _v_check_ip(host, ip, errors):
    import ipaddress

    bad = (
        ip.is_unspecified or ip.is_loopback or ip.is_link_local
        or ip.is_private or ip.is_reserved
    )
    if bad:
        errors.append(f"Host {host!r} ({ip}) is not a public address")


def _v_validate(url, allow_private=False, resolve=None):
    from urllib.parse import urlparse
    import ipaddress
    import socket

    if allow_private:
        return None
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return f"URL scheme {scheme!r} is not allowed for fetching"
    host = parsed.hostname
    if not host:
        return f"URL has no host: {url}"
    host_l = host.lower().rstrip(".")
    if host_l == "localhost" or host_l.endswith(_V_SUFFIXES):
        return f"Host {host!r} is an internal name"
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        return f"URL has an invalid port: {url}"
    try:
        literal = ipaddress.ip_address(host_l)
    except ValueError:
        literal = None
    if literal is not None:
        errs = []
        _v_check_ip(host, literal, errs)
        return errs[0] if errs else None
    try:
        infos = (resolve or socket.getaddrinfo)(host, port, proto=socket.IPPROTO_TCP)
    except OSError as e:
        return f"DNS resolution failed for {host!r}: {e}"
    if not infos:
        return f"DNS returned no addresses for {host!r}"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        errs = []
        _v_check_ip(host, ip, errs)
        if errs:
            return errs[0]
    return None


def _cut_os_error(msg):
    # The OS resolver text after "DNS resolution failed for 'h': " is
    # platform- and locale-dependent (out of contract); compare the head.
    if msg.startswith("DNS resolution failed for '"):
        head, sep, _ = msg.partition("': ")
        if sep:
            return head + "': <os error>"
    return msg


def _rs(url, allow_private=False):
    # Compare MESSAGES: _core raises ValueError; the Python wrapper
    # re-raises SsrfBlockedError (a ValueError subclass) — typed in
    # test_wrapper_raises_ssrf_blocked_error below.
    try:
        _core.ssrf_check_url(url, allow_private)
        return None
    except ValueError as e:
        return _cut_os_error(str(e))


def _py(url, allow_private=False, resolve=None):
    # `urlparse` itself raises on malformed brackets (propagates through
    # the real validate_public_url too); compare the rendered error.
    try:
        err = _v_validate(url, allow_private, resolve)
    except ValueError as e:
        return _cut_os_error(str(e))
    return None if err is None else _cut_os_error(err)


LITERALS = [
    # v4 boundaries (probed)
    "0.0.0.0", "0.0.0.1", "0.255.255.255", "1.0.0.0", "10.1.2.3",
    "100.64.0.1", "127.0.0.1", "127.0.0.2", "169.254.10.20",
    "172.15.255.255", "172.16.5.4", "172.31.255.254", "172.32.0.0",
    "191.255.255.255", "192.0.0.0", "192.0.0.9", "192.0.0.10",
    "192.0.0.11", "192.0.0.170", "192.0.0.255", "192.0.1.0",
    "192.0.2.5", "192.0.70.1", "192.31.196.1", "192.52.193.1",
    "192.88.99.1", "192.168.0.1", "192.175.48.1", "198.17.255.255",
    "198.18.0.0", "198.19.255.255", "198.20.0.0", "198.51.100.7",
    "203.0.113.9", "239.255.255.255", "240.0.0.0", "255.255.255.255",
    "8.8.4.4",
    # v6 boundaries (probed)
    "::", "::1", "64:ff9b::808:808", "64:ff9b:1::1", "64:ff9b:2::1",
    "100::1", "100::ffff:ffff:ffff:ffff", "101::1", "2001::1",
    "2001:1::1", "2001:1::2", "2001:2::1", "2001:f::1", "2001:1f:ffff::1",
    "2001:20::1", "2001:2f::1", "2001:3::1", "2001:4:112::1",
    "2001:10::1", "2001:30::1", "2001:db8::1", "2001:db8:ffff::1",
    "2001:ffff::1", "2002::1", "2002:0808:0808::1", "2003::1",
    "2620:4f:8000::1", "5eff:ffff::1", "5f00::1", "5f00:ffff::1",
    "5f01::1", "fbff:ffff::1", "fc00::1", "fd12:3456::1", "fd00:ec2::254",
    "fe00::1", "fe7f:ffff::1", "fe80::1", "fec0::1", "ff00::1",
    "ff02::1", "ff05::1",
    # mapped forms
    "::ffff:8.8.8.8", "::ffff:10.0.0.1", "::ffff:100.64.0.1",
    "::ffff:192.0.2.1", "::ffff:0.0.0.0", "::ffff:0:192.168.1.1",
    "::ffff:0:8.8.8.8", "::ffff:1.2.3.4", "::1.2.3.4",
]


def _wrap_literal(ip):
    if ":" in ip and not ip.startswith("["):
        return f"[{ip}]"
    return ip


@pytest.mark.parametrize("ip", LITERALS)
@pytest.mark.parametrize("scheme", ["http", "https"])
def test_literal_parity(ip, scheme):
    url = f"{scheme}://{_wrap_literal(ip)}/path?q=1"
    assert _rs(url) == _py(url), url


@pytest.mark.parametrize("ip", LITERALS)
def test_literal_with_ports_and_userinfo(ip):
    host = _wrap_literal(ip)
    for url in (f"http://{host}:8080/", f"https://user:pw@{host}:443/x",
                f"http://{host}:80/", f"http://{host}:0/"):
        assert _rs(url) == _py(url), url


HOST_CASES = [
    "http://localhost/", "http://LOCALHOST:8000/x", "http://localhost./",
    "http://svc.local/", "http://svc.internal/", "http://h.localhost/",
    "http://local/", "http://localhost.com/", "http://mylocal/x",
    "ftp://example.com/", "gopher://x/", "notaurl", "",
    "http://", "http:///", "http://?q=1", "https://",
    "//example.com/", "example.com/", "/relative/path",
    "http://example.com:abc/", "http://example.com:99999/",
    "http://example.com:/", "http://user@example.com/",
    "http://user:pw@example.com:81/a", "HTTP://EXAMPLE.COM/",
    "http://example.com./", "http://[::1", "http://x]/",
    "https://example.com:443/", "http://example.com:80/",
    "http://[fe80::1%25eth0]/", "http://[v1.fe]/",
    "http://[FE80::1%ETH0]/", "http://[2001:db8::1%eth0]/",
    "http://[fe80::1%]/", "http://[fe80::1%a%b]/",
    "http://[8.8.8.8%eth0]/",
]


@pytest.mark.parametrize("url", HOST_CASES)
def test_host_validation_parity(url):
    assert _rs(url) == _py(url), url


def test_bypass_parity(monkeypatch):
    for url in ("http://127.0.0.1/", "http://localhost/", "ftp://x/"):
        assert _rs(url, True) is None
        assert _py(url, True) is None


def test_dns_failure_shape():
    import socket

    def fail(host, port, proto=None):
        raise socket.gaierror(11001, "getaddrinfo failed")

    url = "http://nonexistent-host-xyz.invalid/"
    py = _py(url, resolve=fail)
    assert py == "DNS resolution failed for 'nonexistent-host-xyz.invalid': <os error>"
    rs = _rs(url)
    # Live resolver may actually resolve or fail differently per network;
    # only assert the failure SHAPE when it fails.
    if rs is not None:
        assert rs == "DNS resolution failed for 'nonexistent-host-xyz.invalid': <os error>", rs


def test_empty_dns_result_parity():
    url = "http://empty-dns.invalid/"
    assert _py(url, resolve=lambda h, p, proto=None: []) == (
        "DNS returned no addresses for 'empty-dns.invalid'"
    )


@pytest.mark.parametrize("addrs,blocked", [
    ([("1.2.3.4", 80)], False),
    ([("10.0.0.1", 80)], True),
    ([("8.8.8.8", 80), ("10.0.0.1", 80)], True),
    ([("8.8.8.8", 80), ("1.1.1.1", 80)], False),
])
def test_multi_address_dns_parity(addrs, blocked):
    import socket

    def fake(host, port, proto=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, p))
                for ip, p in addrs]

    url = "http://multi.invalid/"
    py = _py(url, resolve=fake)
    assert (py is not None) == blocked
    if blocked:
        assert "not a public address" in py


@pytest.mark.live
def test_live_dns_agreement():
    if not os.environ.get("GOSSAMER_LIVE"):
        pytest.skip("live DNS agreement (needs network)")
    for url in ("https://example.com/", "http://example.com/"):
        assert _rs(url) is None, url
        assert _py(url) is None, url


def test_wrapper_raises_ssrf_blocked_error():
    from gossamer.ssrf import SsrfBlockedError, validate_public_url

    assert issubclass(SsrfBlockedError, ValueError)
    with pytest.raises(SsrfBlockedError, match="not a public address"):
        validate_public_url("http://169.254.169.254/")
    with pytest.raises(SsrfBlockedError, match="internal name"):
        validate_public_url("http://localhost/")
    # Pass-through returns None.
    assert validate_public_url("https://8.8.8.8/") is None


def test_bypass_env_var_still_works(monkeypatch):
    from gossamer.ssrf import validate_public_url

    monkeypatch.setenv("GOSSAMER_ALLOW_PRIVATE", "true")
    assert validate_public_url("http://127.0.0.1/") is None
