#!/usr/bin/env python3
"""
proton-alias-send - Create a Proton Pass / SimpleLogin reverse alias
(contact) for sending email from an alias without going through the dashboard.

Config file (create with --init-config):
  Linux/macOS : ~/.config/proton-alias-send/config.toml  (or $XDG_CONFIG_HOME)
  Windows     : %APPDATA%\\proton-alias-send\\config.toml

Usage:
  proton-alias-send --init-config                 # first-time setup
  proton-alias-send recipient@example.com         # alias + key come from config
  proton-alias-send -a alias@sl.io --copy recipient@example.com

The reverse alias is written to stdout; progress/status goes to stderr, so
the result can be piped or captured cleanly.

Requires Python 3.11+ (stdlib tomllib).
"""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import platform
import stat
import subprocess
import sys
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

APP_NAME = "proton-alias-send"
DEFAULT_BASE_URL = "https://app.simplelogin.io"

# Seconds to wait on any single API call before giving up.
REQUEST_TIMEOUT = 30

# Safety net for paginating alias search results: a server that ignores page_id
# would otherwise loop forever. The API returns 20 aliases per page, so this is
# 400 results, which is plenty because the search narrows them to near-matches.
MAX_ALIAS_PAGES = 20

# Keys recognised in the config file.
KNOWN_CONFIG_KEYS = {"api_key", "alias", "base_url", "copy", "quiet"}

SAMPLE_CONFIG = """\
# proton-alias-send configuration
# Run `proton-alias-send --init-config` to recreate this file.
#
# Tip: restrict read access after creation (already done on Unix):
#   chmod 600 {config_path}

# Your SimpleLogin API key (required if not passed via --api-key).
# Get it at: https://app.simplelogin.io/dashboard/api_key
# Leave empty to fall back to the $SIMPLELOGIN_API_KEY environment variable.
api_key = ""

# Default alias to send from. Override per-invocation with -a / --alias.
# alias = "you@simplelogin.io"

# SimpleLogin API base URL. Change only for self-hosted instances.
base_url = "{base_url}"

# Copy the reverse alias to the clipboard automatically after printing.
copy = false

# Suppress progress messages (stderr). Only the reverse alias is printed.
quiet = false
"""


# ---------------------------------------------------------------------------
# Config path
# ---------------------------------------------------------------------------

def get_config_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        # Linux, macOS, BSDs - honour XDG_CONFIG_HOME. Empty counts as unset, per
        # the XDG spec; otherwise the config would land in the current directory.
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME


def get_config_path() -> Path:
    return get_config_dir() / "config.toml"


# ---------------------------------------------------------------------------
# Config loading and merging
# ---------------------------------------------------------------------------

def load_config(config_path: Path) -> dict[str, Any]:
    """Load and return the TOML config, or {} if the file doesn't exist."""
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "rb") as fh:
            config = tomllib.load(fh)
    except OSError as exc:
        sys.exit(f"Error reading config file {config_path}: {exc}")
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        sys.exit(f"Error parsing config file {config_path}: {exc}")

    unknown = set(config.keys()) - KNOWN_CONFIG_KEYS
    if unknown:
        print(
            f"Warning: unknown config key(s) will be ignored: {', '.join(sorted(unknown))}",
            file=sys.stderr,
        )
    _warn_if_exposed(config_path, config)
    return config


def merge(cli_val: Any, config_val: Any, fallback: Any) -> Any:
    """
    Return the highest-priority set value: CLI > config > env/hardcoded default.

    An empty string counts as unset so that the `api_key = ""` line in the
    generated sample config still falls through to $SIMPLELOGIN_API_KEY.
    """
    for value in (cli_val, config_val):
        if value is not None and value != "":
            return value
    return fallback


# ---------------------------------------------------------------------------
# Config initialisation
# ---------------------------------------------------------------------------

def _write_private(path: Path, text: str) -> None:
    """
    Atomically replace path with text, readable only by the owner on Unix.

    mkstemp creates the file with mode 600 from the start, so the content is never
    exposed under looser permissions, and os.replace means a failed write leaves
    any existing config intact instead of truncated. A symlinked config (as dotfile
    managers create) is resolved first so the link survives and its target is written.
    """
    target = path.resolve()
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _warn_if_exposed(config_path: Path, config: dict[str, Any]) -> None:
    """Warn when a config holding an API key can be read or changed by other users."""
    if platform.system() == "Windows" or not config.get("api_key"):
        return
    try:
        mode = stat.S_IMODE(config_path.stat().st_mode)
    except OSError:
        return
    if mode & 0o077:
        print(
            f"Warning: {config_path} contains your API key but other users can access it "
            f"(mode {mode:o}). Fix with: chmod 600 {config_path}",
            file=sys.stderr,
        )


def cmd_init_config(config_path: Path) -> int:
    """Write a sample config file and set restrictive permissions on Unix."""
    if config_path.exists():
        try:
            answer = input(
                f"Config already exists at {config_path}. Overwrite? [y/N] "
            ).strip().lower()
        except EOFError:
            # Non-interactive stdin: refuse rather than clobber the file.
            print(
                f"Error: config already exists at {config_path} and stdin is not "
                "interactive. Remove the file first to recreate it.",
                file=sys.stderr,
            )
            return 1
        if answer != "y":
            print("Aborted.", file=sys.stderr)
            return 0

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        _write_private(
            config_path,
            SAMPLE_CONFIG.format(config_path=config_path, base_url=DEFAULT_BASE_URL),
        )
    except OSError as exc:
        print(f"Error writing config file {config_path}: {exc}", file=sys.stderr)
        return 1
    if platform.system() != "Windows":
        print(f"Created {config_path}  (permissions: 600)", file=sys.stderr)
    else:
        print(f"Created {config_path}", file=sys.stderr)

    print("Edit the file to add your API key and default alias.", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# SimpleLogin API
# ---------------------------------------------------------------------------

def _require_http_url(url: str) -> None:
    """Reject anything but http(s), which urllib would otherwise happily open (e.g. file://)."""
    try:
        scheme = urllib.parse.urlsplit(url).scheme.lower()
    except ValueError:
        scheme = ""
    if scheme not in ("http", "https"):
        raise RuntimeError(f"Invalid base URL in {url}: it must start with https:// or http://")


def _error_text(payload: Any) -> str:
    """Pull a human-readable message out of whatever the API returned."""
    if isinstance(payload, dict):
        for key in ("error", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return str(payload)


def _request(
    method: str, url: str, api_key: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Perform a JSON API call and return the decoded object.

    Every failure mode - HTTP error, transport error, malformed body - is
    normalised into RuntimeError so callers have a single thing to catch.
    """
    _require_http_url(url)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method=method
    )
    # Unredirected, so a redirect can't carry the key to another host. SimpleLogin
    # spells this header `Authentication`, not `Authorization`.
    req.add_unredirected_header("Authentication", api_key)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            error_body = exc.read()
        except (OSError, http.client.HTTPException):
            error_body = b""
        try:
            payload = json.loads(error_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = error_body.decode(errors="replace").strip()
        hint = ""
        if exc.code == 401:
            hint = " (check your API key)"
        raise RuntimeError(f"HTTP {exc.code} from {url}: {_error_text(payload)}{hint}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from None
    except TimeoutError:
        raise RuntimeError(f"Timed out after {REQUEST_TIMEOUT}s waiting for {url}") from None
    except (OSError, http.client.HTTPException) as exc:
        raise RuntimeError(f"Could not reach {url}: {exc}") from None
    except ValueError:
        # http.client quotes the offending header in this exception, so never
        # include it here: the bad value is usually the API key.
        raise RuntimeError(
            f"Could not send the request to {url}: the API key or URL contains "
            "invalid characters."
        ) from None

    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        snippet = raw[:200].decode(errors="replace").strip()
        raise RuntimeError(f"Non-JSON response from {url}: {snippet!r}") from None

    if not isinstance(decoded, dict):
        raise RuntimeError(f"Unexpected response from {url}: expected an object, got {decoded!r}")
    return decoded


def find_alias_id(base_url: str, api_key: str, alias_email: str) -> int | None:
    """Search the account's aliases and return the ID for alias_email, or None."""
    alias_email = alias_email.lower().strip()
    base_url = base_url.rstrip("/")
    for page in range(MAX_ALIAS_PAGES):
        # The query is a loose server-side match, so results still need the exact
        # comparison below. POST is the documented way to send it in a body.
        data = _request(
            "POST",
            f"{base_url}/api/v2/aliases?page_id={page}",
            api_key,
            body={"query": alias_email},
        )
        aliases = data.get("aliases") or []
        if not aliases:
            return None
        if not isinstance(aliases, list):
            raise RuntimeError(f"Unexpected API response (aliases is not a list): {data}")
        for alias in aliases:
            if not isinstance(alias, dict):
                raise RuntimeError(f"Unexpected API response (malformed alias): {alias!r}")
            if str(alias.get("email", "")).lower() == alias_email:
                try:
                    return int(alias["id"])
                except (KeyError, TypeError, ValueError):
                    raise RuntimeError(
                        f"Alias '{alias_email}' returned without a valid id field."
                    ) from None
    raise RuntimeError(
        f"Gave up after {MAX_ALIAS_PAGES} pages of search results without finding "
        f"'{alias_email}'. Check the alias address for typos."
    )


def create_contact(base_url: str, api_key: str, alias_id: int, contact_email: str) -> str:
    """
    Create a contact for alias_id and return the reverse alias address.
    Idempotent: returns the existing reverse alias if the contact already exists.
    """
    data = _request(
        "POST",
        f"{base_url.rstrip('/')}/api/aliases/{alias_id}/contacts",
        api_key,
        body={"contact": contact_email},
    )
    # Not `reverse_alias`, which is a display string: "Name a@b.c <ra+x@sl.co>".
    address = data.get("reverse_alias_address")
    if not isinstance(address, str) or not address:
        raise RuntimeError(f"Unexpected API response (no reverse_alias_address field): {data}")
    return address


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------

def copy_to_clipboard(text: str) -> bool:
    """Try to copy text to the system clipboard. Returns True on success."""
    system = platform.system()
    candidates: list[list[str]] = []
    if system == "Darwin":
        candidates = [["pbcopy"]]
    elif system == "Linux":
        candidates = [
            ["wl-copy"],  # Wayland
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ]
    elif system == "Windows":
        candidates = [["clip"]]

    for cmd in candidates:
        try:
            subprocess.run(
                cmd, input=text.encode(), check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        except (FileNotFoundError, OSError, subprocess.CalledProcessError):
            continue
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(default_config_path: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description=(
            "Create a SimpleLogin reverse alias so you can send email from an alias "
            "to a new contact without touching the dashboard."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""\
config file (default): {default_config_path}
  Run --init-config to create it. CLI flags always override config values.

examples:
  # First-time setup
  proton-alias-send --init-config

  # Minimal - alias, api_key, and other settings come from config
  proton-alias-send boss@example.com

  # Override the alias just for this invocation
  proton-alias-send -a work@simplelogin.io boss@example.com

  # Force clipboard copy regardless of config setting
  proton-alias-send --copy boss@example.com

  # Suppress config's copy=true for one run
  proton-alias-send --no-copy boss@example.com

  # Pipe the result directly (quiet suppresses progress noise on stderr)
  open "mailto:$(proton-alias-send -q boss@example.com)"

  # Use a non-default config file
  proton-alias-send --config ~/work-sl.toml boss@example.com
""",
    )

    from proton_alias_send import __version__

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="Write a sample config file to the default path (or --config path) and exit.",
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help=f"Path to config file. Default: {default_config_path}",
    )
    parser.add_argument(
        "contact",
        metavar="CONTACT_EMAIL",
        nargs="?",
        default=None,
        help="Recipient address to create a contact (reverse alias) for.",
    )
    parser.add_argument(
        "-a", "--alias",
        default=None,
        metavar="EMAIL",
        help="SimpleLogin alias to send from. Overrides config 'alias'.",
    )
    parser.add_argument(
        "-k", "--api-key",
        default=None,
        metavar="KEY",
        help="SimpleLogin API key. Overrides config 'api_key' and $SIMPLELOGIN_API_KEY.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        metavar="URL",
        help=f"SimpleLogin base URL. Overrides config 'base_url'. Default: {DEFAULT_BASE_URL}",
    )
    # BooleanOptionalAction generates the --no-* form itself; spelling it out
    # here too would register duplicate and --no-no-* options.
    parser.add_argument(
        "--copy",
        dest="copy",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Copy the reverse alias to clipboard. Overrides config 'copy'.",
    )
    parser.add_argument(
        "-q", "--quiet",
        dest="quiet",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Suppress progress messages. Overrides config 'quiet'.",
    )
    return parser


def log(msg: str, quiet: bool) -> None:
    if not quiet:
        print(msg, file=sys.stderr)


def _config_type_errors(strings: dict[str, Any], booleans: dict[str, Any]) -> list[str]:
    """Report wrongly typed settings by name. Values stay out of it: one may be the API key."""
    errors = [
        f"Setting '{name}' must be a string."
        for name, value in strings.items()
        if value is not None and not isinstance(value, str)
    ]
    errors += [
        f"Setting '{name}' must be true or false."
        for name, value in booleans.items()
        if not isinstance(value, bool)
    ]
    return errors


def _is_plaintext_remote(base_url: str) -> bool:
    """True for an http:// URL off this machine, where the API key travels unencrypted."""
    try:
        parts = urllib.parse.urlsplit(base_url)
        host = parts.hostname or ""
    except ValueError:
        return False
    if parts.scheme.lower() != "http" or host.lower() == "localhost":
        return False
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True


def run(argv: list[str] | None = None) -> int:
    default_config_path = get_config_path()
    parser = build_parser(default_config_path)
    args = parser.parse_args(argv)

    config_path = Path(args.config).expanduser() if args.config else default_config_path

    if args.init_config:
        return cmd_init_config(config_path)

    config = load_config(config_path)

    # Resolve each setting: CLI > config file > env var / hardcoded default
    api_key  = merge(args.api_key,  config.get("api_key"),  os.environ.get("SIMPLELOGIN_API_KEY"))
    alias    = merge(args.alias,    config.get("alias"),    None)
    base_url = merge(args.base_url, config.get("base_url"), DEFAULT_BASE_URL)
    do_copy  = merge(args.copy,     config.get("copy"),     False)
    quiet    = merge(args.quiet,    config.get("quiet"),    False)

    # Validate - collect all errors before printing so the user can fix them in one go
    errors = _config_type_errors(
        strings={"api_key": api_key, "alias": alias, "base_url": base_url},
        booleans={"copy": do_copy, "quiet": quiet},
    )
    if isinstance(api_key, str):
        api_key = api_key.strip()
    if not args.contact:
        errors.append("CONTACT_EMAIL is required.")
    if not api_key:
        errors.append(
            "No API key found. Set 'api_key' in the config file, use -k / --api-key, "
            "or set $SIMPLELOGIN_API_KEY."
        )
    if not alias:
        errors.append(
            "No alias specified. Set 'alias' in the config file or use -a / --alias."
        )
    if errors:
        for msg in errors:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    if _is_plaintext_remote(base_url):
        print(
            f"Warning: {base_url} is not HTTPS, so your API key is sent unencrypted.",
            file=sys.stderr,
        )

    try:
        log(f"Looking up alias ID for {alias} ...", quiet)
        alias_id = find_alias_id(base_url, api_key, alias)
        if alias_id is None:
            print(
                f"Error: alias '{alias}' not found in your SimpleLogin account.",
                file=sys.stderr,
            )
            return 1
        log(f"  Found alias ID: {alias_id}", quiet)

        log(f"Creating contact for {args.contact} ...", quiet)
        reverse_alias = create_contact(base_url, api_key, alias_id, args.contact)
        log("  Done.", quiet)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # Flush here so a closed pipe surfaces inside main(), not at interpreter exit.
    print(reverse_alias, flush=True)

    if do_copy:
        if copy_to_clipboard(reverse_alias):
            log("Copied to clipboard.", quiet)
        else:
            print(
                "Warning: clipboard copy failed. No supported tool found "
                "(pbcopy / xclip / xsel / wl-copy / clip).",
                file=sys.stderr,
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point."""
    try:
        return run(argv)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # e.g. `proton-alias-send ... | head` - exit quietly like other CLI tools.
        # Point stdout at devnull so the flush at interpreter exit can't fail too.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
        return 0


if __name__ == "__main__":
    sys.exit(main())
