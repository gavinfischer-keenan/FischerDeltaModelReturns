"""A small, polite HTTP client for public data services (specification section 6.2):
a plain client name, retries with back-off, and nothing about who is asking."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Protocol

USER_AGENT = "fdmr-runner/0.1"


class JsonClient(Protocol):
    def get_json(self, url: str) -> Any: ...


class HttpJsonClient:
    """Retries server errors with back-off. On "429 Too Many Requests" it waits
    as long as the server's Retry-After asks, or backs off from a minute upward."""

    def __init__(
        self,
        timeout_s: float = 60.0,
        retries: int = 5,
        backoff_s: float = 5.0,
        rate_limit_wait_s: float = 60.0,
        sleep=time.sleep,
    ) -> None:
        self.timeout_s, self.retries, self.backoff_s = timeout_s, retries, backoff_s
        self.rate_limit_wait_s, self.sleep = rate_limit_wait_s, sleep

    def get_json(self, url: str) -> Any:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            wait = self.backoff_s * 2**attempt
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504):
                    raise
                last = e
                if e.code == 429:
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    wait = float(retry_after) if retry_after and retry_after.isdigit() else self.rate_limit_wait_s * 2**attempt
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
            if attempt < self.retries:
                self.sleep(wait)
        raise RuntimeError(f"giving up on {url}: {last}")
