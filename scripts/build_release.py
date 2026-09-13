"""Build reviewed public artifacts without bundling the workspace.

This helper only creates local artifacts. It never publishes and never uses credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
OUT = ROOT / "dist" / "release"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(*args: str, cwd: Path = ROOT) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def git_status() -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "<no-committed-source>"
    return result.stdout.strip()


def source_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    revision = result.stdout.strip()
    if result.returncode != 0:
        return "UNCOMMITTED"
    if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision.lower()):
        raise RuntimeError("git did not return a full source revision")
    return revision


def safe_output_dir() -> Path:
    root = ROOT.resolve()
    raw = ROOT / "dist" / "release"
    if raw.is_symlink():
        raise RuntimeError("dist/release must not be a symlink")
    resolved = raw.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise RuntimeError("dist/release resolves outside the repository")
    if raw.exists() and not raw.is_dir():
        raise RuntimeError("dist/release is not a directory")
    raw.mkdir(parents=True, exist_ok=True)
    return resolved


def clean_known_outputs(output: Path) -> None:
    prefix = f"gaincheck-{VERSION}-"
    fixed = {
        f"gaincheck-{VERSION}.pyz",
        f"gaincheck-{VERSION}.tar.gz",
        "SHA256SUMS",
        "SOURCE-RECEIPT.json",
    }
    for child in output.iterdir():
        is_known_archive = child.name.startswith(prefix) and (
            child.name.endswith(".whl") or child.name.endswith(".tar.gz")
        )
        if child.name in fixed or is_known_archive:
            if child.is_symlink() or not child.is_file():
                raise RuntimeError(f"known output is not a regular file: {child.name}")
            child.unlink()
        else:
            raise RuntimeError(f"refusing to remove unexpected release output: {child.name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-dirty-local",
        action="store_true",
        help="allow a local test build; receipt marks it dirty and not releasable",
    )
    args = parser.parse_args(argv)

    status = git_status()
    dirty = bool(status)
    if dirty and not args.allow_dirty_local:
        raise SystemExit("refusing release build from a dirty tree; commit source or use --allow-dirty-local for a non-releasable local test")
    revision = source_revision()
    package = ROOT / "gaincheck"
    if not package.is_dir():
        raise SystemExit("gaincheck package is missing")

    with tempfile.TemporaryDirectory(prefix="gaincheck-build-") as temporary:
        temporary_root = Path(temporary)
        built = temporary_root / "built"
        built.mkdir()
        run(sys.executable, "-m", "build", "--wheel", "--sdist", "--outdir", str(built))

        wheels = sorted(built.glob("*.whl"))
        sdists = sorted(built.glob("*.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            raise SystemExit("expected exactly one wheel and one source distribution")

        zip_stage = temporary_root / "zip-stage"
        shutil.copytree(
            package,
            zip_stage / "gaincheck",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        license_path = ROOT / "LICENSE"
        if not license_path.is_file():
            raise SystemExit("LICENSE is required in the zipapp source")
        shutil.copy2(license_path, zip_stage / "LICENSE")
        (zip_stage / "__main__.py").write_text(
            "from gaincheck.cli import main\nraise SystemExit(main())\n",
            encoding="utf-8",
        )
        pyz = built / f"gaincheck-{VERSION}.pyz"
        zipapp.create_archive(
            zip_stage,
            pyz,
            interpreter="/usr/bin/env python3",
            compressed=True,
        )

        output = safe_output_dir()
        clean_known_outputs(output)
        public_paths = [wheels[0], sdists[0], pyz]
        for path in public_paths:
            shutil.copy2(path, output / path.name)

        artifact_hashes = {
            path.name: sha256(output / path.name)
            for path in sorted(public_paths, key=lambda item: item.name)
        }
        receipt = {
            "schema_version": 1,
            "artifact_version": VERSION,
            "source_revision": revision,
            "source_clean": not dirty,
            "releasable": not dirty,
            "artifacts": artifact_hashes,
            "builder": "scripts/build_release.py",
            "publishes": False,
        }
        receipt_path = output / "SOURCE-RECEIPT.json"
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        checksummed = sorted([*public_paths, receipt_path], key=lambda item: item.name)
        (output / "SHA256SUMS").write_text(
            "".join(f"{sha256(output / path.name)}  {path.name}\n" for path in checksummed),
            encoding="utf-8",
        )
        state = "clean/releasable" if not dirty else "dirty/non-releasable local test"
        print(f"wrote {len(checksummed)} checksummed public artifacts ({state}) to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
