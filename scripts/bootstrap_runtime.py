#!/usr/bin/env python3
"""Idempotently install the Engineering Journey runtime for a URL-installed skill.

Hermes direct skill installs intentionally bundle referenced support files, not
arbitrary repository directories. This small bundled script obtains the one
canonical app checkout instead of duplicating app source inside the skill.
"""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

DEFAULT_REPOSITORY = "https://github.com/schr3b3r/engineering-journey-v2.git"
DEFAULT_REF = "main"


def _run(command: list[str], cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def bootstrap(
    repository: str,
    ref: str,
    root: Path,
    install_dependencies: bool = True,
) -> tuple[Path, Path]:
    root = root.expanduser().resolve()
    checkout = root / "runtime"
    if (checkout / ".git").is_dir():
        print(f"[bootstrap] Updating existing runtime at {checkout}...", flush=True)
        _run(["git", "remote", "set-url", "origin", repository], cwd=checkout)
        _run(["git", "fetch", "--depth", "1", "origin", ref], cwd=checkout)
        _run(["git", "checkout", "--force", "--detach", "FETCH_HEAD"], cwd=checkout)
    else:
        print(f"[bootstrap] Installing runtime into {checkout}...", flush=True)
        root.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--depth", "1", "--branch", ref, repository, str(checkout)])

    app_dir = checkout / "app"
    requirements = app_dir / "requirements.txt"
    if not (app_dir / "cli.py").is_file() or not requirements.is_file():
        raise RuntimeError(
            f"Runtime checkout is incomplete: expected {app_dir}/cli.py and requirements.txt"
        )

    venv = root / ".venv"
    python = venv / "bin" / "python"
    if install_dependencies:
        uv = shutil.which("uv")
        if uv:
            if not python.exists():
                _run([uv, "venv", str(venv)])
            _run([uv, "pip", "install", "--python", str(python), "-r", str(requirements)])
        else:
            if not python.exists():
                _run([sys.executable, "-m", "venv", str(venv)])
            _run([str(python), "-m", "pip", "install", "-r", str(requirements)])
    else:
        python = Path(sys.executable)

    return app_dir, python


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        default=os.environ.get("ENGINEERING_JOURNEY_REPO_URL", DEFAULT_REPOSITORY),
    )
    parser.add_argument(
        "--ref",
        default=os.environ.get("ENGINEERING_JOURNEY_REPO_REF", DEFAULT_REF),
    )
    parser.add_argument(
        "--root",
        default=os.environ.get(
            "ENGINEERING_JOURNEY_HOME", "~/.cache/engineering-journey-v2"
        ),
    )
    parser.add_argument("--skip-install", action="store_true", help="Clone/update only.")
    args = parser.parse_args(argv)
    app_dir, python = bootstrap(
        args.repo, args.ref, Path(args.root), install_dependencies=not args.skip_install
    )
    print("[bootstrap] Runtime ready.", flush=True)
    print(f"APP_DIR={app_dir}")
    print(f"PYTHON={python}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
