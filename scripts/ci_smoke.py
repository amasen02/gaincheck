"""Exercise a wheel in a fresh, offline virtual environment."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def run(command: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True, env=env)


def assert_demo(executable: Path, cwd: Path, env: dict[str, str], label: str) -> None:
    prefix = [str(executable), "-m", "gaincheck"] if label == "module" else [str(executable)]
    human = run([*prefix, "demo"], cwd, env)
    if not human.stdout.strip():
        raise SystemExit(f"{label} demo produced no stdout")
    receipt = run([*prefix, "demo", "--json"], cwd, env)
    document = json.loads(receipt.stdout)
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise SystemExit(f"{label} demo JSON did not contain schema_version=1")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel-dir", type=Path, required=True)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
