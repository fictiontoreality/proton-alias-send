# proton-alias-send

[![PyPI](https://img.shields.io/pypi/v/proton-alias-send.svg)](https://pypi.org/project/proton-alias-send/)
[![Python](https://img.shields.io/pypi/pyversions/proton-alias-send.svg)](https://pypi.org/project/proton-alias-send/)
[![License](https://img.shields.io/pypi/l/proton-alias-send.svg)](LICENSE)

Create a Proton Pass / SimpleLogin **reverse alias** from the command line or Python, so you can
send mail *from* one of your aliases without opening the web dashboard.

SimpleLogin aliases forward mail to you out of the box, but replying from an alias to a
brand-new recipient requires creating a "contact" first. The dashboard makes you click
through several screens for this. This tool does it in one command and prints the reverse
alias address you actually put in your mail client's To: field.

```console
$ proton-alias-send boss@example.com
Looking up alias ID for work@simplelogin.io ...
  Found alias ID: 1234567
Creating contact for boss@example.com ...
  Done.
boss_at_example.com_abc123@simplelogin.co
```

Mail sent to `boss_at_example.com_abc123@simplelogin.co` arrives at `boss@example.com`
appearing to come from `work@simplelogin.io`.

## Install

```console
pipx install proton-alias-send
```

or

```console
uv tool install proton-alias-send
```

It has **no dependencies** outside the standard library and needs Python 3.11+.

## Setup

```console
$ proton-alias-send --init-config
```

That writes a commented config file (mode `600` on Unix) to:

| Platform     | Path                                                              |
| ------------ | ----------------------------------------------------------------- |
| Linux / BSD  | `$XDG_CONFIG_HOME/proton-alias-send/config.toml`, else `~/.config/...` |
| macOS        | `~/.config/proton-alias-send/config.toml`                          |
| Windows      | `%APPDATA%\proton-alias-send\config.toml`                          |

Fill in your API key from <https://app.simplelogin.io/dashboard/setting> and the alias you
usually send from:

```toml
api_key = "your-api-key-here"
alias   = "work@simplelogin.io"
copy    = true
```

If you'd rather not keep the key on disk, leave `api_key = ""` and export
the environment variable `SIMPLELOGIN_API_KEY` instead.

## Usage

```console
$ proton-alias-send --help
```

| Flag                | Purpose                                                        |
| ------------------- | -------------------------------------------------------------- |
| `-a`, `--alias`     | Alias to send from, overriding the config default               |
| `-k`, `--api-key`   | API key, overriding config and `$SIMPLELOGIN_API_KEY`           |
| `--base-url`        | Point at a self-hosted SimpleLogin instance                     |
| `--copy/--no-copy`  | Copy the result to the clipboard (`pbcopy`/`wl-copy`/`xclip`/`xsel`/`clip`) |
| `-q`, `--quiet`     | Silence progress output on stderr                               |
| `--config PATH`     | Use an alternate config file                                    |
| `--init-config`     | Write the sample config and exit                                |

Settings resolve as **command line > config file > environment / built-in default**, so any
config value can be overridden for a single run — including booleans, via `--no-copy` and
`--no-quiet`.

### Scripting

The reverse alias is the only thing written to stdout; all progress and errors go to
stderr. That makes it safe to capture:

```console
$ open "mailto:$(proton-alias-send -q boss@example.com)"
```

Exit status is `0` on success, `1` on any error, `130` if interrupted.

Creating a contact is idempotent — running it twice for the same recipient returns the
same reverse alias rather than failing.

### As a library

```python
from proton_alias_send import create_contact, find_alias_id

alias_id = find_alias_id("https://app.simplelogin.io", api_key, "work@simplelogin.io")
reverse = create_contact("https://app.simplelogin.io", api_key, alias_id, "boss@example.com")
```

Both raise `RuntimeError` with a readable message on any API, network, or protocol failure.

## Development

```console
$ uv sync                 # create the venv, including dev tools
$ uv run pytest           # tests
$ uv run pytest tests/test_config.py::test_merge_prefers_cli   # a single test
$ uv run ruff check .     # lint (formatting is deliberately not enforced)
$ uv run mypy             # type check (strict)
$ uv build                # build sdist + wheel into dist/
```

## License

Apache-2.0. See [LICENSE](LICENSE).

Not affiliated with Proton AG or SimpleLogin.
