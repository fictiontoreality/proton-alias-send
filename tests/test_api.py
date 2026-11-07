"""API calls: pagination, idempotent contact creation, and error normalisation."""

from __future__ import annotations

import http.client
import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from proton_alias_send import cli

BASE = "https://sl.test"
KEY = "secret-key"


class FakeResponse(io.BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def fake_urlopen(pages: dict[str, Any], calls: list[str] | None = None):
    """Return a urlopen stand-in serving `pages`, keyed by URL, recording calls."""

    def _open(req: Any, timeout: float | None = None) -> FakeResponse:
        url = req.full_url
        if calls is not None:
            calls.append(url)
        if url not in pages:
            raise AssertionError(f"unexpected request to {url}")
        payload = pages[url]
        if isinstance(payload, Exception):
            raise payload
        return FakeResponse(json.dumps(payload).encode())

    return _open


def http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        f"{BASE}/api/v2/aliases", code, "err", {}, io.BytesIO(body)  # type: ignore[arg-type]
    )


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    def _patch(pages: dict[str, Any], calls: list[str] | None = None) -> None:
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(pages, calls))

    return _patch


def test_find_alias_id_matches_case_insensitively(patched) -> None:
    patched({
        f"{BASE}/api/v2/aliases?page_id=0": {
            "aliases": [{"id": 7, "email": "Work@SimpleLogin.io"}]
        },
    })

    assert cli.find_alias_id(BASE, KEY, "work@simplelogin.io") == 7


def test_find_alias_id_paginates(patched) -> None:
    calls: list[str] = []
    patched(
        {
            f"{BASE}/api/v2/aliases?page_id=0": {"aliases": [{"id": 1, "email": "a@x.io"}]},
            f"{BASE}/api/v2/aliases?page_id=1": {"aliases": [{"id": 2, "email": "b@x.io"}]},
        },
        calls,
    )

    assert cli.find_alias_id(BASE, KEY, "b@x.io") == 2
    assert len(calls) == 2


def test_find_alias_id_returns_none_when_exhausted(patched) -> None:
    patched({
        f"{BASE}/api/v2/aliases?page_id=0": {"aliases": [{"id": 1, "email": "a@x.io"}]},
        f"{BASE}/api/v2/aliases?page_id=1": {"aliases": []},
    })

    assert cli.find_alias_id(BASE, KEY, "missing@x.io") is None


def test_find_alias_id_gives_up_on_a_server_that_ignores_paging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A self-hosted instance returning page 0 forever must not hang the CLI."""
    def always_full(req: Any, timeout: float | None = None) -> FakeResponse:
        return FakeResponse(json.dumps({"aliases": [{"id": 1, "email": "a@x.io"}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", always_full)

    with pytest.raises(RuntimeError, match="Gave up after"):
        cli.find_alias_id(BASE, KEY, "missing@x.io")


def test_base_url_trailing_slash_is_normalised(patched) -> None:
    patched({f"{BASE}/api/v2/aliases?page_id=0": {"aliases": []}})

    assert cli.find_alias_id(f"{BASE}/", KEY, "a@x.io") is None


def test_create_contact_returns_reverse_alias(patched) -> None:
    patched({
        f"{BASE}/api/aliases/7/contacts": {
            "reverse_alias": "boss x.io <ra+abc@sl.co>",
            "reverse_alias_address": "ra+abc@sl.co",
            "existed": False,
        }
    })

    assert cli.create_contact(BASE, KEY, 7, "boss@x.io") == "ra+abc@sl.co"


def test_create_contact_rejects_a_response_without_the_field(patched) -> None:
    """The display-only reverse_alias must not be mistaken for the address."""
    patched({f"{BASE}/api/aliases/7/contacts": {"reverse_alias": "boss x.io <ra+abc@sl.co>"}})

    with pytest.raises(RuntimeError, match="no reverse_alias_address"):
        cli.create_contact(BASE, KEY, 7, "boss@x.io")


def capture_request(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch urlopen to serve an empty page and record the request it receives."""
    seen: dict[str, Any] = {}

    def record(req: Any, timeout: float | None = None) -> FakeResponse:
        seen["req"] = req
        return FakeResponse(json.dumps({"aliases": []}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", record)
    return seen


def test_auth_header_uses_simplelogins_spelling(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = capture_request(monkeypatch)

    cli.find_alias_id(BASE, KEY, "a@x.io")

    assert seen["req"].get_header("Authentication") == KEY
    assert seen["req"].get_header("Authorization") is None


def test_api_key_does_not_follow_a_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    """urllib copies ordinary headers onto redirects, which would hand the key to another host."""
    seen = capture_request(monkeypatch)
    cli.find_alias_id(BASE, KEY, "a@x.io")

    redirected = urllib.request.HTTPRedirectHandler().redirect_request(
        seen["req"], io.BytesIO(), 302, "Found", http.client.HTTPMessage(), "https://evil.test/"
    )

    assert redirected is not None
    assert redirected.get_header("Authentication") is None


def test_alias_lookup_searches_server_side(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scanning every page would miss aliases past MAX_ALIAS_PAGES * 20."""
    seen = capture_request(monkeypatch)

    cli.find_alias_id(BASE, KEY, " Work@SimpleLogin.io ")

    assert seen["req"].get_method() == "POST"
    assert json.loads(seen["req"].data) == {"query": "work@simplelogin.io"}


@pytest.mark.parametrize("url", ["notaurl", "file:///etc", "ftp://sl.test", "http://[bad"])
def test_non_http_base_url_is_rejected_before_any_request(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = capture_request(monkeypatch)

    with pytest.raises(RuntimeError, match="Invalid base URL"):
        cli.find_alias_id(url, KEY, "a@x.io")

    assert seen == {}


@pytest.mark.parametrize("bad_key", ["secret\nkey", "s\u20acret"])
def test_invalid_api_key_is_not_echoed_in_the_error(
    bad_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject(req: Any, timeout: float | None = None) -> FakeResponse:
        raise ValueError(f"Invalid header value {bad_key!r}")  # what http.client raises

    monkeypatch.setattr(urllib.request, "urlopen", reject)

    with pytest.raises(RuntimeError, match="invalid characters") as exc:
        cli.find_alias_id(BASE, bad_key, "a@x.io")

    assert "secret" not in str(exc.value)
    assert "s\u20acret" not in str(exc.value)


def test_invalid_port_becomes_a_runtime_error(patched) -> None:
    patched({f"{BASE}/api/v2/aliases?page_id=0": http.client.InvalidURL("nonnumeric port")})

    with pytest.raises(RuntimeError, match="Could not reach"):
        cli.find_alias_id(BASE, KEY, "a@x.io")


def test_connection_lost_while_reading_becomes_a_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Dropped(FakeResponse):
        def read(self, *args: Any) -> bytes:
            raise http.client.IncompleteRead(b"")

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: Dropped())

    with pytest.raises(RuntimeError, match="Could not reach"):
        cli.find_alias_id(BASE, KEY, "a@x.io")


@pytest.mark.parametrize(
    "payload",
    [
        {"aliases": "nope"},
        {"aliases": ["nope"]},
        {"aliases": [{"email": "a@x.io"}]},
        {"aliases": [{"email": "a@x.io", "id": "seven"}]},
    ],
)
def test_malformed_alias_listing_becomes_a_runtime_error(patched, payload: dict) -> None:
    patched({f"{BASE}/api/v2/aliases?page_id=0": payload})

    with pytest.raises(RuntimeError, match=r"Unexpected API response|valid id"):
        cli.find_alias_id(BASE, KEY, "a@x.io")


def test_http_error_surfaces_the_api_message(patched) -> None:
    patched({
        f"{BASE}/api/v2/aliases?page_id=0": http_error(400, b'{"error": "wrong alias"}')
    })

    with pytest.raises(RuntimeError, match="wrong alias"):
        cli.find_alias_id(BASE, KEY, "a@x.io")


def test_unauthorised_hints_at_the_api_key(patched) -> None:
    patched({f"{BASE}/api/v2/aliases?page_id=0": http_error(401, b'{"error": "unauthorized"}')})

    with pytest.raises(RuntimeError, match="check your API key"):
        cli.find_alias_id(BASE, KEY, "a@x.io")


def test_html_error_body_does_not_crash_the_json_decode(patched) -> None:
    """A proxy or misconfigured host can return HTML instead of the API's JSON."""
    patched({f"{BASE}/api/v2/aliases?page_id=0": http_error(502, b"<html>Bad Gateway</html>")})

    with pytest.raises(RuntimeError, match="Bad Gateway"):
        cli.find_alias_id(BASE, KEY, "a@x.io")


def test_unreachable_host_becomes_a_runtime_error(patched) -> None:
    patched({
        f"{BASE}/api/v2/aliases?page_id=0": urllib.error.URLError("Name or service not known")
    })

    with pytest.raises(RuntimeError, match="Could not reach"):
        cli.find_alias_id(BASE, KEY, "a@x.io")


def test_timeout_becomes_a_runtime_error(patched) -> None:
    patched({f"{BASE}/api/v2/aliases?page_id=0": TimeoutError()})

    with pytest.raises(RuntimeError, match="Timed out"):
        cli.find_alias_id(BASE, KEY, "a@x.io")


def test_non_json_success_body_becomes_a_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def html(req: Any, timeout: float | None = None) -> FakeResponse:
        return FakeResponse(b"<html>login page</html>")

    monkeypatch.setattr(urllib.request, "urlopen", html)

    with pytest.raises(RuntimeError, match="Non-JSON response"):
        cli.find_alias_id(BASE, KEY, "a@x.io")


def test_json_array_response_becomes_a_runtime_error(patched) -> None:
    patched({f"{BASE}/api/v2/aliases?page_id=0": ["not", "an", "object"]})

    with pytest.raises(RuntimeError, match="expected an object"):
        cli.find_alias_id(BASE, KEY, "a@x.io")


def test_requests_carry_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def record(req: Any, timeout: float | None = None) -> FakeResponse:
        seen["timeout"] = timeout
        return FakeResponse(json.dumps({"aliases": []}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", record)
    cli.find_alias_id(BASE, KEY, "a@x.io")

    assert seen["timeout"] == cli.REQUEST_TIMEOUT
