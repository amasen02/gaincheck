# GainCheck

When a filter drops the failure line an agent needed, a smaller output can look like success. GainCheck compares a caller-visible capture with its candidate, highlights declared signals that disappeared, and leaves unsupported conclusions as `unknown`.

GainCheck is a local, deterministic evidence tool for developers who capture a command's baseline output and a filtered candidate. It measures bytes and logical lines, records SHA-256 fingerprints, checks declared literal markers, and reports what the files do not prove. An optional RTK adapter audits recorded counters from a standalone SQLite database; it does not reconstruct missing baselines or certify token, billing, or semantic savings.

File comparison reports begin with: **File measurements only; token, billing and semantic outcomes are unverified.**

## Quickstart from source

Run from source with Python 3.11 or newer; runtime dependencies are limited to the standard library.

```text
git clone https://github.com/amasen02/gaincheck.git
cd gaincheck
python -m gaincheck demo
```

For an isolated editable environment, create and activate a virtual environment, then run `python -m pip install -e .` before using the commands below. The demo is synthetic and self-checking. A successful demo exits `0`; its example findings are expected and do not make the demo fail. To inspect the machine-readable receipt:

```text
python -m gaincheck demo --json
```

Packaged downloads are on the project's [Releases](https://github.com/amasen02/gaincheck/releases) page. Python 3.11 or newer is required; verify `SHA256SUMS` before installing a wheel or Python zipapp.

## Compare captured files

Provide files captured from the same intended operation. The repository includes a synthetic capture pair:

```text
python -m gaincheck compare \
  --baseline examples/fixtures/caller-output.txt \
  --candidate examples/fixtures/candidate-output.txt
```

On Windows PowerShell, use the same command on one line or PowerShell's backtick for continuation. `--require` values are literal markers, not regular expressions. A missing marker in the baseline is invalid input; a marker missing from the candidate is a finding. Marker preservation is only a declared sanity check and does not establish semantic equivalence.

The command reports nested `baseline.bytes` and `candidate.bytes` measurements, plus `bytes_removed` and `byte_reduction_pct`. These are arithmetic over the supplied files. They are not token counts, marginal savings, invoices, or proof that the candidate retained the information an agent needed. `--json` emits a deterministic schema with `provenance: user_supplied`, hashes, counts, findings, and unknown outcomes.

An optional `--claimed-baseline path/to/another-capture.txt` reads a third supplied file and reports its arithmetic separately. A differing hash or byte count produces an attribution warning; a numeric baseline is not accepted.

For repeatable CI cases, use the included synthetic manifest or one you own:

```text
python -m gaincheck check examples/demo-manifest.json
python -m gaincheck check examples/demo-manifest.json --json
```

Manifest paths are resolved beneath the manifest directory, symlinks are checked, regular files are required, and no commands from a manifest are executed. The manifest is bounded to 100 cases, 16 MiB per file, 1 MiB total manifest size, 256 markers per case, and 4096 characters per marker.

The included manifest contains an intentional missing-marker case, so its aggregate result exits `1`. That is an expected finding in the demo corpus.

To run one of its capture cases directly:

```text
python -m gaincheck compare --baseline examples/fixtures/error-baseline.txt --candidate examples/fixtures/error-compact.txt --require "FAIL E42"
```

## Audit an RTK history database

The RTK adapter reads a supplied, quiescent rollback-journal database or standalone SQLite backup:

```text
python -m gaincheck rtk --db examples/rtk/history-standalone.db
python -m gaincheck rtk --db examples/rtk/history-standalone.db --json
```

Close the producer first and use a backup when necessary. GainCheck rejects a database configured for WAL and rejects existing `-wal`, `-shm`, or `-journal` companions before opening it. It never replays commands, enables SQLite extensions, runs SQL from the database, or reads a user history automatically. Recorded RTK counters are reported as upstream estimates; missing caller-window, pipe, or baseline provenance remains `unknown`.

Create a new standalone backup without overwriting an existing destination, then audit that backup:

```text
python -c "from pathlib import Path; import sqlite3; d=Path('history-standalone.db'); assert not d.exists(), 'choose a new destination'; s=sqlite3.connect(Path('history.db').resolve().as_uri()+'?mode=ro', uri=True); t=sqlite3.connect(d); s.backup(t); t.execute('PRAGMA journal_mode=DELETE'); t.close(); s.close()"
python -m gaincheck rtk --db history-standalone.db
```

The backup uses Python [SQLite backup API](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup). Keep the RTK producer closed while creating it, store it in a controlled location, and review the output before sharing hashes or receipts.

## Why this exists

[RTK issue #2805](https://github.com/rtk-ai/rtk/issues/2805) documents a case where caller-side `head` filtering was counted in a compression estimate; the later correction also makes clear that fewer bytes do not prove equal information. Independent research likewise found a setup with 38.4% fewer delivered tool-output tokens but 6.8% higher billed cost ([Token Reduction Is Not Cost Reduction](https://arxiv.org/abs/2607.12161v5)). GainCheck keeps those boundaries visible: it checks supplied file evidence and recorded arithmetic while leaving semantic, token, and billing outcomes `unknown`.

## Exit codes

- `0`: complete report with no configured finding. Unknown attribution can still be present.
- `1`: finding such as a lost marker, recorded arithmetic mismatch, or denominator warning.
- `2`: invalid, missing, malformed, or unsupported input, including an empty or unsupported database.
- `3`: operational failure such as I/O, timeout, lock/concurrent change, or broken pipe.

With `--json`, stdout contains one deterministic JSON report or error envelope. Diagnostics are bounded and scrubbed on stderr. Paths, captured text, commands, marker values, SQL errors, and secrets are not emitted by default. Hashes are fingerprints of bytes, not signatures, anonymization, or provenance attestations.

## Development

```text
python -m unittest discover -s tests
python -m compileall -q gaincheck
python -m build
python scripts/build_release.py
```

The CI matrix runs the tests and fresh-install/demo checks on Windows, Linux, and macOS with Python 3.11 and 3.14. The release helper creates a wheel, source distribution, Python zipapp, `SHA256SUMS`, and a source-revision receipt. It only builds artifacts; publication requires separate human review.

## Scope and limitations

GainCheck intentionally does not provide billing reconciliation, universal agent-session parsing, a cost dashboard, a model judgment, a hosted service, telemetry, automatic filtering, automatic fixes, hooks, or merge decisions. A clean file comparison does not prove semantic equivalence or cost savings. If the evidence is incomplete, the useful result is `unknown`.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [CHANGELOG.md](CHANGELOG.md).
