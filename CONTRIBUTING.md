# Contributing

Thanks for helping build browser automation with local, open-weight models. This project is a work in progress, and contributions are welcome.

## Getting started

Fork the repository, create a branch, and run:

```bash
uv sync --frozen
cp .env.example .env
```

Only live examples need a text-model API key. Offline tests do not load model weights or call paid APIs.

## Checks

```bash
uv run python -m pytest -q
uv run ruff check .
node --check gliner_ultrafast/static/app.js
node --check gliner_ultrafast/snapshot.js
uv build
```

With Chrome connected, optional local-browser checks are available:

```bash
uv run python scripts/check_guards.py
```

These interact with local test controls without model calls. Live examples interact with external websites and can incur text-model charges.

## Useful contributions

Improve control matching, browser semantics, local model support, performance, accessibility or documentation. For a bug report, include a minimal goal, public URL or local fixture, expected outcome, model configuration and relevant redacted trace.

Keep the controller general: avoid task-specific selectors, prepared click sequences or field values in the runtime. Supply task values through the goal, and keep acceptance checks separate from action selection. Include focused regression tests for behavioral changes.

Report evaluation setup and timing boundaries. Keep development cases separate from new evaluation cases, and report all outcomes in evaluation submissions. The initial demo is not a general reliability benchmark.

## Pull requests

Describe the problem, resulting behavior and validation. Keep changes focused and commit messages descriptive. Follow the existing code style and preserve upstream attribution.

Never commit `.env`, credentials, browser profiles, model caches or unreviewed traces. Review exported screenshots and page content before attaching them. Contributions are made under the repository's MIT license.
