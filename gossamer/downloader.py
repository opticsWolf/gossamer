"""Safe, standalone URL-to-file downloads for the toolbox."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import httpx

from gossamer.config import normalize_url
from gossamer.ssrf import SsrfBlockedError

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 10
_PDF_MAGIC = b"%PDF-"
_BOT_MARKERS = (
    b"cloudflare",
    b"captcha",
    b"attention required",
    b"access denied",
    b"akamai",
    b"robot check",
)


class _DownloadFailure(Exception):
    def __init__(self, code: str, message: str, **details) -> None:
        self.code = code
        self.details = details
        super().__init__(message)


class DownloadService:
    """Stream a remote file to a caller-selected local path safely."""

    def __init__(self, toolbox) -> None:
        self._tb = toolbox

    def _download_candidates(
        self,
        source: str,
        fallback_urls: list[str],
        output_path: str,
        **kwargs,
    ) -> str:
        if not isinstance(source, str) or not source.strip():
            return self._error(str(source), "invalid_argument", "source must be a URL string")
        if not isinstance(fallback_urls, (list, tuple)) or any(
            not isinstance(url, str) or not url.strip() for url in fallback_urls
        ):
            return self._error(
                str(source), "invalid_argument",
                "fallback_urls must be a list of non-empty URL strings",
            )
        candidates = list(dict.fromkeys([source, *fallback_urls]))
        attempts = []
        for candidate in candidates:
            result = json.loads(self.download_file(
                candidate, output_path, fallback_urls=None, **kwargs,
            ))
            attempt = {
                "url": candidate,
                "status": result.get("status"),
                "error": result.get("error"),
            }
            attempts.append(attempt)
            if result.get("status") == "downloaded":
                result["requested_source"] = source
                result["attempts"] = attempts
                result["mirror_fallback_used"] = candidate != source
                return json.dumps(result, indent=2)
            error = result.get("error")
            if isinstance(error, dict) and error.get("code") in {
                "invalid_argument", "file_exists",
            }:
                result["attempts"] = attempts
                return json.dumps(result, indent=2)

        human_needed = any(
            isinstance(attempt.get("error"), dict)
            and attempt["error"].get("code") in {"bot_wall", "access_denied"}
            for attempt in attempts
        )
        code = "human_action_needed" if human_needed else "all_sources_failed"
        message = (
            "All candidates were blocked by access controls or anti-bot challenges; "
            "human action is needed. No bypass was attempted."
            if human_needed
            else "All caller-supplied download candidates failed."
        )
        return self._error(
            source,
            code,
            message,
            attempted_urls=[attempt["url"] for attempt in attempts],
            attempts=attempts,
        )

    @staticmethod
    def _error(source: str, code: str, message: str, **details) -> str:
        error = {"code": code, "message": message}
        error.update(details)
        return json.dumps({"source": source, "status": "error", "error": error}, indent=2)

    @staticmethod
    def _http_error(response: httpx.Response, url: str) -> _DownloadFailure:
        status = response.status_code
        retry_after = response.headers.get("Retry-After")
        if status == 404:
            code, message = "not_found", "The requested file was not found."
        elif status in (401, 403):
            code, message = "access_denied", "The server denied access to this file."
        elif status in (408, 429):
            code, message = "rate_limited", "The server asked the client to slow down."
        elif status >= 500:
            code, message = "server_error", f"The server returned HTTP {status}."
        else:
            code, message = "http_error", f"The server returned HTTP {status}."

        # Inspect only a small bounded error prefix to distinguish a bot wall
        # from a generic 403 without downloading an error page into memory.
        sample = bytearray()
        if status in (401, 403, 406) and "html" in response.headers.get("content-type", "").lower():
            try:
                for chunk in response.iter_bytes():
                    sample.extend(chunk[: 1024 - len(sample)])
                    if len(sample) >= 1024:
                        break
            except httpx.HTTPError:
                pass
        lowered = bytes(sample).lower()
        if any(marker in lowered for marker in _BOT_MARKERS):
            code, message = (
                "bot_wall",
                "The server returned an anti-bot or browser challenge; no bypass was attempted.",
            )
        return _DownloadFailure(
            code, message, http_status=status, url=url, retry_after=retry_after,
        )

    def download_file(
        self,
        source: str,
        output_path: str,
        *,
        min_bytes: int = 1,
        max_bytes: Optional[int] = None,
        expected_format: Optional[str] = None,
        overwrite: bool = False,
        fallback_urls: Optional[list[str]] = None,
    ) -> str:
        """Download *source* atomically to *output_path* and return JSON.

        PDF validation is enabled by ``expected_format="pdf"`` or a ``.pdf``
        destination suffix. A configured maximum response size is always
        enforced; callers may lower/raise it per download with ``max_bytes``.
        Existing files are not replaced unless ``overwrite=True``. Optional
        ``fallback_urls`` are caller-supplied, tried sequentially, and each is
        independently checked against robots/SSRF policy.
        """
        if fallback_urls is not None:
            if not isinstance(fallback_urls, (list, tuple)):
                return self._error(
                    str(source), "invalid_argument",
                    "fallback_urls must be a list of non-empty URL strings",
                )
            if fallback_urls:
                return self._download_candidates(
                    source,
                    fallback_urls,
                    output_path,
                    min_bytes=min_bytes,
                    max_bytes=max_bytes,
                    expected_format=expected_format,
                    overwrite=overwrite,
                )
        try:
            url = normalize_url(source)
        except (TypeError, ValueError) as exc:
            return self._error(str(source), "invalid_url", str(exc))

        try:
            self._tb._validate_url(url)
        except (ValueError, SsrfBlockedError) as exc:
            return self._error(url, "url_rejected", str(exc))
        if self._tb._robots_disallows(url):
            return self._error(
                url,
                "robots_disallowed",
                "The URL is disallowed by robots.txt; no request was made.",
            )

        try:
            target = Path(output_path).expanduser()
            if not str(output_path).strip() or target.name in ("", ".", ".."):
                raise ValueError("output_path must name a file")
            target = target.absolute()
            minimum = int(min_bytes)
            configured_cap = (
                self._tb.max_response_bytes
                if max_bytes is None or max_bytes == 0
                else max_bytes
            )
            cap = int(configured_cap)
            if minimum < 0:
                raise ValueError("min_bytes must be >= 0")
            if cap < 1:
                raise ValueError("max_bytes must be >= 1")
            if minimum > cap:
                raise ValueError("min_bytes cannot exceed max_bytes")
            if expected_format not in (None, "pdf"):
                raise ValueError("expected_format must be omitted or 'pdf'")
        except (TypeError, ValueError, OSError) as exc:
            return self._error(url, "invalid_argument", str(exc))

        if target.exists() and not overwrite:
            return self._error(
                url, "file_exists", f"Output already exists: {target}",
                output_path=str(target),
            )

        expect_pdf = expected_format == "pdf" or target.suffix.lower() == ".pdf"
        current_url = url
        temp_path: Optional[str] = None
        try:
            with httpx.Client(timeout=30.0, follow_redirects=False) as client:
                for redirect_count in range(_MAX_REDIRECTS + 1):
                    # Every redirect target is independently checked against
                    # SSRF and robots policy before it is requested.
                    self._tb._validate_url(current_url)
                    if self._tb._robots_disallows(current_url):
                        raise _DownloadFailure(
                            "robots_disallowed",
                            "A redirect target is disallowed by robots.txt.",
                            url=current_url,
                        )
                    self._tb._rate_limit_domain(current_url)
                    with client.stream(
                        "GET", current_url, headers=self._tb._next_headers(),
                    ) as response:
                        if response.status_code in _REDIRECT_STATUSES:
                            location = response.headers.get("Location")
                            if not location:
                                raise _DownloadFailure(
                                    "redirect_error",
                                    "The server returned a redirect without a Location header.",
                                    http_status=response.status_code,
                                    url=current_url,
                                )
                            if redirect_count == _MAX_REDIRECTS:
                                raise _DownloadFailure(
                                    "too_many_redirects",
                                    f"The download exceeded {_MAX_REDIRECTS} redirects.",
                                    url=current_url,
                                )
                            try:
                                current_url = normalize_url(urljoin(current_url, location))
                            except ValueError as exc:
                                raise _DownloadFailure(
                                    "invalid_redirect", str(exc), url=current_url,
                                ) from exc
                            continue

                        if response.status_code == 206:
                            raise _DownloadFailure(
                                "partial_response",
                                "The server returned partial content without a resume request.",
                                http_status=response.status_code,
                                url=current_url,
                            )
                        if not 200 <= response.status_code < 300:
                            raise self._http_error(response, current_url)

                        declared_length = response.headers.get("Content-Length")
                        try:
                            if declared_length is not None and int(declared_length) > cap:
                                raise _DownloadFailure(
                                    "too_large",
                                    f"The server declared {declared_length} bytes; limit is {cap}.",
                                    http_status=response.status_code,
                                    url=current_url,
                                )
                        except ValueError:
                            # Ignore malformed Content-Length and enforce the
                            # cap against the decoded stream below.
                            pass

                        content_type = response.headers.get("Content-Type")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        fd, temp_path = tempfile.mkstemp(
                            prefix=f".{target.name}.", suffix=".part",
                            dir=str(target.parent),
                        )
                        size = 0
                        digest = hashlib.sha256()
                        prefix = bytearray()
                        sample = bytearray()
                        with os.fdopen(fd, "wb") as output:
                            for chunk in response.iter_bytes():
                                if not chunk:
                                    continue
                                size += len(chunk)
                                if size > cap:
                                    raise _DownloadFailure(
                                        "too_large",
                                        f"The download exceeded the {cap}-byte limit.",
                                        http_status=response.status_code,
                                        url=current_url,
                                    )
                                if len(prefix) < len(_PDF_MAGIC):
                                    prefix.extend(chunk[: len(_PDF_MAGIC) - len(prefix)])
                                if len(sample) < 1024:
                                    sample.extend(chunk[: 1024 - len(sample)])
                                digest.update(chunk)
                                output.write(chunk)

                        if size < minimum:
                            raise _DownloadFailure(
                                "too_small",
                                f"The file contained {size} bytes; minimum is {minimum}.",
                                bytes=size,
                                http_status=response.status_code,
                                url=current_url,
                            )
                        if expect_pdf and not bytes(prefix).startswith(_PDF_MAGIC):
                            sample_lower = bytes(sample).lower()
                            is_html = "html" in (content_type or "").lower() or sample_lower.lstrip().startswith(b"<html")
                            if is_html and any(marker in sample_lower for marker in _BOT_MARKERS):
                                code = "bot_wall"
                                message = (
                                    "The server returned an anti-bot/browser challenge instead "
                                    "of a PDF; no bypass was attempted."
                                )
                            elif is_html:
                                code = "unexpected_content"
                                message = "The server returned HTML instead of a PDF."
                            else:
                                code = "invalid_file"
                                message = "The response does not have a PDF signature (%PDF-)."
                            raise _DownloadFailure(
                                code, message, bytes=size, content_type=content_type,
                                http_status=response.status_code, url=current_url,
                            )

                        if overwrite:
                            os.replace(temp_path, target)
                        else:
                            try:
                                # Hard-link creation is atomic and cannot
                                # overwrite a destination created concurrently.
                                os.link(temp_path, target)
                            except FileExistsError as exc:
                                raise _DownloadFailure(
                                    "file_exists",
                                    f"Output already exists: {target}",
                                    output_path=str(target),
                                ) from exc
                            os.unlink(temp_path)
                        temp_path = None
                        return json.dumps(
                            {
                                "source": url,
                                "final_url": current_url,
                                "output_path": str(target),
                                "bytes": size,
                                "sha256": digest.hexdigest(),
                                "content_type": content_type,
                                "http_status": response.status_code,
                                "status": "downloaded",
                            },
                            indent=2,
                        )
            raise _DownloadFailure("redirect_error", "The download did not reach a file response.")
        except _DownloadFailure as exc:
            return self._error(
                url, exc.code, str(exc), **exc.details,
            )
        except (ValueError, SsrfBlockedError) as exc:
            return self._error(url, "url_rejected", str(exc))
        except httpx.TimeoutException as exc:
            return self._error(url, "timeout", str(exc) or "The download timed out.")
        except httpx.TransportError as exc:
            return self._error(url, "network_error", str(exc))
        except OSError as exc:
            return self._error(url, "local_write_error", str(exc))
        finally:
            if temp_path is not None:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass
