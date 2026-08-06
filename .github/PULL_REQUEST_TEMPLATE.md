<!-- Thanks for contributing to biMEMO! Keep this PR focused and reviewable. -->

## Summary

<!-- What does this change do, and why? -->

Closes #

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor / cleanup
- [ ] Documentation
- [ ] CI / tooling

## How was this tested?

<!-- Commands run, new/updated tests, manual verification. -->

- [ ] `ruff check` passes
- [ ] Test suite passes (`pytest`)
- [ ] Frontend lints/builds (if touched)

## Security & trust boundaries

<!-- biMEMO handles an encrypted store and credentials across isolated
     processes. Confirm this change respects those boundaries. -->

- [ ] No secrets, tokens, or `.env` contents are committed
- [ ] Memory-content writes still route through the orchestrator (Socket A);
      the web tier (Socket B) is not given write or credential access
- [ ] Credential/integration access still goes through the credential proxy
- [ ] N/A — this change doesn't touch process/socket boundaries

## Checklist

- [ ] The change is self-contained and the description explains the reasoning
- [ ] Docs updated if behavior or configuration changed
