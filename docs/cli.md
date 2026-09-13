# CLI contract

`python -m gaincheck` and the installed `gaincheck` command use Python 3.11+
standard library only.

Every report begins with a limitation sentence: file measurements or recorded
estimates do not establish semantic equivalence, token outcomes, billing, or
causality. `--json` emits one canonical JSON document on stdout. Diagnostics
never include paths, commands, marker values, SQL, or captured text.

```text
gaincheck compare --baseline BASELINE --candidate CANDIDATE [--claimed-baseline FILE] [--require MARKER ...] [--json]
gaincheck check MANIFEST [--json]
gaincheck rtk --db DATABASE [--json]
gaincheck demo [--json]
```

Exit codes are `0` for a complete report without configured findings, `1` for
findings, `2` for invalid or missing input, and `3` for operational failures.
`demo` returns `0` only after its expected synthetic assertions pass.

`--claimed-baseline` is a second supplied file. Its hash and measurements are
reported separately; a differing hash or byte count produces an attribution
warning. It is never treated as proof of an agent-visible baseline.

The RTK adapter accepts only a quiescent rollback-journal database or standalone
backup with the pinned `commands` schema. It rejects WAL headers and journal
companions before opening SQLite. It audits recorded arithmetic and reports
causality as unknown.
