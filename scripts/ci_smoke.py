"""Exercise a wheel in a fresh, offline virtual environment."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import venv
from pathlib import Path


def run(command: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True, env=env)


def assert_demo(executable: Path, cwd: Path, env: dict[str, str], label: str) -> None:
    prefix = [str(executable), "-m", "gaincheck"] if label in {"module", "source archive"} else [str(executable)]
    human = run([*prefix, "demo"], cwd, env)
    if not human.stdout.strip():
        raise SystemExit(f"{label} demo produced no stdout")
    receipt = run([*prefix, "demo", "--json"], cwd, env)
    document = json.loads(receipt.stdout)
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise SystemExit(f"{label} demo JSON did not contain schema_version=1")


def extract_archive(archive: Path, destination: Path) -> Path:
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        roots = {member.name.split("/", 1)[0] for member in members if member.name}
        if len(roots) != 1:
            raise SystemExit("source archive must contain one top-level directory")
        root = next(iter(roots))
        destination_resolved = destination.resolve()
        for member in members:
            target = (destination / member.name).resolve()
            if os.path.commonpath((str(destination_resolved), str(target))) != str(destination_resolved):
                raise SystemExit(f"source archive contains an unsafe path: {member.name}")
            if member.issym() or member.islnk():
                raise SystemExit(f"source archive contains a link: {member.name}")
            if not member.isdir() and not member.isreg():
                raise SystemExit(f"source archive contains an unsupported member: {member.name}")
        if sys.version_info >= (3, 12):
            bundle.extractall(destination, filter="data")
        else:
            bundle.extractall(destination)
    return destination / root


def assert_source_archive(source_dir: Path, env: dict[str, str]) -> None:
    archives = sorted(source_dir.resolve().glob("*.tar.gz"))
    if len(archives) != 1:
        raise SystemExit("expected exactly one source archive in --sdist-dir")
    with tempfile.TemporaryDirectory(prefix="gaincheck-sdist-") as temporary:
        extracted = Path(temporary) / "source"
        extracted.mkdir()
        source_root = extract_archive(archives[0], extracted)
        required = (
            "examples/demo-manifest.json",
            "examples/fixtures/empty.txt",
            "examples/rtk/history-standalone.db",
            "docs/cli.md",
            "scripts/build_release.py",
            "tests",
        )
        missing = [path for path in required if not (source_root / path).exists()]
        if missing:
            raise SystemExit(f"source archive is missing required paths: {', '.join(missing)}")
        run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test*.py"],
            source_root,
            env,
        )
        assert_demo(sys.executable, source_root, env, "source archive")
    print("source archive extracted outside checkout, tests and demos passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument("--sdist-dir", type=Path, required=True)
    args = parser.parse_args()
    wheels = sorted(args.wheel_dir.resolve().glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit("expected exactly one wheel in --wheel-dir")

    clean_env = os.environ.copy()
    clean_env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="gaincheck-smoke-") as temporary:
        run_root = Path(temporary) / "run"
        run_root.mkdir()
        environment = Path(temporary) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        executable = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        console = environment / ("Scripts/gaincheck.exe" if sys.platform == "win32" else "bin/gaincheck")
        run([str(executable), "-m", "pip", "install", "--no-index", "--no-deps", str(wheels[0])], run_root, clean_env)
        assert_demo(executable, run_root, clean_env, "module")
        assert_demo(console, run_root, clean_env, "console")
        print("fresh wheel install, module/console demos, and JSON demos passed")
    assert_source_archive(args.sdist_dir, clean_env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
