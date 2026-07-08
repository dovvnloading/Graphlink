# Security Policy

## Supported Versions

Security fixes are expected to land on the current main development line. Older snapshots, experimental branches, and local forks should not be assumed to receive coordinated security updates.

## Reporting a Vulnerability

If you discover a security issue in Graphlink:

1. Do not publish exploit details in a public GitHub issue first.
2. Prefer GitHub private security reporting or a private maintainer contact path if one is available for the repository.
3. Include a clear description of the issue, impact, affected area, and reproduction steps.
4. If possible, include a proof of concept that is safe, minimal, and does not expose third-party secrets.

## Scope Notes

Areas that deserve extra care in this project include:

- local storage of API keys and GitHub tokens
- execution-oriented plugins such as Execution Sandbox and Py-Coder
- GitHub-backed repository access
- provider configuration and outbound API requests
- file import and export flows

## Disclosure Expectations

Please allow the maintainer a reasonable window to reproduce the issue, assess impact, and prepare a fix before public disclosure.
