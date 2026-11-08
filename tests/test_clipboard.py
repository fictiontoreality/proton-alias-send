"""Clipboard tool selection, fallback, and timeouts."""

from __future__ import annotations

import platform
import subprocess
import sys
import time
from typing import Any

import pytest

from proton_alias_send import cli

X11_WAYLAND = ["wl-copy", "xclip", "xsel"]


@pytest.mark.parametrize(
    ("system", "tools"),
    [
        ("Darwin", ["pbcopy"]),
        ("Windows", ["clip"]),
        ("Linux", X11_WAYLAND),
        ("FreeBSD", X11_WAYLAND),
        ("OpenBSD", X11_WAYLAND),
        ("NetBSD", X11_WAYLAND),
        ("DragonFly", X11_WAYLAND),
    ],
)
def test_each_platform_gets_its_clipboard_tools(system: str, tools: list[str]) -> None:
    assert [cmd[0] for cmd in cli.clipboard_commands(system)] == tools


def fake_tools(monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, Exception | None]):
    """Patch subprocess.run so each tool raises its outcome (or succeeds on None)."""
    calls: list[dict[str, Any]] = []

    def run(cmd: list[str], **kwargs: Any) -> None:
        calls.append({"tool": cmd[0], **kwargs})
        outcome = outcomes[cmd[0]]
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(platform, "system", lambda: "FreeBSD")
    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_a_hung_tool_is_skipped_for_the_next_one(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_tools(monkeypatch, {
        "wl-copy": subprocess.TimeoutExpired("wl-copy", cli.CLIPBOARD_TIMEOUT),
        "xclip": None,
        "xsel": None,
    })

    assert cli.copy_to_clipboard("ra+abc@sl.co") is True
    assert [c["tool"] for c in calls] == ["wl-copy", "xclip"]
    assert all(c["timeout"] == cli.CLIPBOARD_TIMEOUT for c in calls)
    assert calls[-1]["input"] == b"ra+abc@sl.co"


def test_every_kind_of_failure_falls_through_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_tools(monkeypatch, {
        "wl-copy": FileNotFoundError("wl-copy"),
        "xclip": subprocess.CalledProcessError(1, "xclip"),
        "xsel": subprocess.TimeoutExpired("xsel", cli.CLIPBOARD_TIMEOUT),
    })

    assert cli.copy_to_clipboard("ra+abc@sl.co") is False
    assert [c["tool"] for c in calls] == X11_WAYLAND


def test_a_real_hanging_process_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the real subprocess.run timeout, not a stand-in."""
    hang = [sys.executable, "-c", "import time; time.sleep(60)"]
    monkeypatch.setattr(cli, "clipboard_commands", lambda _system: [hang])
    monkeypatch.setattr(cli, "CLIPBOARD_TIMEOUT", 0.5)

    started = time.monotonic()
    assert cli.copy_to_clipboard("ra+abc@sl.co") is False
    assert time.monotonic() - started < 10
