"""Clean-install regression for the bundled Hermes runtime bootstrap."""

from pathlib import Path
import subprocess
import sys


def _run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


def test_bootstrap_clones_and_updates_complete_runtime(tmp_path) -> None:
    source = tmp_path / "source"
    app = source / "app"
    app.mkdir(parents=True)
    (app / "cli.py").write_text("VERSION = 1\n", encoding="utf-8")
    (app / "requirements.txt").write_text("", encoding="utf-8")
    _run(["git", "init", "-b", "main"], source)
    _run(["git", "config", "user.name", "Bootstrap Test"], source)
    _run(["git", "config", "user.email", "bootstrap@example.invalid"], source)
    _run(["git", "add", "."], source)
    _run(["git", "commit", "-m", "initial"], source)

    script = Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_runtime.py"
    install_root = tmp_path / "installed"
    first = subprocess.run(
        [
            sys.executable, str(script), "--repo", str(source), "--ref", "main",
            "--root", str(install_root), "--skip-install",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    installed_cli = install_root / "runtime" / "app" / "cli.py"
    assert installed_cli.read_text(encoding="utf-8") == "VERSION = 1\n"
    assert f"APP_DIR={installed_cli.parent}" in first.stdout
    assert "PYTHON=" in first.stdout

    (app / "cli.py").write_text("VERSION = 2\n", encoding="utf-8")
    _run(["git", "add", "."], source)
    _run(["git", "commit", "-m", "update"], source)
    second = subprocess.run(
        [
            sys.executable, str(script), "--repo", str(source), "--ref", "main",
            "--root", str(install_root), "--skip-install",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert installed_cli.read_text(encoding="utf-8") == "VERSION = 2\n"
    assert "Updating existing runtime" in second.stdout


def test_skill_references_bootstrap_for_hermes_url_bundle() -> None:
    root = Path(__file__).resolve().parents[2]
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    assert "[runtime bootstrap](scripts/bootstrap_runtime.py)" in skill
    assert (root / "scripts" / "bootstrap_runtime.py").is_file()
