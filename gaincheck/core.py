"""Bounded, privacy-safe arithmetic over caller-supplied evidence files."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path
from typing import Any, Iterable

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 1 * 1024 * 1024
MAX_CASES = 100
MAX_MARKERS = 256
MAX_MARKER_CHARS = 4096
CASE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class GainCheckError(Exception):
    """An expected input or operational failure with a stable public code."""

    def __init__(self, code: str, status: int = 2):
        super().__init__(code)
        self.code = code
        self.status = status


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def logical_line_count(data: bytes) -> int:
    """Count logical lines, including one final unterminated line."""
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _finite(value: float | int | None) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite number")
    return value


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return _finite(100.0 * numerator / denominator)


def read_bounded(path: Path, *, manifest: bool = False) -> bytes:
    """Read a regular file with a hard upper bound and stable error mapping."""
    limit = MAX_MANIFEST_BYTES if manifest else MAX_FILE_BYTES
    try:
        file_stat = path.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            raise GainCheckError("not_regular_file", 2)
        with path.open("rb") as handle:
            mode = os.fstat(handle.fileno()).st_mode
            if (mode & 0o170000) != 0o100000:
                raise GainCheckError("not_regular_file", 2)
            data = handle.read(limit + 1)
    except FileNotFoundError as exc:
        raise GainCheckError("missing_file", 2) from exc
    except PermissionError as exc:
        raise GainCheckError("permission_denied", 3) from exc
    except OSError as exc:
        raise GainCheckError("file_read_failed", 3) from exc
    if len(data) > limit:
        raise GainCheckError("input_too_large", 2)
    return data


def _marker_hash(marker: str) -> str:
    try:
        encoded = marker.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise GainCheckError("invalid_marker", 2) from exc
    return sha256_bytes(encoded)


def _normalise_markers(markers: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for marker in markers:
        if not isinstance(marker, str) or not marker or len(marker) > MAX_MARKER_CHARS:
            raise GainCheckError("invalid_marker", 2)
        try:
            marker.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise GainCheckError("invalid_marker", 2) from exc
        if marker not in seen:
            result.append(marker)
            seen.add(marker)
    if len(result) > MAX_MARKERS:
        raise GainCheckError("too_many_markers", 2)
    return result


def compare_bytes(
    baseline: bytes,
    candidate: bytes,
    *,
    claimed_baseline: tuple[bytes, str] | None = None,
    markers: Iterable[str] = (),
) -> tuple[dict[str, Any], int]:
    """Build a deterministic receipt from two supplied byte strings.

    The receipt deliberately makes no claim that either file was agent-visible,
    semantically equivalent, token-equivalent, or billable.  Those facts require
    capture provenance unavailable to this pure file comparison.
    """
    try:
        baseline.decode("utf-8")
        candidate.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GainCheckError("invalid_utf8", 2) from exc
    normalised = _normalise_markers(markers)

    baseline_bytes = len(baseline)
    candidate_bytes = len(candidate)
    bytes_removed = baseline_bytes - candidate_bytes
    marker_checks: list[dict[str, Any]] = []
    baseline_marker_missing = False
    candidate_marker_missing = False
    for index, marker in enumerate(normalised):
        marker_bytes = marker.encode("utf-8")
        baseline_present = marker_bytes in baseline
        candidate_present = marker_bytes in candidate
        baseline_marker_missing |= not baseline_present
        candidate_marker_missing |= not candidate_present
        marker_checks.append(
            {
                "marker_index": index,
                "marker_sha256": _marker_hash(marker),
                "baseline_present": baseline_present,
                "candidate_present": candidate_present,
            }
        )

    finding_codes: list[str] = []
    claimed_data = claimed_baseline[0] if claimed_baseline is not None else None
    claimed_hash = claimed_baseline[1] if claimed_baseline is not None else None
    if claimed_data is not None and (
        len(claimed_data) != baseline_bytes or claimed_hash != sha256_bytes(baseline)
    ):
        finding_codes.append("claimed_baseline_mismatch")
    if baseline_bytes == 0 and candidate_bytes > 0:
        finding_codes.append("zero_denominator")
    if baseline_marker_missing:
        finding_codes.append("missing_baseline_marker")
    if candidate_marker_missing:
        finding_codes.append("missing_candidate_marker")

    if baseline_marker_missing:
        status = 2
        status_name = "invalid_declared_evidence"
    elif finding_codes:
        status = 1
        status_name = "finding"
    else:
        status = 0
        status_name = "clear"

    claimed_bytes = len(claimed_data) if claimed_data is not None else None
    claimed_removed = claimed_bytes - candidate_bytes if claimed_bytes is not None else None
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "measurement_kind": "supplied_file_bytes",
        "provenance": {
            "baseline": "user_supplied",
            "candidate": "user_supplied",
            "claimed_baseline": "user_supplied" if claimed_data is not None else None,
        },
        "baseline": {
            "role": "baseline",
            "sha256": sha256_bytes(baseline),
            "bytes": baseline_bytes,
            "logical_lines": logical_line_count(baseline),
        },
        "candidate": {
            "role": "candidate",
            "sha256": sha256_bytes(candidate),
            "bytes": candidate_bytes,
            "logical_lines": logical_line_count(candidate),
        },
        "bytes_removed": bytes_removed,
        "byte_reduction_pct": _pct(bytes_removed, baseline_bytes),
        "claimed_baseline": (
            {
                "role": "claimed_baseline",
                "provenance": "user_supplied",
                "sha256": claimed_hash,
                "bytes": claimed_bytes,
                "logical_lines": logical_line_count(claimed_data),
            }
            if claimed_data is not None
            else None
        ),
        "claimed_baseline_bytes": claimed_bytes,
        "claimed_bytes_removed": claimed_removed,
        "claimed_byte_reduction_pct": (
            _pct(claimed_removed, claimed_bytes)
            if claimed_bytes is not None and claimed_removed is not None
            else None
        ),
        "marker_checks": marker_checks,
        "finding_codes": finding_codes,
        "status": status_name,
        "semantic_equivalence": "unknown",
        "token_outcome": "unknown",
        "billing_outcome": "unknown",
        "attribution": "unknown",
    }
    return receipt, status


def compare_paths(
    baseline_path: Path,
    candidate_path: Path,
    *,
    claimed_baseline: Path | None = None,
    markers: Iterable[str] = (),
) -> tuple[dict[str, Any], int]:
    baseline = read_bounded(baseline_path)
    candidate = read_bounded(candidate_path)
    claimed = None
    if claimed_baseline is not None:
        claimed_data = read_bounded(claimed_baseline)
        try:
            claimed_data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GainCheckError("invalid_utf8", 2) from exc
        claimed = (claimed_data, sha256_bytes(claimed_data))
    return compare_bytes(
        baseline,
        candidate,
        claimed_baseline=claimed,
        markers=markers,
    )


def _manifest_path(manifest_path: Path, value: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise GainCheckError("invalid_manifest_path", 2)
    relative = Path(value)
    if relative.is_absolute():
        raise GainCheckError("manifest_path_outside_root", 2)
    root = manifest_path.resolve().parent
    resolved = (root / relative).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise GainCheckError("manifest_path_outside_root", 2) from exc
    return resolved


def _load_manifest(
    path: Path,
) -> list[tuple[str, Path | None, Path | None, Path | None, list[str], str | None]]:
    raw = read_bounded(path, manifest=True)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GainCheckError("invalid_manifest_json", 2) from exc
    if (
        not isinstance(document, dict)
        or type(document.get("schema_version")) is not int
        or document.get("schema_version") != 1
    ):
        raise GainCheckError("unsupported_manifest_schema", 2)
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases or len(cases) > MAX_CASES:
        raise GainCheckError("invalid_manifest_cases", 2)
    result: list[tuple[str, Path | None, Path | None, Path | None, list[str], str | None]] = []
    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise GainCheckError("invalid_manifest_case", 2)
        case_id = case.get("id")
        if not isinstance(case_id, str) or not CASE_ID_RE.fullmatch(case_id) or case_id in ids:
            raise GainCheckError("invalid_case_id", 2)
        ids.add(case_id)
        claimed_value = case.get("claimed_baseline")
        if claimed_value is not None and not isinstance(claimed_value, str):
            raise GainCheckError("invalid_claimed_baseline", 2)
        markers = case.get("require", [])
        if not isinstance(markers, list):
            raise GainCheckError("invalid_markers", 2)
        normalised = _normalise_markers(markers)
        path_error: str | None = None
        try:
            baseline_path = _manifest_path(path, case.get("baseline"))
            candidate_path = _manifest_path(path, case.get("candidate"))
            claimed_path = (
                _manifest_path(path, claimed_value) if claimed_value is not None else None
            )
        except GainCheckError as exc:
            baseline_path = candidate_path = claimed_path = None
            path_error = exc.code
        result.append((case_id, baseline_path, candidate_path, claimed_path, normalised, path_error))
    return sorted(result, key=lambda item: item[0])


def check_manifest(path: Path) -> tuple[dict[str, Any], int]:
    cases = _load_manifest(path)
    receipts: list[dict[str, Any]] = []
    statuses: list[int] = []
    for index, (case_id, baseline, candidate, claimed, markers, path_error) in enumerate(cases):
        evidence_id = hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:16]
        try:
            if path_error is not None or baseline is None or candidate is None:
                raise GainCheckError(path_error or "invalid_manifest_path", 2)
            receipt, status = compare_paths(
                baseline,
                candidate,
                claimed_baseline=claimed,
                markers=markers,
            )
        except GainCheckError as exc:
            receipt = {
                "schema_version": 1,
                "measurement_kind": "supplied_file_bytes",
                "provenance": {
                    "baseline": "user_supplied",
                    "candidate": "user_supplied",
                    "claimed_baseline": "user_supplied" if claimed is not None else None,
                },
                "status": "operational" if exc.status == 3 else "invalid",
                "error_code": exc.code,
                "semantic_equivalence": "unknown",
                "token_outcome": "unknown",
                "billing_outcome": "unknown",
            }
            status = exc.status
        receipt = {"case_index": index, "evidence_id": evidence_id, **receipt}
        receipts.append(receipt)
        statuses.append(status)
    aggregate = 3 if 3 in statuses else 2 if 2 in statuses else 1 if 1 in statuses else 0
    report = {
        "schema_version": 1,
        "measurement_kind": "batch_supplied_file_bytes",
        "provenance": "user_supplied",
        "cases": receipts,
        "summary": {
            "case_count": len(receipts),
            "clear_count": sum(s == 0 for s in statuses),
            "finding_count": sum(s == 1 for s in statuses),
            "invalid_count": sum(s == 2 for s in statuses),
            "operational_count": sum(s == 3 for s in statuses),
        },
        "semantic_equivalence": "unknown",
        "token_outcome": "unknown",
        "billing_outcome": "unknown",
    }
    return report, aggregate


def human_compare(receipt: dict[str, Any]) -> str:
    lines = [
        "File measurements only; token, billing and semantic outcomes are unverified.",
    ]
    if "baseline" in receipt:
        b = receipt["baseline"]
        c = receipt["candidate"]
        lines.extend(
            [
                f"Baseline: {b['bytes']} bytes, {b['logical_lines']} logical lines, sha256 {b['sha256']}.",
                f"Candidate: {c['bytes']} bytes, {c['logical_lines']} logical lines, sha256 {c['sha256']}.",
                f"Bytes removed from supplied files: {receipt['bytes_removed']} ({receipt['byte_reduction_pct'] if receipt['byte_reduction_pct'] is not None else 'unknown'}%).",
            ]
        )
        for marker in receipt.get("marker_checks", []):
            lines.append(
                f"Marker {marker['marker_index']}: baseline={'present' if marker['baseline_present'] else 'missing'}, candidate={'present' if marker['candidate_present'] else 'missing'}, marker sha256 {marker['marker_sha256']}."
            )
        if receipt.get("finding_codes"):
            lines.append("Findings: " + ", ".join(receipt["finding_codes"]) + ".")
        lines.append("Semantic equivalence, token outcome, billing outcome, and attribution: unknown.")
    return "\n".join(lines)


def human_check(report: dict[str, Any]) -> str:
    lines = [
        "File measurements only; token, billing and semantic outcomes are unverified.",
        f"Cases: {report['summary']['case_count']}; clear={report['summary']['clear_count']}, findings={report['summary']['finding_count']}, invalid={report['summary']['invalid_count']}, operational={report['summary']['operational_count']}.",
    ]
    for case in report["cases"]:
        label = case.get("status", "unknown")
        code = case.get("error_code") or ",".join(case.get("finding_codes", [])) or "none"
        lines.append(f"Case {case['case_index']} ({case['evidence_id']}): {label}; {code}.")
    return "\n".join(lines)


def canonical_json(document: dict[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def error_document(code: str) -> dict[str, Any]:
    messages = {
        "invalid_arguments": "inspect the command syntax and retry",
        "missing_file": "the supplied file is missing",
        "permission_denied": "the supplied file cannot be read",
        "invalid_utf8": "the supplied text file is not valid UTF-8",
        "input_too_large": "the supplied input exceeds the configured bound",
        "invalid_manifest_json": "the supplied manifest is not valid JSON",
        "unsupported_manifest_schema": "the supplied manifest schema is unsupported",
        "invalid_marker": "a required marker is invalid",
        "invalid_claimed_baseline": "the claimed baseline must name a supplied file",
    }
    return {
        "schema_version": 1,
        "error": {"code": code, "message": messages.get(code, "inspect the named input role and retry")},
    }
