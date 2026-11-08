# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/fictiontoreality/proton-alias-send/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/fictiontoreality/proton-alias-send/releases/tag/v0.1.0
