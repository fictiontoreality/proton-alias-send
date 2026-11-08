"""Config loading, setting resolution, and argument parsing."""

from __future__ import annotations

import argparse
import os
import platform
from pathlib import Path

import pytest

from proton_alias_send import cli


def test_merge_prefers_cli() -> None:
    assert cli.merge("cli", "config", "default") == "cli"


def test_merge_falls_back_through_the_chain() -> None:
    assert cli.merge(None, "config", "default") == "config"
    assert cli.merge(None, None, "default") == "default"


def test_merge_treats_empty_string_as_unset() -> None:
    """The generated sample config ships api_key = "", which must not shadow the env var."""
    assert cli.merge(None, "", "from-env") == "from-env"


def test_merge_keeps_false_from_config() -> None:
    """False is a real value, unlike None - `copy = false` must beat a True default."""
    assert cli.merge(None, False, True) is False


def test_load_config_missing_file_is_empty(tmp_path: Path) -> None:
    assert cli.load_config(tmp_path / "nope.toml") == {}


def test_load_config_warns_on_unknown_keys(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    path = tmp_path / "config.toml"
    path.write_text('alias = "a@b.io"\nnonsense = 1\n')

    assert cli.load_config(path) == {"alias": "a@b.io", "nonsense": 1}
    assert "nonsense" in capsys.readouterr().err


def test_load_config_rejects_malformed_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("this is not toml = = =\n")

    with pytest.raises(SystemExit):
        cli.load_config(path)


def test_init_config_writes_restrictive_permissions(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"

    assert cli.cmd_init_config(path) == 0
    assert 'base_url = "https://app.simplelogin.io"' in path.read_text()
    if platform.system() != "Windows":  # no POSIX modes there; cmd_init_config skips the chmod
        assert path.stat().st_mode & 0o777 == 0o600


posix_only = pytest.mark.skipif(
    platform.system() == "Windows", reason="no POSIX permission bits on Windows"
)


@posix_only
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o620])
def test_exposed_config_with_a_key_warns(
    mode: int, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('api_key = "sekrit"\n')
    path.chmod(mode)

    cli.load_config(path)

    err = capsys.readouterr().err
    assert f"chmod 600 {path}" in err
    assert "sekrit" not in err


@posix_only
@pytest.mark.parametrize(
    ("body", "mode"),
    [('api_key = "sekrit"\n', 0o600), ('api_key = ""\n', 0o644), ('alias = "a@b.io"\n', 0o644)],
)
def test_private_or_keyless_config_is_quiet(
    body: str, mode: int, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Without a key in the file there is nothing to protect, so no nagging."""
    path = tmp_path / "config.toml"
    path.write_text(body)
    path.chmod(mode)

    cli.load_config(path)

    assert capsys.readouterr().err == ""


@posix_only
def test_init_config_overwrite_tightens_a_loose_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('api_key = "old"\n')
    path.chmod(0o644)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    assert cli.cmd_init_config(path) == 0
    assert path.stat().st_mode & 0o777 == 0o600
    assert 'api_key = ""' in path.read_text()


def test_init_config_failure_keeps_the_old_file_and_no_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('api_key = "keep-me"\n')
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    def fail(_src: object, _dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)

    assert cli.cmd_init_config(path) == 1
    assert path.read_text() == 'api_key = "keep-me"\n'
    assert [p.name for p in tmp_path.iterdir()] == ["config.toml"]


@posix_only
def test_init_config_writes_through_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dotfile managers symlink configs; replacing the link would silently detach it."""
    real = tmp_path / "dotfiles" / "config.toml"
    real.parent.mkdir()
    real.write_text("")
    link = tmp_path / "config.toml"
    link.symlink_to(real)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    assert cli.cmd_init_config(link) == 0
    assert link.is_symlink()
    assert 'base_url = "https://app.simplelogin.io"' in real.read_text()
    assert real.stat().st_mode & 0o777 == 0o600


def test_init_config_refuses_to_clobber_without_a_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text("alias = 'keep@me.io'\n")

    def no_stdin(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", no_stdin)

    assert cli.cmd_init_config(path) == 1
    assert "keep@me.io" in path.read_text()


def test_config_dir_honours_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg")

    assert cli.get_config_dir() == Path("/tmp/xdg/proton-alias-send")


@pytest.mark.parametrize(("system", "var"), [("Linux", "XDG_CONFIG_HOME"), ("Windows", "APPDATA")])
def test_empty_config_base_counts_as_unset(
    system: str, var: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty value must not put the config, and later the key, in the current directory."""
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv(var, "")

    assert cli.get_config_dir().is_absolute()
    assert tmp_path in cli.get_config_dir().parents


def test_boolean_flags_have_no_duplicate_negations() -> None:
    """BooleanOptionalAction generates --no-*; spelling it out too yields --no-no-copy."""
    parser = cli.build_parser(Path("/tmp/config.toml"))
    options = [opt for action in parser._actions for opt in action.option_strings]

    assert options.count("--no-copy") == 1
    assert "--no-no-copy" not in options
    assert "--no-no-quiet" not in options


@pytest.mark.parametrize(
    ("argv", "copy", "quiet"),
    [
        ([], None, None),
        (["--copy"], True, None),
        (["--no-copy"], False, None),
        (["-q"], None, True),
        (["--no-quiet"], None, False),
    ],
)
def test_boolean_flags_resolve(argv: list[str], copy: bool | None, quiet: bool | None) -> None:
    parser = cli.build_parser(Path("/tmp/config.toml"))
    args = parser.parse_args([*argv, "boss@example.com"])

    assert args.copy is copy
    assert args.quiet is quiet


def test_unset_flags_default_to_none_so_config_can_win() -> None:
    """merge() relies on argparse defaults being None rather than real values."""
    parser = cli.build_parser(Path("/tmp/config.toml"))
    args = parser.parse_args(["boss@example.com"])

    for attr in ("alias", "api_key", "base_url", "copy", "quiet", "config"):
        assert getattr(args, attr) is None, attr


def test_version_flag_exits_cleanly(capsys: pytest.CaptureFixture) -> None:
    parser = cli.build_parser(Path("/tmp/config.toml"))

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])

    assert exc.value.code == 0
    assert cli.APP_NAME in capsys.readouterr().out


def test_parser_is_usable_without_a_contact() -> None:
    """CONTACT_EMAIL is validated in run() so it can be reported alongside other errors."""
    parser = cli.build_parser(Path("/tmp/config.toml"))
    args = parser.parse_args(["--init-config"])

    assert isinstance(args, argparse.Namespace)
    assert args.contact is None
