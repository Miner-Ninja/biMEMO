# Security Policy

## Supported Versions

Security fixes are applied to the current development branch and the latest
published release.

## Reporting a Vulnerability

Do not open a public GitHub issue for a security problem.

Report security issues through
[GitHub Private Vulnerability Reporting](https://github.com/Miner-Ninja/biMEMO/security/advisories/new).

Include the following details when possible:

- affected version, branch, or commit hash,
- clear reproduction steps,
- expected behavior and actual behavior,
- impact and whether the issue can expose user data,
- logs, screenshots, or request samples if they help reproduce the issue.

## Response Expectations

- Initial acknowledgment target: within 7 days.
- Status updates target: at least once every 14 days while the report is open.
- Fix timing depends on severity, exploitability, and release risk.

## Scope

The following areas are especially sensitive in this project:

- encryption keys and encrypted data handling,
- authentication, JWT signing, and session handling,
- credential storage and admin configuration,
- tool execution boundaries and network access controls,
- Docker, Caddy, and local HTTPS setup.
