"""Optional, read-only adapter for the pinned RTK tracking database shape."""

from __future__ import annotations

import hashlib
import math
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .core import GainCheckError

MAX_ROWS = 100_000
MAX_DATABASE_BYTES = 256 * 1024 * 1024
MAX_TEXT_FIELD_BYTES = 64 * 1024
REQUIRED_TYPES = {
    "id": "INTEGER",
    "timestamp": "TEXT",
    "original_cmd": "TEXT",
    "rtk_cmd": "TEXT",
    "input_tokens": "INTEGER",
    "output_tokens": "INTEGER",
    "saved_tokens": "INTEGER",
    "savings_pct": "REAL",
}
OPTIONAL_TYPES = {"exec_time_ms": "INTEGER", "project_path": "TEXT"}
COMPANION_SUFFIXES = ("-wal", "-shm", "-journal")


def _file_fingerprint(path: Path) -> tuple[int, int, str]:
    try:
        initial = path.stat()
    except OSError as exc:
        raise GainCheckError("database_snapshot_failed", 3) from exc
    if initial.st_size > MAX_DATABASE_BYTES:
        raise GainCheckError("database_too_large", 2)
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_DATABASE_BYTES:
                raise GainCheckError("database_too_large", 2)
            digest.update(chunk)
    final = path.stat()
    if final.st_size != total or final.st_mtime_ns != initial.st_mtime_ns:
        raise GainCheckError("database_changed_during_read", 3)
    return total, final.st_mtime_ns, digest.hexdigest()


def _tracked_snapshot(path: Path) -> dict[str, tuple[int, int, str] | None]:
    try:
        result: dict[str, tuple[int, int, str] | None] = {}
        for entry in [path, *_companions(path)]:
            if not os.path.lexists(entry):
                result[entry.name] = None
                continue
            stat = entry.stat()
            result[entry.name] = (stat.st_size, stat.st_mtime_ns, "file")
        return result
    except OSError as exc:
        raise GainCheckError("database_directory_unreadable", 3) from exc


def _companions(path: Path) -> list[Path]:
    return [path.with_name(path.name + suffix) for suffix in COMPANION_SUFFIXES]


def _read_header(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read(100)
    except FileNotFoundError as exc:
        raise GainCheckError("missing_database", 2) from exc
    except PermissionError as exc:
        raise GainCheckError("database_permission_denied", 3) from exc
    except OSError as exc:
        raise GainCheckError("database_read_failed", 3) from exc


def _raise_sqlite_error(exc: sqlite3.Error, fallback: str = "database_query_failed") -> None:
    """Translate SQLite's bounded input errors without exposing driver text."""
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int) and (code & 0xFF) in {
        sqlite3.SQLITE_CORRUPT,
        sqlite3.SQLITE_NOTADB,
    }:
        raise GainCheckError("invalid_database", 2) from exc
    raise GainCheckError(fallback, 3) from exc


def _validate_schema(connection: sqlite3.Connection) -> None:
    try:
        object_row = connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name = 'commands'"
        ).fetchone()
        if object_row is None:
            raise GainCheckError("unsupported_schema", 2)
        object_type, _name, sql = object_row
        if object_type != "table" or (isinstance(sql, str) and "VIRTUAL TABLE" in sql.upper()):
            raise GainCheckError("unsupported_schema", 2)
        columns = connection.execute("PRAGMA table_info(commands)").fetchall()
    except GainCheckError:
        raise
    except sqlite3.Error as exc:
        _raise_sqlite_error(exc)
    actual: dict[str, str] = {}
    for _cid, name, declared_type, _notnull, _default, _pk in columns:
        if not isinstance(name, str) or not isinstance(declared_type, str):
            raise GainCheckError("unsupported_schema", 2)
        if name in actual:
            raise GainCheckError("unsupported_schema", 2)
        actual[name] = declared_type.upper().strip()
    for name, expected in REQUIRED_TYPES.items():
        if actual.get(name) != expected:
            raise GainCheckError("unsupported_schema", 2)
    for name, declared in actual.items():
        if name in OPTIONAL_TYPES and declared != OPTIONAL_TYPES[name]:
            raise GainCheckError("unsupported_schema", 2)


def _is_integer(value: Any) -> bool:
    return type(value) is int and -(2**63) <= value < 2**63


def _is_text(value: Any) -> bool:
    return isinstance(value, str)


def _inspect_row(row: tuple[Any, ...]) -> tuple[int, int, int, bool, str]:
    if len(row) != 8:
        raise GainCheckError("malformed_database_row", 2)
    row_id, timestamp, original_cmd, rtk_cmd, input_tokens, output_tokens, saved_tokens, savings_pct = row
    if not _is_integer(row_id) or row_id < 0:
        raise GainCheckError("malformed_database_row", 2)
    if not all(_is_text(value) for value in (timestamp, original_cmd, rtk_cmd)):
        raise GainCheckError("malformed_database_row", 2)
    if any(len(value.encode("utf-8")) > MAX_TEXT_FIELD_BYTES for value in (timestamp, original_cmd, rtk_cmd)):
        raise GainCheckError("database_text_field_too_large", 2)
    if not _is_integer(input_tokens) or input_tokens < 0:
        raise GainCheckError("malformed_database_row", 2)
    if not _is_integer(output_tokens) or output_tokens < 0:
        raise GainCheckError("malformed_database_row", 2)
    if not _is_integer(saved_tokens):
        raise GainCheckError("malformed_database_row", 2)
    if isinstance(savings_pct, bool) or not isinstance(savings_pct, (int, float)):
        raise GainCheckError("malformed_database_row", 2)
    if not math.isfinite(float(savings_pct)):
        raise GainCheckError("malformed_database_row", 2)
    expected_saved = input_tokens - output_tokens
    expected_pct = 0.0 if input_tokens == 0 else 100.0 * expected_saved / input_tokens
    mismatch = saved_tokens != expected_saved or not math.isclose(
        float(savings_pct), expected_pct, rel_tol=1e-9, abs_tol=1e-6
    )
    command_class = "read" if rtk_cmd.strip().startswith("rtk read") else "other"
    return input_tokens, output_tokens, saved_tokens, mismatch, command_class


def audit_rtk(path: Path) -> tuple[dict[str, Any], int]:
    """Audit recorded RTK arithmetic without claiming causal savings."""
    try:
        if not path.exists():
            raise GainCheckError("missing_database", 2)
        if not path.is_file():
            raise GainCheckError("database_not_regular_file", 2)
    except GainCheckError:
        raise
    except OSError as exc:
        raise GainCheckError("database_stat_failed", 3) from exc

    try:
        directory_before = _tracked_snapshot(path)
        db_before = _file_fingerprint(path)
        if db_before[0] > MAX_DATABASE_BYTES:
            raise GainCheckError("database_too_large", 2)
    except GainCheckError:
        raise
    except PermissionError as exc:
        raise GainCheckError("database_permission_denied", 3) from exc
    except OSError as exc:
        raise GainCheckError("database_snapshot_failed", 3) from exc
    for companion in _companions(path):
        try:
            if os.path.lexists(companion):
                raise GainCheckError("database_companion_present", 3)
        except GainCheckError:
            raise
        except OSError as exc:
            raise GainCheckError("database_companion_unreadable", 3) from exc

    header = _read_header(path)
    if len(header) < 20 or not header.startswith(b"SQLite format 3\x00"):
        raise GainCheckError("invalid_database", 2)
    # SQLite header bytes 18/19 are read/write versions: 2 means WAL.
    if header[18] != 1 or header[19] != 1:
        raise GainCheckError("wal_database_rejected", 3)

    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection: sqlite3.Connection | None = None
    row_count = 0
    input_sum = 0
    output_sum = 0
    saved_sum = 0
    mismatch_count = 0
    nonpositive_count = 0
    command_class_counts = {"read": 0, "other": 0}
    class_aggregates = {
        "read": {"count": 0, "input_tokens": 0, "output_tokens": 0, "reported_delta": 0, "recomputed_delta": 0},
        "other": {"count": 0, "input_tokens": 0, "output_tokens": 0, "reported_delta": 0, "recomputed_delta": 0},
    }
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA busy_timeout=2000")
        connection.execute("BEGIN")
        started = time.monotonic()
        connection.set_progress_handler(
            lambda: 1 if time.monotonic() - started > 2.0 else 0, 10_000
        )
        _validate_schema(connection)
        cursor = connection.execute(
            "SELECT id, timestamp, original_cmd, rtk_cmd, input_tokens, output_tokens, saved_tokens, savings_pct "
            "FROM commands ORDER BY id LIMIT ?",
            (MAX_ROWS + 1,),
        )
        for row in cursor:
            row_count += 1
            if row_count > MAX_ROWS:
                break
            input_tokens, output_tokens, saved_tokens, mismatch, command_class = _inspect_row(row)
            input_sum += input_tokens
            output_sum += output_tokens
            saved_sum += saved_tokens
            mismatch_count += int(mismatch)
            nonpositive_count += int(saved_tokens <= 0)
            command_class_counts[command_class] += 1
            aggregate = class_aggregates[command_class]
            aggregate["count"] += 1
            aggregate["input_tokens"] += input_tokens
            aggregate["output_tokens"] += output_tokens
            aggregate["reported_delta"] += saved_tokens
            aggregate["recomputed_delta"] += input_tokens - output_tokens
        connection.set_progress_handler(None, 0)
        connection.execute("ROLLBACK")
    except GainCheckError:
        raise
    except sqlite3.OperationalError as exc:
        _raise_sqlite_error(exc, "database_busy_or_query_failed")
    except sqlite3.DatabaseError as exc:
        _raise_sqlite_error(exc, "database_query_failed")
    except sqlite3.Error as exc:
        raise GainCheckError("database_query_failed", 3) from exc
    finally:
        if connection is not None:
            connection.close()

    try:
        directory_after = _tracked_snapshot(path)
        db_after = _file_fingerprint(path)
    except (OSError, PermissionError) as exc:
        raise GainCheckError("database_snapshot_failed", 3) from exc
    if directory_before != directory_after or db_before != db_after:
        raise GainCheckError("database_changed_during_read", 3)
    if row_count > MAX_ROWS:
        raise GainCheckError("too_many_database_rows", 2)
    if not row_count:
        raise GainCheckError("no_data", 2)

    finding_codes = ["recorded_arithmetic_mismatch"] if mismatch_count else []
    status = 1 if finding_codes else 0
    report = {
        "schema_version": 1,
        "measurement_kind": "rtk_recorded_estimate",
        "provenance": "user_supplied_database",
        "row_count": row_count,
        "input_tokens_sum": input_sum,
        "output_tokens_sum": output_sum,
        "reported_saved_estimate": saved_sum,
        "recomputed_recorded_delta": input_sum - output_sum,
        "arithmetic_mismatch_count": mismatch_count,
        "nonpositive_count": nonpositive_count,
        "command_class_counts": command_class_counts,
        "class_aggregates": class_aggregates,
        "finding_codes": finding_codes,
        "status": "finding" if finding_codes else "clear",
        "attribution": "unknown",
        "semantic_equivalence": "unknown",
        "token_outcome": "unknown",
        "billing_outcome": "unknown",
        "causal_savings": "unknown",
        "warnings": ["read_baseline_unverified", "causality_unknown"],
    }
    return report, status


def human_rtk(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Recorded estimates only; causality, semantic equivalence, token outcome, and billing outcome are unverified.",
            f"Rows: {report['row_count']}; recorded input={report['input_tokens_sum']}, output={report['output_tokens_sum']}.",
            f"Reported saved estimate: {report['reported_saved_estimate']}; recomputed recorded delta: {report['recomputed_recorded_delta']}.",
            f"Arithmetic mismatches: {report['arithmetic_mismatch_count']}; non-positive rows: {report['nonpositive_count']}; attribution: unknown.",
            f"Fixed command classes: read={report['command_class_counts']['read']}, other={report['command_class_counts']['other']}; read baseline: unverified.",
            f"Class recorded deltas: read={report['class_aggregates']['read']['reported_delta']}/{report['class_aggregates']['read']['recomputed_delta']}, other={report['class_aggregates']['other']['reported_delta']}/{report['class_aggregates']['other']['recomputed_delta']}.",
        ]
    )
