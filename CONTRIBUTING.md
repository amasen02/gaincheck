# Contributing to GainCheck

Thanks for helping improve a small, local evidence tool. Runtime code is Python 3.11+ standard library code. Please keep changes deterministic, offline by default, and within the documented [CLI contract](docs/cli.md) and [report schema](docs/schema.md).

## Local checks

```text
python -m venv .venv
# activate .venv for your shell
python -m pip install -e .
python -m unittest discover -s tests
python -m compileall -q gaincheck
python -m gaincheck demo
python -m gaincheck demo --json
```

Use a fresh virtual environment when checking packaging. `python -m build` must produce a wheel and source distribution without bundling workspace files. `scripts/build_release.py` additionally builds the Python zipapp and writes checksums and the source revision receipt.

## Good contribution areas

These are concrete compatibility gaps that can be addressed with fixtures and acceptance checks:

1. **Evidence-fixture compatibility:** add a small baseline/candidate corpus for a real output format, with an independent raw-byte/hash/marker oracle and an explanation of what remains unknown.
2. **Manifest portability:** add Windows, macOS, or Linux path and line-ending fixtures that exercise containment, symlink rejection, bounds, and deterministic case ordering.
3. **RTK schema compatibility:** add a pinned upstream schema or supported-version fixture only when its source, types, and provenance are documented. Keep the adapter read-only and preserve the standalone-backup/WAL rejection contract.

Avoid changing labels from bytes to tokens or money without authoritative provenance. Do not add telemetry, network calls, command replay, automatic edits, a score, or a new parser family without a reviewed plan.

## Pull requests

- Explain which evidence contract or named behavior gate changed.
- Add an independent expected receipt for new fixtures; do not use generated tool output as its own oracle.
- Test stdout, stderr, and exit codes for CLI behavior.
- Include privacy and filesystem-side-effect coverage when reading files or SQLite.
- Run the relevant local checks and state any environment-specific limitation.

Please report a security issue privately as described in [SECURITY.md](SECURITY.md). Do not include secrets or private captured output in an issue or pull request.
