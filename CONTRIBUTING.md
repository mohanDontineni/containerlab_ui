# Contributing

Thank you for improving ContainerLab Studio. Open an issue before a large architectural change so design and compatibility expectations are clear.

## Development checks

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend test
pnpm --dir frontend build
```

Keep normal operator workflows GUI-only. New runtime actions must enforce project authorization, validate all input server-side, use idempotent asynchronous jobs where appropriate, redact secrets, and add audit evidence. Add backend and frontend tests for behavioral changes. Never commit network operating-system images, credentials, private configurations, PCAPs, or customer data.

By submitting a contribution, you agree that it is licensed under the repository's Apache-2.0 license and that you have the right to contribute it.
