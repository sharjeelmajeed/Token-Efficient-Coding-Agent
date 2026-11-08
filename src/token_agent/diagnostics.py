import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import Settings, load_tokens


def diagnose(settings: Settings) -> dict:
    checks = []

    def add(name: str, ok: bool, required: bool = True):
        checks.append({"check": name, "ok": ok, "required": required})

    try:
        load_tokens(settings)
        add("API credentials configured and distinct", True)
    except ValueError:
        add("API credentials configured and distinct", False)
    add(
        "Allowed repository roots exist",
        all(Path(r).is_dir() for user in settings.users for r in user.repo_roots),
    )
    if settings.mode == "bifrost":
        add(
            "Standard virtual keys configured",
            all(os.environ.get(u.bifrost_key_env) for u in settings.users),
        )
        add(
            "Local virtual keys configured",
            all(os.environ.get(u.local_key_env) for u in settings.users),
            required=bool(settings.local_model),
        )
    add(
        "Research credential configured",
        bool(os.environ.get(settings.research.key_env)),
        required=settings.research.enabled,
    )
    add(
        "Research package catalog configured",
        bool(settings.research.packages),
        required=settings.research.enabled,
    )
    docker = shutil.which("docker")
    add("Docker executable available", docker is not None, required=settings.sandbox.enabled)
    if settings.sandbox.enabled and docker:
        with tempfile.TemporaryDirectory(prefix="token-agent-doctor-") as directory:
            env = {"PATH": os.environ.get("PATH", ""), "HOME": directory}
            if os.name == "nt":
                env["SystemRoot"] = os.environ.get("SystemRoot", "C:\\Windows")
            for name, args in [
                ("Docker daemon reachable", ["info", "--format", "{{.ServerVersion}}"]),
                ("Pinned image already present", ["image", "inspect", settings.sandbox.image]),
            ]:
                try:
                    result = subprocess.run(
                        [docker, *args],
                        env=env,
                        timeout=8,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    add(name, result.returncode == 0)
                except (OSError, subprocess.TimeoutExpired):
                    add(name, False)
    return {
        "ready": all(c["ok"] for c in checks if c["required"]),
        "mode": settings.mode,
        "checks": checks,
        "live_provider_verified": False,
        "note": "Checks do not call model or research APIs; no secret values are displayed",
    }
