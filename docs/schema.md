# JSON schema vocabulary

Receipts use `schema_version: 1` and deterministic key ordering.

`compare` measures `baseline` and `candidate` supplied UTF-8 files. Each input
contains a role, `user_supplied` provenance, SHA-256, byte count, and logical
line count. `bytes_removed` is `baseline.bytes - candidate.bytes`; the percentage
is null for a zero denominator. Marker checks expose only a deterministic marker
index and marker hash. Marker presence is a declared sanity check and does not
prove payload or semantic preservation.

The fields `semantic_equivalence`, `token_outcome`, `billing_outcome`, and
`attribution` are always `unknown` in generic receipts. A claimed baseline is a
separate supplied file and a differing hash or size produces
`claimed_baseline_mismatch`.

`check` wraps the same receipts in sorted case order. User case IDs are replaced
by a local `evidence_id` fingerprint and never echoed. Manifest paths are
relative to the manifest and must resolve under its directory tree.

`rtk` uses `measurement_kind: rtk_recorded_estimate`. It reports
`reported_saved_estimate` from the database and `recomputed_recorded_delta` from
the recorded input/output counters. These are upstream estimates; the adapter
does not certify causal savings or billing. `read_baseline_unverified` and
`causality_unknown` remain explicit warnings.
