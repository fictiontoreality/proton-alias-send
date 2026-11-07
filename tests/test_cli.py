"""End-to-end behaviour of run(): setting resolution, output contract, exit codes."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from proton_alias_send import cli
from tests.test_api import FakeResponse

REVERSE = "boss_at_example.com_abc@simplelogin.co"


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep tests off the developer's real config file and API key."""
    monkeypatch.delenv("SIMPLELOGIN_API_KEY", raising=False)
    monkeypatch.setattr(cli, "get_config_path", lambda: tmp_path / "absent.toml")


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch):
    """Serve a single alias and accept contact creation, recording the requests made."""
    seen: list[dict[str, Any]] = []

    def _open(req: Any, timeout: float | None = None) -> FakeResponse:
        seen.append({
            "url": req.full_url,
            "key": req.headers.get("Authentication"),
            "body": json.loads(req.data) if req.data else None,
        })
        if "/contacts" in req.full_url:
            return FakeResponse(json.dumps({"reverse_alias": REVERSE}).encode())
        page = req.full_url.rsplit("page_id=", 1)[1]
        aliases = [{"id": 7, "email": "work@simplelogin.io"}] if page == "0" else []
        return FakeResponse(json.dumps({"aliases": aliases}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", _open)
    return seen


def write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body)
    return path


def test_only_the_reverse_alias_goes_to_stdout(
    api, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """The mailto: piping trick in the README depends on this."""
    config = write_config(tmp_path, 'api_key = "k"\nalias = "work@simplelogin.io"\n')

    assert cli.run(["--config", str(config), "boss@example.com"]) == 0

    out = capsys.readouterr()
    assert out.out == f"{REVERSE}\n"
    assert "Looking up alias ID" in out.err


def test_quiet_silences_progress_but_not_the_result(
    api, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    config = write_config(tmp_path, 'api_key = "k"\nalias = "work@simplelogin.io"\n')

    assert cli.run(["--config", str(config), "-q", "boss@example.com"]) == 0

    out = capsys.readouterr()
    assert out.out == f"{REVERSE}\n"
    assert out.err == ""


def test_cli_alias_overrides_config(api, tmp_path: Path) -> None:
    config = write_config(tmp_path, 'api_key = "k"\nalias = "unused@simplelogin.io"\n')

    assert cli.run(
        ["--config", str(config), "-a", "work@simplelogin.io", "boss@example.com"]
    ) == 0
    assert api[-1]["body"] == {"contact": "boss@example.com"}


def test_empty_api_key_in_config_falls_back_to_the_env_var(
    api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--init-config writes api_key = "", which must not block $SIMPLELOGIN_API_KEY."""
    monkeypatch.setenv("SIMPLELOGIN_API_KEY", "from-env")
    config = write_config(tmp_path, 'api_key = ""\nalias = "work@simplelogin.io"\n')

    assert cli.run(["--config", str(config), "boss@example.com"]) == 0
    assert api[0]["key"] == "from-env"


def test_no_copy_overrides_config_copy_true(
    api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied: list[str] = []

    def record(text: str) -> bool:
        copied.append(text)
        return True

    monkeypatch.setattr(cli, "copy_to_clipboard", record)
    config = write_config(
        tmp_path, 'api_key = "k"\nalias = "work@simplelogin.io"\ncopy = true\n'
    )

    assert cli.run(["--config", str(config), "--no-copy", "boss@example.com"]) == 0
    assert copied == []

    assert cli.run(["--config", str(config), "boss@example.com"]) == 0
    assert copied == [REVERSE]


def test_clipboard_failure_warns_without_failing_the_run(
    api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(cli, "copy_to_clipboard", lambda text: False)
    config = write_config(
        tmp_path, 'api_key = "k"\nalias = "work@simplelogin.io"\ncopy = true\n'
    )

    assert cli.run(["--config", str(config), "boss@example.com"]) == 0

    out = capsys.readouterr()
    assert out.out == f"{REVERSE}\n"
    assert "clipboard copy failed" in out.err


def test_missing_settings_are_all_reported_at_once(capsys: pytest.CaptureFixture) -> None:
    assert cli.run([]) == 1

    err = capsys.readouterr().err
    assert "CONTACT_EMAIL is required" in err
    assert "No API key found" in err
    assert "No alias specified" in err


def test_unknown_alias_exits_one(api, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    config = write_config(tmp_path, 'api_key = "k"\nalias = "ghost@simplelogin.io"\n')

    assert cli.run(["--config", str(config), "boss@example.com"]) == 1
    assert "not found in your SimpleLogin account" in capsys.readouterr().err


def test_api_failure_is_reported_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    def boom(req: Any, timeout: float | None = None) -> FakeResponse:
        raise urllib.error.URLError("nope")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    config = write_config(tmp_path, 'api_key = "k"\nalias = "work@simplelogin.io"\n')

    assert cli.run(["--config", str(config), "boss@example.com"]) == 1

    out = capsys.readouterr()
    assert out.out == ""
    assert "Error: Could not reach" in out.err
    assert "Traceback" not in out.err


def test_init_config_short_circuits_before_validation(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    config = tmp_path / "new.toml"

    assert cli.run(["--config", str(config), "--init-config"]) == 0
    assert config.exists()
    assert capsys.readouterr().out == ""


def test_config_path_expands_a_tilde(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # what expanduser reads on Windows

    assert cli.run(["--config", "~/expanded.toml", "--init-config"]) == 0
    assert (tmp_path / "expanded.toml").exists()


def test_interrupt_exits_130(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    def interrupt(argv: list[str] | None) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run", interrupt)

    assert cli.main([]) == 130
    assert "Interrupted" in capsys.readouterr().err
