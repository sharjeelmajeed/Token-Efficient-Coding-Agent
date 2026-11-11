"""Create the pinned test environments that the frozen task checks run in.

- ``python``: a virtual environment with ``environments/requirements-python.txt`` (pytest and
  the libraries the click and jinja tests need). The repositories themselves are not
  installed; checks run them from their ``src`` folder.
- ``node-express5`` and ``node-express4``: ``npm ci`` from the committed package files and
  lockfiles, matching the express 5.x and 4.x tasks. The repositories' own code is used from
  the task workspace, and their test dependencies come from ``NODE_PATH``.

    python benchmarks/setup_environments.py <directory>

Prints the arguments to pass to ``validate_tasks.py`` and the harness.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent / "environments"
NODE_ENVIRONMENTS = ("node-express5", "node-express4")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    target = Path(sys.argv[1]).resolve()
    target.mkdir(parents=True, exist_ok=True)

    python_dir = target / "python"
    if not python_dir.exists():
        venv.EnvBuilder(with_pip=True).create(python_dir)
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    python = python_dir / scripts / ("python.exe" if sys.platform == "win32" else "python")
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--disable-pip-version-check",
            "-r",
            str(HERE / "requirements-python.txt"),
        ],
        check=True,
    )

    npm = shutil.which("npm")
    if npm is None:
        print("npm was not found; the JavaScript tasks need Node.js 18 or later", file=sys.stderr)
        return 1
    arguments = [f"--python {python}"]
    for name in NODE_ENVIRONMENTS:
        folder = target / name
        folder.mkdir(exist_ok=True)
        for file in ("package.json", "package-lock.json"):
            shutil.copyfile(HERE / name / file, folder / file)
        subprocess.run(
            [npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error"],
            cwd=folder,
            check=True,
        )
        arguments.append(f"--node-modules {name}={folder / 'node_modules'}")
    print("Environments ready. Pass these to validate_tasks.py and the harness:")
    print(" ".join(arguments))
    return 0


if __name__ == "__main__":
    sys.exit(main())
