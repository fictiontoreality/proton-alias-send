# Security policy

`proton-alias-send` handles your SimpleLogin API key, which has full control over your aliases.
Security reports are taken seriously, and thank you for making one.

## Supported versions

Only the latest release on [PyPI](https://pypi.org/project/proton-alias-send/) gets security
fixes. Please check that a problem still happens on it before reporting.

## Reporting a vulnerability

**Please don't open a public issue for a security problem.** Report it privately instead:

Use GitHub's [private vulnerability reporting](https://github.com/fictiontoreality/proton-alias-send/security/advisories/new).

Include what you can of:

- the version (`proton-alias-send --version`), Python version, and operating system
- steps to reproduce, or a proof of concept
- what an attacker could do with it

**Never include a real API key.** If you think yours has been exposed, revoke it in the
[SimpleLogin dashboard](https://app.simplelogin.io/dashboard/api_key) first.

## What to expect

This is a one-person project, so timings are best effort:

- an acknowledgement within a week
- a fix or a decision as soon as practical, depending on severity
- credit in the release notes and security advisory, unless you'd rather stay anonymous

Please give a reasonable chance to release a fix before disclosing publicly.

## Scope

In scope: problems in this tool's own code, such as leaking the API key (in output, logs, files,
or network requests), sending it somewhere other than the configured server, or unsafe handling of
the config file.

Out of scope, so please report these upstream instead:

- **SimpleLogin or Proton services:** [Proton's bug bounty programme](https://proton.me/security/bug-bounty).
- **Python or its standard library:** the [Python security team](https://www.python.org/dev/security/).
