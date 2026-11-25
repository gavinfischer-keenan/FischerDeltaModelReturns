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
    def __init__(self, timeout_s: float = 60.0, retries: int = 3, backoff_s: float = 5.0) -> None:
        self.timeout_s, self.retries, self.backoff_s = timeout_s, retries, backoff_s

    def get_json(self, url: str) -> Any:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504):
                    raise
                last = e
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
            if attempt < self.retries:
                time.sleep(self.backoff_s * 2**attempt)
        raise RuntimeError(f"giving up on {url}: {last}")
