from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gaincheck.core import (
    GainCheckError,
    MAX_FILE_BYTES,
    canonical_json,
    check_manifest,
    compare_bytes,
    compare_paths,
    logical_line_count,
    sha256_bytes,
)
from gaincheck.rtk import audit_rtk


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "examples" / "fixtures"


class CoreEvidenceTests(unittest.TestCase):
    def test_logical_lines_match_independent_oracle_edges(self) -> None:
        self.assertEqual(logical_line_count(b""), 0)
        self.assertEqual(logical_line_count(b"a\n"), 1)
        self.assertEqual(logical_line_count(b"a\nb"), 2)
        self.assertEqual(logical_line_count("λ\n".encode()), 1)

    def test_compare_matches_public_manual_fixture_measurements(self) -> None:
        receipt, status = compare_paths(
            FIXTURES / "caller-output.txt", FIXTURES / "candidate-output.txt"
        )
        self.assertEqual(status, 0)
        self.assertEqual(receipt["schema_version"], 1)
        self.assertEqual(receipt["baseline"]["bytes"], 1491)
        self.assertEqual(receipt["candidate"]["bytes"], 758)
        self.assertEqual(receipt["baseline"]["logical_lines"], 20)
        self.assertEqual(receipt["candidate"]["logical_lines"], 11)
        self.assertEqual(receipt["bytes_removed"], 733)
        self.assertEqual(receipt["semantic_equivalence"], "unknown")

    def test_public_fixture_hashes_match_independent_oracle(self) -> None:
        oracle = json.loads((ROOT / "examples" / "independent-oracle.json").read_text())
        expected = {**oracle["file_evidence"], **oracle["additional_files"]}
        for name, facts in expected.items():
            data = (FIXTURES / name).read_bytes()
            self.assertEqual(len(data), facts["bytes"], name)
            self.assertEqual(sha256_bytes(data), facts["sha256"], name)
            expected_lines = facts.get("lines", facts.get("logical_lines"))
            self.assertEqual(logical_line_count(data), expected_lines, name)

    def test_marker_only_preservation_is_clear_but_semantics_unknown(self) -> None:
        receipt, status = compare_bytes(
            b"ERROR E42\nimportant payload\n",
            b"ERROR E42\n",
            markers=["ERROR E42"],
        )
        self.assertEqual(status, 0)
        self.assertEqual(receipt["marker_checks"][0]["candidate_present"], True)
        self.assertEqual(receipt["semantic_equivalence"], "unknown")
        self.assertNotIn("ERROR E42", canonical_json(receipt))

    def test_zero_denominator_and_duplicate_markers_are_explicit(self) -> None:
        receipt, status = compare_bytes(b"", b"x\n")
        self.assertEqual(status, 1)
        self.assertEqual(receipt["finding_codes"], ["zero_denominator"])
        receipt, status = compare_bytes(b"m\n", b"m\n", markers=["m", "m"])
        self.assertEqual(status, 0)
        self.assertEqual(len(receipt["marker_checks"]), 1)

    def test_claimed_baseline_is_a_file_and_hash_mismatch_is_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "baseline").write_bytes(b"abcd")
            (root / "candidate").write_bytes(b"ab")
            (root / "claim").write_bytes(b"wxyz")
            receipt, status = compare_paths(
                root / "baseline", root / "candidate", claimed_baseline=root / "claim"
            )
        self.assertEqual(status, 1)
        self.assertIn("claimed_baseline_mismatch", receipt["finding_codes"])
        self.assertEqual(receipt["claimed_baseline"]["bytes"], 4)
        self.assertNotEqual(receipt["claimed_baseline"]["sha256"], receipt["baseline"]["sha256"])

    def test_invalid_utf8_and_bound_are_invalid_without_traceback(self) -> None:
        with self.assertRaises(GainCheckError) as context:
            compare_bytes(b"\xff", b"ok")
        self.assertEqual(context.exception.code, "invalid_utf8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large"
            path.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
            with self.assertRaises(GainCheckError) as context:
                compare_paths(path, path)
        self.assertEqual(context.exception.code, "input_too_large")

    def test_manifest_is_sorted_and_aggregates_marker_finding(self) -> None:
        report, status = check_manifest(ROOT / "examples" / "demo-manifest.json")
        self.assertEqual(status, 1)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["summary"]["case_count"], 6)
        self.assertEqual(report["summary"]["finding_count"], 1)
        self.assertEqual(report["summary"]["clear_count"], 5)
        self.assertTrue(all("case_id" not in case for case in report["cases"]))

    def test_manifest_path_escape_is_invalid_and_does_not_echo_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cases": [
                            {"id": "escape", "baseline": "../secret", "candidate": "../secret"}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report, status = check_manifest(root / "manifest.json")
        self.assertEqual(status, 2)
        self.assertEqual(report["cases"][0]["error_code"], "manifest_path_outside_root")
        self.assertNotIn("secret", canonical_json(report))

    def test_manifest_invalid_case_outweighs_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "base").write_text("FAIL E42\n", encoding="utf-8")
            (root / "candidate").write_text("ok\n", encoding="utf-8")
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cases": [
                            {"id": "finding", "baseline": "base", "candidate": "candidate", "require": ["FAIL E42"]},
                            {"id": "missing", "baseline": "base", "candidate": "missing-output"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report, status = check_manifest(root / "manifest.json")
        self.assertEqual(status, 2)
        self.assertEqual(report["summary"]["finding_count"], 1)
        self.assertEqual(report["summary"]["invalid_count"], 1)

    def test_manifest_boolean_schema_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text('{"schema_version": true, "cases": []}', encoding="utf-8")
            with self.assertRaises(GainCheckError) as context:
                check_manifest(path)
        self.assertEqual(context.exception.code, "unsupported_manifest_schema")

    def test_manifest_mixed_operational_invalid_and_finding_uses_priority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "good.txt").write_text("marker\n", encoding="utf-8")
            (root / "lost.txt").write_text("other\n", encoding="utf-8")
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cases": [
                            {"id": "finding", "baseline": "good.txt", "candidate": "lost.txt", "require": ["marker"]},
                            {"id": "invalid", "baseline": "missing.txt", "candidate": "good.txt"},
                            {"id": "operational", "baseline": "operational.txt", "candidate": "good.txt"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            original = __import__("gaincheck.core", fromlist=["read_bounded"]).read_bounded

            def bounded(path: Path, *, manifest: bool = False) -> bytes:
                if path.name == "operational.txt":
                    raise GainCheckError("permission_denied", 3)
                return original(path, manifest=manifest)

            with patch("gaincheck.core.read_bounded", side_effect=bounded):
                report, status = check_manifest(root / "manifest.json")
        self.assertEqual(status, 3)
        self.assertEqual(report["summary"], {"case_count": 3, "clear_count": 0, "finding_count": 1, "invalid_count": 1, "operational_count": 1})

    def test_manifest_symlink_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / (root.name + "-outside.txt")
            outside.write_text("outside", encoding="utf-8")
            link = root / "link.txt"
            try:
                os.symlink(outside, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable")
            (root / "manifest.json").write_text(
                json.dumps({"schema_version": 1, "cases": [{"id": "escape", "baseline": "link.txt", "candidate": "link.txt"}]}),
                encoding="utf-8",
            )
            report, status = check_manifest(root / "manifest.json")
            outside.unlink()
        self.assertEqual(status, 2)
        self.assertEqual(report["cases"][0]["error_code"], "manifest_path_outside_root")


class RTKAdapterTests(unittest.TestCase):
    def test_sanitized_standalone_fixture_matches_oracle_sums(self) -> None:
        report, status = audit_rtk(ROOT / "examples" / "rtk" / "history-standalone.db")
        self.assertEqual(status, 0)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["row_count"], 2)
        self.assertEqual(report["input_tokens_sum"], 192243)
        self.assertEqual(report["output_tokens_sum"], 208)
        self.assertEqual(report["recomputed_recorded_delta"], 192035)
        self.assertEqual(report["reported_saved_estimate"], 192035)
        self.assertEqual(report["attribution"], "unknown")
        self.assertEqual(report["nonpositive_count"], 0)
        self.assertEqual(report["class_aggregates"]["read"]["count"], 1)
        self.assertEqual(report["class_aggregates"]["read"]["reported_delta"], 192034)
        self.assertEqual(report["class_aggregates"]["read"]["recomputed_delta"], 192034)
        self.assertEqual(report["class_aggregates"]["other"]["reported_delta"], 1)

    def test_wal_header_is_rejected_before_sqlite_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wal.db"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE commands (id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, original_cmd TEXT NOT NULL, rtk_cmd TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, saved_tokens INTEGER NOT NULL, savings_pct REAL NOT NULL)")
            connection.execute("INSERT INTO commands VALUES (1, 't', 'a', 'b', 2, 1, 1, 50.0)")
            connection.commit()
            connection.close()
            data = bytearray(path.read_bytes())
            data[18] = 2
            data[19] = 2
            path.write_bytes(data)
            before = path.read_bytes()
            with self.assertRaises(GainCheckError) as context:
                audit_rtk(path)
            self.assertEqual(context.exception.code, "wal_database_rejected")
            self.assertEqual(path.read_bytes(), before)

    def test_missing_and_empty_database_do_not_create_or_mutate_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing.db"
            with self.assertRaises(GainCheckError) as context:
                audit_rtk(missing)
            self.assertEqual(context.exception.code, "missing_database")
            self.assertFalse(missing.exists())
            empty = root / "empty.db"
            empty.write_bytes(b"")
            before = empty.read_bytes()
            with self.assertRaises(GainCheckError) as context:
                audit_rtk(empty)
            self.assertEqual(context.exception.code, "invalid_database")
            self.assertEqual(empty.read_bytes(), before)

    def test_companion_is_rejected_without_mutating_companion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "database.db"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE commands (id INTEGER PRIMARY KEY, timestamp TEXT, original_cmd TEXT, rtk_cmd TEXT, input_tokens INTEGER, output_tokens INTEGER, saved_tokens INTEGER, savings_pct REAL)")
            connection.execute("INSERT INTO commands VALUES (1, 't', 'a', 'b', 2, 1, 1, 50.0)")
            connection.commit()
            connection.close()
            companion = path.with_name(path.name + "-wal")
            companion.write_bytes(b"fixture companion")
            before = companion.stat()
            with self.assertRaises(GainCheckError) as context:
                audit_rtk(path)
            after = companion.stat()
            self.assertEqual(context.exception.code, "database_companion_present")
            self.assertEqual(companion.read_bytes(), b"fixture companion")
            self.assertEqual((after.st_size, after.st_mtime_ns), (before.st_size, before.st_mtime_ns))

    def test_view_and_virtual_commands_objects_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            view = root / "view.db"
            connection = sqlite3.connect(view)
            connection.execute("CREATE TABLE source (id INTEGER)")
            connection.execute("CREATE VIEW commands AS SELECT id FROM source")
            connection.commit()
            connection.close()
            with self.assertRaises(GainCheckError) as context:
                audit_rtk(view)
            self.assertEqual(context.exception.code, "unsupported_schema")
            virtual = root / "virtual.db"
            connection = sqlite3.connect(virtual)
            try:
                connection.execute("CREATE VIRTUAL TABLE commands USING fts5(id)")
                connection.commit()
            except sqlite3.Error:
                connection.close()
                self.skipTest("SQLite build has no FTS5 module")
            connection.close()
            with self.assertRaises(GainCheckError) as context:
                audit_rtk(virtual)
            self.assertEqual(context.exception.code, "unsupported_schema")

    def test_malformed_counter_rows_are_invalid_but_signed_saved_is_a_finding(self) -> None:
        ddl = "CREATE TABLE commands (id INTEGER PRIMARY KEY, timestamp TEXT, original_cmd TEXT, rtk_cmd TEXT, input_tokens INTEGER, output_tokens INTEGER, saved_tokens INTEGER, savings_pct REAL)"
        cases = [
            ((1, "t", "a", "b", -1, 1, -2, 0.0), "malformed_database_row"),
            ((1, "t", "a", "b", 2, 1, -1, -50.0), None),
            ((1, "t", "a", "b", 2, 1, 1, float("nan")), "malformed_database_row"),
        ]
        for row, expected_error in cases:
            with self.subTest(row=row, expected_error=expected_error), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "row.db"
                connection = sqlite3.connect(path)
                connection.execute(ddl)
                connection.execute("INSERT INTO commands VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
                connection.commit()
                connection.close()
                if expected_error:
                    with self.assertRaises(GainCheckError) as context:
                        audit_rtk(path)
                    self.assertEqual(context.exception.code, expected_error)
                    self.assertEqual(context.exception.status, 2)
                else:
                    report, status = audit_rtk(path)
                    self.assertEqual(status, 1)
                    self.assertEqual(report["nonpositive_count"], 1)

    def test_valid_header_with_corrupt_pages_is_invalid_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corrupt.db"
            data = bytearray(b"SQLite format 3\x00" + b"\x00" * 200)
            data[18] = 1
            data[19] = 1
            path.write_bytes(data)
            with self.assertRaises(GainCheckError) as context:
                audit_rtk(path)
        self.assertEqual(context.exception.code, "invalid_database")
        self.assertEqual(context.exception.status, 2)

    def test_arithmetic_mismatch_is_finding_and_nonpositive_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.db"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE commands (id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, original_cmd TEXT NOT NULL, rtk_cmd TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, saved_tokens INTEGER NOT NULL, savings_pct REAL NOT NULL)")
            connection.execute("INSERT INTO commands VALUES (1, 't', 'a', 'rtk read', 2, 3, 0, 0.0)")
            connection.commit()
            connection.close()
            report, status = audit_rtk(path)
        self.assertEqual(status, 1)
        self.assertEqual(report["arithmetic_mismatch_count"], 1)
        self.assertEqual(report["nonpositive_count"], 1)
        self.assertEqual(report["command_class_counts"], {"read": 1, "other": 0})


if __name__ == "__main__":
    unittest.main()
