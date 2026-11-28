"""A small, polite HTTP client for public data services (specification section 6.2):
a plain client name, retries with back-off, and nothing about who is asking.

Every adapter asks through here (#65), so there is one policy for all of them:

- **Retried:** a server error (500, 502, 503, 504), "429 Too Many Requests",
  and a connection that fails or drops while the reply is read.
- **Not retried:** any other answer (a 404 is the answer).
- **The waits:** a back-off doubling from `backoff_s`; on a 429, the
  server's Retry-After (seconds or a date), within `max_wait_s`. All the
  waits for one address together stay within `max_wait_s` (#90), and there
  is no wait after the last try. A server that does not answer at all
  (a timeout) is tried once more, not until the retries run out: each try
  costs a whole timeout (four took 20 minutes of a Delta cycle, 28 Nov 2026).
- **What is sent:** the User-Agent below and nothing more. A token
  (`bearer`) goes only to the host asked, never on through a redirect.

Being polite between requests (a pause per host) is the fetch step's job
(fdmr_runner.fetch, #34); this is one request.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

USER_AGENT = "fdmr-runner/0.1"
RETRIED = (429, 500, 502, 503, 504)  # answers worth asking again
RETRIES = 3  # tries after the first (FDMR's choice)
BACKOFF_S = 5.0  # first wait before a retry, doubling each time (FDMR's choice)
MAX_WAIT_S = 120.0  # all the waits for one address, together, at most this (FDMR's choice: a cycle is hourly, #90)
MAX_TIMEOUTS = 1  # after a timeout one more try, then give up: a server that does not answer costs a whole timeout each try (FDMR's choice)


def _retry_after(e: urllib.error.HTTPError) -> float | None:
    """A 429's Retry-After, in seconds: a number of seconds, or a date."""
    v = e.headers.get("Retry-After") if e.headers else None
    if not v:
        return None
    v = v.strip()
    if v.isdigit():
        return float(v)
    try:
        return max(0.0, parsedate_to_datetime(v).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def _timed_out(e: Exception) -> bool:
    """Whether a failure was the server not answering in time."""
    return isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError) or "timed out" in str(e)


def request(url: str, *, method: str = "GET", data: bytes | None = None, headers: dict[str, str] | None = None,
            accept: str | None = None, bearer: str | None = None, byte_range: tuple[int, int] | None = None,
            timeout: float = 60.0, retries: int = RETRIES, backoff_s: float = BACKOFF_S,
            max_wait_s: float = MAX_WAIT_S, sleep=time.sleep) -> bytes:
    """The reply's body (empty for "204 No Content"), by the policy above."""
    h = {"User-Agent": USER_AGENT, **(headers or {})}
    if accept:
        h["Accept"] = accept
    if byte_range:
        h["Range"] = f"bytes={byte_range[0]}-{byte_range[1] - 1}"
    waited, last, timeouts = 0.0, None, 0
    for attempt in range(retries + 1):
        wait = backoff_s * 2**attempt
        try:
            req = urllib.request.Request(url, data=data, method=method, headers=h)
            if bearer:
                req.add_unredirected_header("Authorization", f"Bearer {bearer}")  # not passed on to a redirect's host
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return b"" if getattr(r, "status", 200) == 204 else r.read()
        except urllib.error.HTTPError as e:
            if e.code not in RETRIED:
                raise
            last = e
            if e.code == 429:
                ra = _retry_after(e)
                wait = ra if ra is not None else wait * 4
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, OSError) as e:
            last = e  # a connection that failed, or dropped while the reply was read
            if _timed_out(e):
                timeouts += 1
        if attempt == retries or timeouts > MAX_TIMEOUTS:
            break
        wait = min(wait, max_wait_s - waited)
        if wait <= 0:
            break
        sleep(wait)
        waited += wait
    raise RuntimeError(f"giving up on {url}: {last}")


def get_text(url: str, encoding: str = "utf-8", **kw) -> str:
    """The reply as text; bytes that do not decode are replaced."""
    return request(url, **kw).decode(encoding, "replace")


def get_json(url: str, **kw) -> Any:
    """The reply as JSON."""
    kw.setdefault("accept", "application/json")
    return json.loads(request(url, **kw))


def post_json(url: str, body: Any, **kw) -> Any:
    """POST a JSON body; the reply as JSON, or None for an empty reply ("204 No Content")."""
    headers = {"Content-Type": "application/json", **kw.pop("headers", {})}
    kw.setdefault("accept", "application/json")
    raw = request(url, method="POST", data=json.dumps(body).encode(), headers=headers, **kw)
    return json.loads(raw) if raw else None


class JsonClient(Protocol):
    def get_json(self, url: str) -> Any: ...


class HttpJsonClient:
    """`get_json` by the policy above, with this client's own settings (an
    adapter's tests pass a recorded client instead)."""

    def __init__(
        self,
        timeout_s: float = 60.0,
        retries: int = RETRIES,
        backoff_s: float = BACKOFF_S,
        max_wait_s: float = MAX_WAIT_S,
        sleep=time.sleep,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.timeout_s, self.retries, self.backoff_s = timeout_s, retries, backoff_s
        self.max_wait_s, self.sleep = max_wait_s, sleep
        self.headers = headers or {}

    def get_json(self, url: str) -> Any:
        return get_json(url, headers=self.headers, timeout=self.timeout_s, retries=self.retries,
                        backoff_s=self.backoff_s, max_wait_s=self.max_wait_s, sleep=self.sleep)
