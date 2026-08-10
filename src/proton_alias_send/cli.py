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
import json
import os
import platform
import stat
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

APP_NAME = "proton-alias-send"
DEFAULT_BASE_URL = "https://app.simplelogin.io"

# Seconds to wait on any single API call before giving up.
REQUEST_TIMEOUT = 30

# Safety net for paginating aliases: a server that ignores page_id would
# otherwise loop forever. 20 pages is ~4000 aliases at the default page size.
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
# Get it at: https://app.simplelogin.io/dashboard/setting
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
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        # Linux, macOS, BSDs - honour XDG_CONFIG_HOME
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
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
    except tomllib.TOMLDecodeError as exc:
        sys.exit(f"Error parsing config file {config_path}: {exc}")

    unknown = set(config.keys()) - KNOWN_CONFIG_KEYS
    if unknown:
        print(
            f"Warning: unknown config key(s) will be ignored: {', '.join(sorted(unknown))}",
            file=sys.stderr,
        )
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
        config_path.write_text(
            SAMPLE_CONFIG.format(config_path=config_path, base_url=DEFAULT_BASE_URL),
            encoding="utf-8",
        )
        if platform.system() != "Windows":
            config_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
            print(f"Created {config_path}  (permissions: 600)", file=sys.stderr)
        else:
            print(f"Created {config_path}", file=sys.stderr)
    except OSError as exc:
        print(f"Error writing config file {config_path}: {exc}", file=sys.stderr)
        return 1

    print("Edit the file to add your API key and default alias.", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# SimpleLogin API
# ---------------------------------------------------------------------------

def _headers(api_key: str) -> dict[str, str]:
    return {"Authentication": api_key, "Content-Type": "application/json"}


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
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(api_key), method=method)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        error_body = exc.read()
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

    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        snippet = raw[:200].decode(errors="replace").strip()
        raise RuntimeError(f"Non-JSON response from {url}: {snippet!r}") from None

    if not isinstance(decoded, dict):
        raise RuntimeError(f"Unexpected response from {url}: expected an object, got {decoded!r}")
    return decoded


def find_alias_id(base_url: str, api_key: str, alias_email: str) -> int | None:
    """Paginate through all aliases and return the ID for alias_email, or None."""
    alias_email = alias_email.lower().strip()
    base_url = base_url.rstrip("/")
    for page in range(MAX_ALIAS_PAGES):
        data = _request("GET", f"{base_url}/api/v2/aliases?page_id={page}", api_key)
        aliases = data.get("aliases") or []
        if not aliases:
            return None
        for alias in aliases:
            if str(alias.get("email", "")).lower() == alias_email:
                alias_id = alias.get("id")
                if alias_id is None:
                    raise RuntimeError(f"Alias '{alias_email}' returned without an id field.")
                return int(alias_id)
    raise RuntimeError(
        f"Gave up after {MAX_ALIAS_PAGES} pages of aliases without finding "
        f"'{alias_email}'. Pass -a with the exact alias address."
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
    reverse_alias = data.get("reverse_alias")
    if not isinstance(reverse_alias, str) or not reverse_alias:
        raise RuntimeError(f"Unexpected API response (no reverse_alias field): {data}")
    return reverse_alias


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
    errors: list[str] = []
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

    print(reverse_alias)

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
        return 0


if __name__ == "__main__":
    sys.exit(main())
