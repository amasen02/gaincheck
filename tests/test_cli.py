from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
PYTHON = sys.executable


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-m", "gaincheck", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class CLITests(unittest.TestCase):
    def test_compare_json_has_schema_and_scrubbed_roles(self) -> None:
        result = run_cli(
            "compare",
            "--baseline",
            "examples/fixtures/caller-output.txt",
            "--candidate",
            "examples/fixtures/candidate-output.txt",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["measurement_kind"], "supplied_file_bytes")
        self.assertNotIn("examples/fixtures", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_marker_loss_is_exit_one_and_literal_is_scrubbed(self) -> None:
        result = run_cli(
            "compare",
            "--baseline",
            "examples/fixtures/error-baseline.txt",
            "--candidate",
            "examples/fixtures/error-lost.txt",
            "--require",
            "FAIL E42",
            "--json",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing_candidate_marker", result.stdout)
        self.assertNotIn("FAIL E42", result.stdout)

    def test_claimed_baseline_file_is_valid_json_finding(self) -> None:
        result = run_cli(
            "compare",
            "--baseline",
            "examples/fixtures/caller-output.txt",
            "--candidate",
            "examples/fixtures/candidate-output.txt",
            "--claimed-baseline",
            "examples/fixtures/source.log",
            "--json",
        )
        self.assertEqual(result.returncode, 1)
        document = json.loads(result.stdout)
        self.assertEqual(document["schema_version"], 1)
        self.assertIn("claimed_baseline_mismatch", document["finding_codes"])

    def test_malformed_json_argument_is_one_error_document(self) -> None:
        result = run_cli("compare", "--json", "--baseline")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        document = json.loads(result.stdout)
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["error"]["code"], "invalid_arguments")
        self.assertEqual(result.stderr, "")

    def test_missing_file_json_error_does_not_echo_path(self) -> None:
        secret_path = "missing-secret-command-output.txt"
        result = run_cli(
            "compare",
            "--baseline",
            secret_path,
            "--candidate",
            "examples/fixtures/candidate-output.txt",
            "--json",
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(secret_path, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "missing_file")

    def test_check_and_demo_have_stable_envelopes(self) -> None:
        checked = run_cli("check", "examples/demo-manifest.json", "--json")
        self.assertEqual(checked.returncode, 1)
        check_document = json.loads(checked.stdout)
        self.assertEqual(check_document["schema_version"], 1)
        self.assertEqual(check_document["summary"]["finding_count"], 1)

        demo = run_cli("demo")
        self.assertEqual(demo.returncode, 0)
        self.assertTrue(demo.stdout.startswith("File measurements only; token, billing and semantic outcomes are unverified."))
        demo_json = run_cli("demo", "--json")
        self.assertEqual(demo_json.returncode, 0)
        self.assertEqual(json.loads(demo_json.stdout)["schema_version"], 1)

    def test_rtk_json_reports_recorded_estimates_and_unknown_attribution(self) -> None:
        result = run_cli("rtk", "--db", "examples/rtk/history-standalone.db", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["attribution"], "unknown")
        self.assertEqual(document["recomputed_recorded_delta"], 192035)
        self.assertNotIn("gaincheck", document.get("database", ""))


if __name__ == "__main__":
    unittest.main()
