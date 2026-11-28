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


def test_rate_limit_without_retry_after_backs_off_longer(monkeypatch):
    waits = []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen([http_error(429), http_error(429), {"ok": 1}], []))
    HttpJsonClient(sleep=waits.append).get_json("https://example.test")
    assert waits == [20.0, 40.0]  # four times the back-off: 5 s doubling


def test_the_waits_for_one_address_stay_within_the_cap_and_none_follows_the_last_try(monkeypatch):
    """A 429 asking for an hour must not stall an hourly cycle (#90)."""
    waits = []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen([http_error(429, "3600")] * 4, []))
    with pytest.raises(RuntimeError):
        HttpJsonClient(sleep=waits.append).get_json("https://example.test")
    assert sum(waits) <= 120.0 and len(waits) == 1  # capped; the cap used up, no more waiting


def test_retry_after_may_be_a_date(monkeypatch):
    from email.utils import formatdate
    import time

    waits = []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen([http_error(429, formatdate(time.time() + 30, usegmt=True)), {"ok": 1}], []))
    HttpJsonClient(sleep=waits.append).get_json("https://example.test")
    assert 25 <= waits[0] <= 31


def test_a_connection_dropped_while_reading_is_retried(monkeypatch):
    import http.client

    waits = []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen([http.client.IncompleteRead(b"{"), ConnectionResetError(), {"ok": 1}], []))
    assert HttpJsonClient(sleep=waits.append).get_json("https://example.test") == {"ok": 1}
    assert waits == [5.0, 10.0]


def test_a_token_is_not_passed_on_through_a_redirect(monkeypatch):
    from fdmr_adapters import http

    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen([{"ok": 1}], seen))
    http.get_json("https://example.test", bearer="t")
    assert seen[0].unredirected_hdrs.get("Authorization") == "Bearer t" and "Authorization" not in seen[0].headers


def test_client_errors_are_not_retried(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen([http_error(404)], []))
    with pytest.raises(urllib.error.HTTPError):
        HttpJsonClient(sleep=lambda s: None).get_json("https://example.test")


def test_gives_up_after_the_retries(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen([http_error(503)] * 3, []))
    with pytest.raises(RuntimeError):
        HttpJsonClient(retries=2, sleep=lambda s: None).get_json("https://example.test")


def test_extra_headers_are_sent(monkeypatch):
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen([{"ok": 1}], seen))
    HttpJsonClient(headers={"X-Api-Key": "k"}).get_json("https://example.test")
    assert seen[0].get_header("X-api-key") == "k"


def test_usgs_key_comes_from_the_environment(monkeypatch, tmp_path):
    from fdmr_adapters import usgs

    monkeypatch.setattr(usgs, "API_KEY_FILE", tmp_path / "none")
    monkeypatch.delenv(usgs.API_KEY_ENV, raising=False)
    assert usgs.api_key() is None
    monkeypatch.setenv(usgs.API_KEY_ENV, " abc ")
    assert usgs.api_key() == "abc"
    monkeypatch.delenv(usgs.API_KEY_ENV)
    (tmp_path / "key").write_text("fromfile" + chr(10))
    monkeypatch.setattr(usgs, "API_KEY_FILE", tmp_path / "key")
    assert usgs.api_key() == "fromfile"


def test_a_server_that_does_not_answer_is_tried_once_more_then_given_up(monkeypatch):
    """Each try costs a whole timeout; four cost a Delta cycle 20 minutes."""
    seen, waits = [], []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen([urllib.error.URLError(TimeoutError("timed out"))] * 5, seen))
    with pytest.raises(RuntimeError):
        HttpJsonClient(sleep=waits.append).get_json("https://example.test")
    assert len(seen) == 2 and len(waits) == 1
