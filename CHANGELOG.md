# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-11-07

### Added

- Warning when a config file holding an API key is readable or writable by other users.
- Python 3.15 support.
- `--copy` on FreeBSD, OpenBSD, NetBSD, and other BSDs, via `wl-copy`, `xclip`, or `xsel`.
- `SECURITY.md`, describing how to report vulnerabilities privately.

### Changed

- `--init-config` writes the file atomically with mode `600` from the start. A failed write leaves
  any existing config untouched, and a symlinked config keeps its link.
- Each clipboard tool gets 5 seconds. One that hangs (say, `xclip` with no reachable display) is
  skipped for the next, rather than stalling the CLI after the result is printed.

## [0.1.0] - 2026-11-07

Initial release to PyPi.

### Added

- `proton-alias-send CONTACT_EMAIL` creates a SimpleLogin reverse alias and prints it to
  stdout.
- TOML config file with `--init-config`, created mode `600` on Unix.
- Setting resolution: command line > config file > `$SIMPLELOGIN_API_KEY` / defaults.
- `--copy/--no-copy` clipboard support via `pbcopy`, `wl-copy`, `xclip`, `xsel`, or `clip`.
- `--base-url` for self-hosted SimpleLogin instances.
- `find_alias_id` and `create_contact` are importable for use as a library.

[Unreleased]: https://github.com/fictiontoreality/proton-alias-send/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/fictiontoreality/proton-alias-send/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/fictiontoreality/proton-alias-send/releases/tag/v0.1.0
