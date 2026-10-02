"""One place for HTTP GETs and POSTs that return JSON, with a couple of polite retries."""
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

ERROR_BODY_LIMIT = 500  # some servers return a full HTML error page instead of a small JSON body


def _http_error_message(e):
    """An HTTPError's default str() is just "HTTP Error 400: Bad Request" - the status line, with
    none of the actual detail an API puts in the response body (e.g. OpenAI's "Unsupported
    parameter: 'max_tokens'..."). Read and include it, since every caller that catches on failure
    was otherwise showing that useless status-line text as the whole error."""
    try:
        body = e.read().decode("utf-8", "replace").strip()
    except Exception:
        body = ""
    return f"HTTP {e.code}: {body[:ERROR_BODY_LIMIT] if body else (e.reason or '')}"


def get_json(url, headers=None, params=None, timeout=20, retries=2):
    """GET url and return the decoded JSON. Retries on timeouts, 429 and 5xx. Headers (which is where
    the API tokens go) are never included in error messages."""
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)
    request = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                try:
                    wait = min(10, int(e.headers.get("Retry-After", "")))
                except ValueError:
                    wait = attempt + 1
                time.sleep(wait)
                continue
            raise RuntimeError(_http_error_message(e)) from e
        except (urllib.error.URLError, TimeoutError):
            if attempt < retries:
                time.sleep(attempt + 1)
                continue
            raise


def get_bytes(url, headers=None, params=None, timeout=10, max_bytes=2_000_000, retries=2):
    """GET url and return (content_type, data) - for images. Same retry and error rules as get_json;
    RuntimeError if the body is bigger than max_bytes."""
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)
    request = urllib.request.Request(url, headers=headers or {})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise RuntimeError("Response too large")
                content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                return content_type, data
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                try:
                    wait = min(10, int(e.headers.get("Retry-After", "")))
                except ValueError:
                    wait = attempt + 1
                time.sleep(wait)
                continue
            raise RuntimeError(_http_error_message(e)) from e
        except (urllib.error.URLError, TimeoutError):
            if attempt < retries:
                time.sleep(attempt + 1)
                continue
            raise


def post_json(url, headers=None, body=None, timeout=20):
    """POST a JSON body and return the decoded JSON response. No retries, unlike get_json - this is
    for write actions (e.g. adding a movie to Radarr), where blindly retrying a failed write is
    riskier than just surfacing the error and letting the caller decide whether to try again."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Accept": "application/json", "Content-Type": "application/json",
                                             **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise RuntimeError(_http_error_message(e)) from e
