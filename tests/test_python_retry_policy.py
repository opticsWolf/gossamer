"""Python adapter retries must avoid permanent errors and respect Retry-After."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from gossamer.search_providers import QuotaExhaustedError, retry


def _status_error(status_code, headers=None):
    request = httpx.Request("GET", "https://provider.example/api")
    response = httpx.Response(
        status_code, headers=headers or {}, request=request,
    )
    return httpx.HTTPStatusError(
        f"HTTP {status_code}", request=request, response=response,
    )


def test_permanent_http_error_is_not_retried():
    calls = 0

    @retry(max_attempts=3, delay=0)
    def request():
        nonlocal calls
        calls += 1
        raise _status_error(406)

    with pytest.raises(httpx.HTTPStatusError):
        request()
    assert calls == 1


def test_transport_error_is_retried(monkeypatch):
    calls, waits = 0, []
    monkeypatch.setattr("gossamer.search_providers.time.sleep", waits.append)

    @retry(max_attempts=3, delay=0.1, backoff=2)
    def request():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("temporary connection failure")
        return "ok"

    assert request() == "ok"
    assert calls == 2
    assert len(waits) == 1
    assert 0.1 <= waits[0] <= 0.125


def test_retry_after_delta_is_honored(monkeypatch):
    calls, waits = 0, []
    monkeypatch.setattr("gossamer.search_providers.time.sleep", waits.append)

    @retry(max_attempts=2, delay=0.1)
    def request():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _status_error(429, {"Retry-After": "2"})
        return "ok"

    assert request() == "ok"
    assert calls == 2
    assert len(waits) == 1
    assert 2.0 <= waits[0] <= 2.25


def test_retry_after_http_date_is_parsed():
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    error = _status_error(503, {"Retry-After": format_datetime(retry_at, usegmt=True)})

    from gossamer.search_providers import retry_after_seconds

    seconds = retry_after_seconds(error)
    assert seconds is not None
    assert 3.0 <= seconds <= 5.0


def test_retry_after_over_cap_fails_without_retry(monkeypatch):
    calls, waits = 0, []
    monkeypatch.setattr("gossamer.search_providers.time.sleep", waits.append)

    @retry(max_attempts=3, delay=0.1)
    def request():
        nonlocal calls
        calls += 1
        raise _status_error(429, {"Retry-After": "120"})

    with pytest.raises(httpx.HTTPStatusError):
        request()
    assert calls == 1
    assert waits == []


def test_application_errors_and_local_quota_are_not_retried():
    calls = 0

    @retry(max_attempts=3, delay=0)
    def application_error():
        nonlocal calls
        calls += 1
        raise ValueError("invalid provider payload")

    with pytest.raises(ValueError):
        application_error()
    assert calls == 1

    @retry(max_attempts=3, delay=0)
    def quota_error():
        nonlocal calls
        calls += 1
        raise QuotaExhaustedError("daily quota exhausted")

    with pytest.raises(QuotaExhaustedError):
        quota_error()
    assert calls == 2
