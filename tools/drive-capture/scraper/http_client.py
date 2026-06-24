"""
HTTP client for drive-capture scraper.
stdlib-only (no requests/httpx).
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from http.cookiejar import CookieJar

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level opener with cookie jar (session cookies maintained across calls)
# ---------------------------------------------------------------------------
_cookie_jar = CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookie_jar))

# ---------------------------------------------------------------------------
# Default browser-like headers
# ---------------------------------------------------------------------------
_DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "DNT": "1",
}

# Retry config
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = [1, 3, 9]
_RETRY_STATUSES = {429, 502, 503}
_MAX_RETRY_AFTER = 60


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class FetchResult:
    url: str
    status: int  # HTTP status code, 0 if network error
    body_json: dict | list | None  # parsed JSON if Content-Type is application/json
    body_text: str | None  # raw text if HTML or other text content
    error: str | None  # error description if failed, else None
    response_headers: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _decompress(data: bytes, encoding: str) -> bytes:
    """Decompress response body based on Content-Encoding header value."""
    enc = encoding.lower().strip()
    if enc == "gzip":
        return gzip.decompress(data)
    if enc == "deflate":
        try:
            return zlib.decompress(data)
        except zlib.error:
            # Some servers send raw deflate without zlib wrapper
            return zlib.decompress(data, -15)
    if enc == "br":
        try:
            import brotli  # type: ignore[import-untyped]
            return brotli.decompress(data)
        except ImportError:
            logger.warning("brotli package not installed — returning compressed bytes as-is")
            return data
    # identity or unknown
    return data


def _is_json_url_or_payload(url: str, payload: dict | None) -> bool:
    return "/api/" in url or payload is not None


def _build_headers(url: str, payload: dict | None, extra: dict[str, str] | None) -> dict[str, str]:
    headers = dict(_DEFAULT_HEADERS)
    if _is_json_url_or_payload(url, payload):
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
    # Carrefour /geoloc requires X-Requested-With to return JSON (otherwise 404)
    if "carrefour.fr/geoloc" in url:
        headers["Accept"] = "application/json, text/plain, */*"
        headers["X-Requested-With"] = "XMLHttpRequest"
    if extra:
        headers.update(extra)
    return headers


def _build_proxy_url(
    original_url: str,
    api_key: str,
    render: bool = False,
    cookies: str | None = None,
    capture_cookies: bool = False,
) -> str:
    quoted = urllib.parse.quote(original_url, safe="")
    params = f"token={api_key}&url={quoted}&render={'true' if render else 'false'}"
    if cookies:
        params += f"&setCookies={urllib.parse.quote(cookies, safe='')}"
    if capture_cookies:
        params += "&pureCookies=true&disableRedirection=true"
    return f"https://api.scrape.do?{params}"


def _do_single_request(
    url: str,
    method: str,
    body: bytes | None,
    headers: dict[str, str],
    timeout: int,
    no_redirect: bool = False,
) -> tuple[int, dict[str, str], bytes]:
    """
    Perform one HTTP request via the module-level opener.
    Returns (status, response_headers_dict, raw_body_bytes).
    Raises urllib.error.HTTPError or urllib.error.URLError on error.

    When ``no_redirect=True``, a custom opener that does not follow HTTP
    redirects is used so that 3xx responses are returned as-is (needed for
    scrape.do ``disableRedirection=true`` cookie capture).
    """
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    if no_redirect:
        # Build a one-shot opener without HTTPRedirectHandler so urllib does
        # not follow the 302 returned by scrape.do when disableRedirection=true.
        no_redir_opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(_cookie_jar),
            # Explicitly exclude redirect handler by not including it
        )
        no_redir_opener.handlers = [
            h for h in no_redir_opener.handlers
            if not isinstance(h, urllib.request.HTTPRedirectHandler)
        ]
        try:
            with no_redir_opener.open(req, timeout=timeout) as resp:
                status: int = resp.status
                resp_headers: dict[str, str] = {k.lower(): v for k, v in resp.headers.items()}
                raw_body: bytes = resp.read()
        except urllib.error.HTTPError as exc:
            # urllib raises HTTPError for 3xx when no redirect handler is present
            status = exc.code
            resp_headers = {k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])}
            raw_body = exc.read() if exc.fp else b""
        return status, resp_headers, raw_body

    with _opener.open(req, timeout=timeout) as resp:
        status = resp.status
        resp_headers = {k.lower(): v for k, v in resp.headers.items()}
        raw_body = resp.read()
    return status, resp_headers, raw_body


def _parse_response(
    url: str,
    status: int,
    resp_headers: dict[str, str],
    raw_body: bytes,
) -> FetchResult:
    encoding = resp_headers.get("content-encoding", "")
    if encoding:
        try:
            raw_body = _decompress(raw_body, encoding)
        except Exception as exc:
            logger.warning("Decompression failed (%s): %s", encoding, exc)

    content_type = resp_headers.get("content-type", "")
    text: str | None = None
    try:
        text = raw_body.decode("utf-8", errors="replace")
    except Exception:
        pass

    body_json: dict | list | None = None
    body_text: str | None = text

    if "application/json" in content_type and text is not None:
        try:
            body_json = json.loads(text)
            body_text = None
        except json.JSONDecodeError:
            pass

    return FetchResult(
        url=url,
        status=status,
        body_json=body_json,
        body_text=body_text,
        error=None,
        response_headers=resp_headers,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    form_payload: dict | None = None,
    headers: dict[str, str] | None = None,
    use_proxy: bool = False,
    render: bool = False,
    timeout: int = 60,
    cookies: str | None = None,
    capture_cookies: bool = False,
) -> FetchResult:
    """
    Fetch a URL with retry, decompression, cookie jar, and optional proxy.

    Parameters
    ----------
    url:
        Target URL.
    method:
        HTTP method (default GET).
    payload:
        Dict serialised as JSON body (POST).
    form_payload:
        Dict serialised as form-encoded body (POST).
    headers:
        Extra headers merged on top of defaults.
    use_proxy:
        Route request via scrape.do (for captcha-protected sites).
    render:
        Ask scrape.do to run a headless browser (render=true). Only used
        when use_proxy=True. Required for SPA sites (ITM, Carrefour, Leclerc).
    timeout:
        Socket timeout in seconds.
    cookies:
        Cookie string injected via setCookies scrape.do param (per-store context).
        Ignored when use_proxy=False.
    capture_cookies:
        Ask scrape.do to return Set-Cookie headers (pureCookies=true +
        disableRedirection=true). A 302 response is valid in this mode.
        Ignored when use_proxy=False.

    Returns
    -------
    FetchResult — never raises.
    """
    # --- Proxy setup ---
    actual_url = url
    actual_method = method
    actual_body: bytes | None = None

    if use_proxy:
        api_key = os.environ.get("SCRAPE_DO_API_KEY", "")
        if not api_key:
            logger.warning("SCRAPE_DO_API_KEY not set — falling back to direct request")
        else:
            actual_url = _build_proxy_url(
                url, api_key, render=render,
                cookies=cookies, capture_cookies=capture_cookies,
            )
            actual_method = "GET"
            payload = None
            form_payload = None

    # --- Build request body ---
    if payload is not None:
        actual_body = json.dumps(payload).encode()
    elif form_payload is not None:
        actual_body = urllib.parse.urlencode(form_payload).encode()
        # Inject form content-type if not already overridden
        if headers is None:
            headers = {}
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/x-www-form-urlencoded"

    # --- Build final headers (form content-type may need to bypass json detection) ---
    merged_headers = _build_headers(url, payload, headers)
    # If form_payload was set, content-type was set above; make sure it wins
    if form_payload is not None and payload is None:
        merged_headers["Content-Type"] = "application/x-www-form-urlencoded"
        # Revert Accept override from json detection (url may contain /api/)
        if "application/json" not in merged_headers.get("Accept", ""):
            pass  # already fine
        # Accept stays as-is; the json override only fires on payload!=None

    # Whether to suppress redirect-following (needed for capture_cookies mode)
    _no_redirect = use_proxy and capture_cookies

    for attempt in range(_MAX_ATTEMPTS):
        try:
            status, resp_headers, raw_body = _do_single_request(
                actual_url, actual_method, actual_body, merged_headers, timeout,
                no_redirect=_no_redirect,
            )

            # capture_cookies mode: 302 is the expected success response — return immediately
            if _no_redirect and status == 302:
                return _parse_response(url, status, resp_headers, raw_body)

            # 403 → return immediately, no retry
            if status == 403:
                result = _parse_response(url, status, resp_headers, raw_body)
                result.error = "HTTP 403 Forbidden — possible captcha/block"
                return result

            # Retry-eligible statuses
            if status in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS - 1:
                wait = _BACKOFF_SECONDS[attempt]
                if status == 429:
                    retry_after_raw = resp_headers.get("retry-after", "")
                    if retry_after_raw:
                        try:
                            wait = min(int(retry_after_raw), _MAX_RETRY_AFTER)
                        except ValueError:
                            pass
                logger.warning(
                    "HTTP %d from %s — retrying in %ds (attempt %d/%d)",
                    status,
                    url,
                    wait,
                    attempt + 1,
                    _MAX_ATTEMPTS,
                )
                time.sleep(wait)
                continue

            return _parse_response(url, status, resp_headers, raw_body)

        except urllib.error.HTTPError as exc:
            status = exc.code
            resp_headers_raw = exc.headers
            resp_headers_dict = {k.lower(): v for k, v in (resp_headers_raw.items() if resp_headers_raw else [])}
            raw_body = exc.read() if exc.fp else b""

            if status == 403:
                result = _parse_response(url, status, resp_headers_dict, raw_body)
                result.error = f"HTTP 403 Forbidden — possible captcha/block: {exc}"
                return result

            if status in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS - 1:
                wait = _BACKOFF_SECONDS[attempt]
                if status == 429:
                    retry_after_raw = resp_headers_dict.get("retry-after", "")
                    if retry_after_raw:
                        try:
                            wait = min(int(retry_after_raw), _MAX_RETRY_AFTER)
                        except ValueError:
                            pass
                logger.warning(
                    "HTTP %d from %s — retrying in %ds (attempt %d/%d)",
                    status,
                    url,
                    wait,
                    attempt + 1,
                    _MAX_ATTEMPTS,
                )
                time.sleep(wait)
                continue

            # Non-retriable HTTP error
            result = _parse_response(url, status, resp_headers_dict, raw_body)
            result.error = f"HTTP {status}: {exc.reason}"
            return result

        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", str(exc))
            if attempt < _MAX_ATTEMPTS - 1:
                wait = _BACKOFF_SECONDS[attempt]
                logger.warning(
                    "Network error fetching %s: %s — retrying in %ds (attempt %d/%d)",
                    url,
                    reason,
                    wait,
                    attempt + 1,
                    _MAX_ATTEMPTS,
                )
                time.sleep(wait)
                continue

            return FetchResult(
                url=url,
                status=0,
                body_json=None,
                body_text=None,
                error=f"Network error: {reason}",
            )

    # Should be unreachable
    return FetchResult(
        url=url,
        status=0,
        body_json=None,
        body_text=None,
        error="Max retries exceeded",
    )
