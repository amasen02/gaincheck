"""Command-line interface for gaincheck."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .core import (
    GainCheckError,
    canonical_json,
    check_manifest,
    compare_paths,
    compare_bytes,
    error_document,
    human_check,
    human_compare,
    sha256_bytes,
)
from .rtk import audit_rtk, human_rtk


class _Parser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise GainCheckError("invalid_arguments", 2)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="gaincheck", description="Portable evidence receipts for supplied output files.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    compare = sub.add_parser("compare", help="compare two supplied files")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)
    compare.add_argument("--claimed-baseline")
    compare.add_argument("--require", action="append", default=[])
    compare.add_argument("--json", action="store_true")

    check = sub.add_parser("check", help="check a schema-1 manifest")
    check.add_argument("manifest")
    check.add_argument("--json", action="store_true")

    rtk = sub.add_parser("rtk", help="audit a quiescent RTK SQLite database")
    rtk.add_argument("database", nargs="?")
    rtk.add_argument("--db", dest="database_option")
    rtk.add_argument("--json", action="store_true")

    demo = sub.add_parser("demo", help="run the self-contained synthetic demo")
    demo.add_argument("--json", action="store_true")
    return parser


def _demo() -> tuple[dict, int]:
    cases = [
        ("compact output", b"alpha\nbeta\n", b"alpha\n", None, []),
        ("required marker kept", b"FAIL E42\nok\n", b"FAIL E42\n", None, ["FAIL E42"]),
        ("required marker lost", b"FAIL E42\nok\n", b"ok\n", None, ["FAIL E42"]),
        ("zero byte denominator", b"", b"x\n", None, []),
        ("claimed baseline differs", b"same\n", b"same\n", (b"other", sha256_bytes(b"other")), []),
        ("marker only payload", b"ERROR E42\nimportant payload\n", b"ERROR E42\n", None, ["ERROR E42"]),
    ]
    receipts = []
    statuses = []
    for index, (label, baseline, candidate, claimed, markers) in enumerate(cases):
        receipt, status = compare_bytes(
            baseline, candidate, claimed_baseline=claimed, markers=markers
        )
        receipt = {"case_index": index, "demo_case": label, **receipt}
        receipts.append(receipt)
        statuses.append(status)
    expected = [0, 0, 1, 1, 1, 0]
    if statuses != expected:
        raise GainCheckError("demo_assertion_failed", 3)
    report = {
        "schema_version": 1,
        "demo": "passed",
        "measurement_kind": "synthetic_supplied_file_bytes",
        "cases": receipts,
        "expected_statuses": expected,
        "finding_count": statuses.count(1),
        "semantic_equivalence": "unknown",
        "token_outcome": "unknown",
        "billing_outcome": "unknown",
    }
    return report, 0


def _emit(document: dict, human: str | None, json_mode: bool) -> None:
    if json_mode:
        print(canonical_json(document))
    else:
        print(human or "")


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    json_requested = "--json" in args_list
    try:
        args = _parser().parse_args(args_list)
        if args.command == "compare":
            receipt, status = compare_paths(
                Path(args.baseline),
                Path(args.candidate),
                claimed_baseline=(Path(args.claimed_baseline) if args.claimed_baseline else None),
                markers=args.require,
            )
            _emit(receipt, human_compare(receipt), args.json)
            return status
        if args.command == "check":
            report, status = check_manifest(Path(args.manifest))
            _emit(report, human_check(report), args.json)
            return status
        if args.command == "rtk":
            database = args.database_option or args.database
            if not database or (args.database_option and args.database):
                raise GainCheckError("invalid_arguments", 2)
            report, status = audit_rtk(Path(database))
            _emit(report, human_rtk(report), args.json)
            return status
        if args.command == "demo":
            report, status = _demo()
            human = "\n".join(
                [
                    "File measurements only; token, billing and semantic outcomes are unverified.",
                    "Synthetic supplied-file cases (baseline -> candidate bytes):",
                    *[
                        f"Case {case['case_index']} {case['demo_case']}: {case['baseline']['bytes']} -> {case['candidate']['bytes']} bytes; {case['status']} ({', '.join(case['finding_codes']) or 'none'})."
                        for case in report["cases"]
                    ],
                    "Demo assertions passed; findings are arithmetic or declared-marker checks.",
                ]
            )
            _emit(report, human, args.json)
            return status
        raise GainCheckError("invalid_arguments", 2)
    except GainCheckError as exc:
        if json_requested:
            print(canonical_json(error_document(exc.code)))
        else:
            print("gaincheck: operation failed", file=sys.stderr)
        return exc.status
    except (BrokenPipeError, OSError):
        # Do not leak paths, SQL, or interpreter tracebacks to a pipeline.
        return 3
    except Exception:
        # Keep unexpected failures bounded and free of paths, SQL, and tracebacks.
        if json_requested:
            print(canonical_json(error_document("internal_error")))
        else:
            print("gaincheck: internal error; inspect the operation and retry", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
