# Security policy

GainCheck is designed for local, offline use. It does not require an API key, hosted service, telemetry, model call, or daemon. Treat manifests, captured output, and SQLite databases as untrusted input.

The input boundary is intentionally narrow:

- file paths are bounded and must resolve beneath a manifest directory for batch checks;
- regular files are required and inputs are size-limited;
- captured text and marker values are not printed by default;
- SQLite reads use fixed column queries, read-only mode, `query_only`, and `trusted_schema` disabled;
- extensions, user-provided SQL, command execution, and command replay are not supported;
- WAL-mode databases and existing `-wal`, `-shm`, or `-journal` companions are rejected;
- hashes are byte fingerprints, not signatures, anonymization, or proof of source integrity.

A report can still disclose information if you deliberately publish it. Review JSON, hashes, filenames, and manifests before sharing them. Use synthetic fixtures for bug reports whenever possible.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability or include credentials, private logs, or captured agent output. Use GitHub’s private vulnerability reporting on the repository’s **Security** tab. Include the affected version or commit, operating system and Python version, a minimal reproduction using synthetic data, expected behavior, and observed behavior. If private reporting is unavailable, contact the maintainer through the GitHub profile before public disclosure.

Supported releases and fixes will be documented in the changelog. GainCheck does not promise forensic isolation from another process changing a file concurrently; operational changes are reported as failures when detectable.
