# AGENTS.md

This file provides guidance to AI agents.

## Commands

The project is managed with `uv` (Python 3.11 pinned via `.python-version`).

```bash
uv sync                                       # create/update the venv incl. dev tools
uv run proton-alias-send --help               # run the CLI from the working tree
uv run pytest -q                              # full test suite (no network)
uv run pytest tests/test_cli.py::test_quiet_silences_progress_but_not_the_result  # one test
uv run ruff check .                           # lint (formatting is not enforced)
uv run mypy                                   # strict type check (config picks up src + tests)
uv build && uvx twine check dist/*            # build and validate the PyPI artifacts
```

`dependencies` in `pyproject.toml` is intentionally empty — the tool is stdlib-only
(`urllib.request`, `tomllib`, `argparse`) so it installs anywhere with Python 3.11+ and no
resolution risk. Keep it that way; reach for the stdlib before adding a dependency.

Releases go out via `.github/workflows/release.yml` on a `v*` tag, using PyPI trusted
publishing. The workflow fails the build if the tag doesn't match `__version__`, so bump
`src/proton_alias_send/__init__.py` and `CHANGELOG.md` together before tagging.

## Architecture

`src/proton_alias_send/cli.py` is the whole implementation: it creates a
SimpleLogin/Proton Pass *reverse alias* (a "contact") so mail can be sent from an alias
without opening the web dashboard. Two API calls do the work:

1. `find_alias_id` — `POST /api/v2/aliases?page_id=N` with `{"query": alias}` until an empty
   page, matching the alias email exactly (case-insensitive) on the client. SimpleLogin has
   no lookup-by-email endpoint, and the query is only a loose search, so the exact match is
   required. Pages hold 20 aliases; `MAX_ALIAS_PAGES` caps the search at 400 results so a
   self-hosted server that ignores `page_id` can't spin forever.
2. `create_contact` — `POST /api/aliases/{id}/contacts`, returns `reverse_alias_address`. Do
   not use `reverse_alias`: that is a display string (`Name a@b.c <ra+x@sl.co>`). Idempotent
   server-side: an existing contact returns the same address.

Auth is the `Authentication` header, not `Authorization`. It is set with
`add_unredirected_header` so a redirect can't carry the API key to another host; don't move
it into the ordinary request headers.

`find_alias_id` and `create_contact` are re-exported from the package root and are part of
the public API — keep their signatures stable.

### Error handling

Every failure mode is funnelled through one type. `_request` converts HTTP errors, transport
errors, timeouts, non-JSON bodies, and non-object JSON into `RuntimeError` with a readable
message, so `run()` needs exactly one `except RuntimeError` to keep tracebacks off the
user's terminal. Never echo a rejected value into these messages: `http.client` quotes bad
header values in its exceptions, and that value is usually the API key. New API calls should go through `_request` rather than calling `urlopen`
directly. `main()` wraps `run()` only to turn `KeyboardInterrupt` into exit 130 and swallow
`BrokenPipeError`.

### Settings resolution

Every setting flows through `merge(cli, config, fallback)` — **CLI > config file >
env/hardcoded default**. `merge` skips `None` *and* empty string (the generated config ships
`api_key = ""`, which must not shadow `$SIMPLELOGIN_API_KEY`) but deliberately keeps `False`,
so `copy = false` still beats a truthy default.

This depends on every argparse default being `None` rather than a real value — there's a
test enforcing that. Booleans use `argparse.BooleanOptionalAction`, which generates the
`--no-*` form itself; don't also list `"--no-copy"` in `add_argument`, or you get duplicate
and `--no-no-copy` options.

Adding a setting means: add it to `KNOWN_CONFIG_KEYS` (unknown keys warn), document it in
`SAMPLE_CONFIG`, add the argparse flag with `default=None`, add a `merge` line in `run`, and
add it to the `_config_type_errors` call (TOML values can be any type).

Config lives at `$XDG_CONFIG_HOME/proton-alias-send/config.toml` (`%APPDATA%` on Windows)
and is chmod 600 on Unix at creation since it holds the API key.

### Output contract

The reverse alias is the **only** thing on stdout; all progress and errors go to stderr via
`log()` / `print(file=sys.stderr)`. This is what makes
`open "mailto:$(proton-alias-send -q boss@example.com)"` work, and tests assert it. Don't
print anything else to stdout.

`run()` returns an exit code rather than calling `sys.exit`, which is what makes it testable
in-process; validation collects all errors into a list before returning 1 so the user sees
every problem at once.

## Tests

No network access — `tests/test_api.py` monkeypatches `urllib.request.urlopen` with a
`FakeResponse`, and `tests/test_cli.py` reuses it plus an autouse fixture that points the
config path at `tmp_path` and clears `$SIMPLELOGIN_API_KEY`. Keep that isolation: a test
that hits the real API or reads the developer's real config is a bug.
