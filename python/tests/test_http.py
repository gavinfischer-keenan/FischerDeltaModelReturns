import io
import json
import urllib.error
import urllib.request
from email.message import Message

import pytest

from fdmr_adapters.http import USER_AGENT, HttpJsonClient


def http_error(code, retry_after=None):
    h = Message()
    if retry_after:
        h["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://example.test", code, "err", h, None)


def fake_urlopen(responses, seen):
    def urlopen(req, timeout):
        seen.append(req)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return io.BytesIO(json.dumps(r).encode())
    return urlopen


def test_rate_limit_waits_as_the_server_asks(monkeypatch):
    seen, waits = [], []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen([http_error(429, "7"), {"ok": 1}], seen))
    assert HttpJsonClient(sleep=waits.append).get_json("https://example.test") == {"ok": 1}
    assert waits == [7.0]
    assert seen[0].get_header("User-agent") == USER_AGENT


def test_rate_limit_without_retry_after_backs_off_from_a_minute(monkeypatch):
    waits = []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen([http_error(429), http_error(429), {"ok": 1}], []))
    HttpJsonClient(sleep=waits.append).get_json("https://example.test")
    assert waits == [60.0, 120.0]


def test_client_errors_are_not_retried(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen([http_error(404)], []))
    with pytest.raises(urllib.error.HTTPError):
        HttpJsonClient(sleep=lambda s: None).get_json("https://example.test")


def test_gives_up_after_the_retries(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen([http_error(503)] * 3, []))
    with pytest.raises(RuntimeError):
        HttpJsonClient(retries=2, sleep=lambda s: None).get_json("https://example.test")
