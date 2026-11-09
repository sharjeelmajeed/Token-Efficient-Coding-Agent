import hmac
from pathlib import Path
from urllib.parse import urlsplit

from .config import Settings, UserConfig
from .models import (
    ApplyPatch,
    Finish,
    Grep,
    ListTree,
    ReadFile,
    RepoMap,
    RunShell,
    SyntaxCheck,
    WebResearch,
    WriteFile,
)
from .repo import PolicyError

TOOL_CLASSES = {
    ListTree: "read",
    ReadFile: "read",
    Grep: "read",
    RepoMap: "read",
    SyntaxCheck: "read",
    ApplyPatch: "gated",
    WriteFile: "gated",
    RunShell: "gated",
    WebResearch: "read_external",
    Finish: "finish",
}


def authenticate(token: str, tokens: dict[str, str]) -> str | None:
    identity = None
    for owner, candidate in tokens.items():
        if hmac.compare_digest(token.encode(), candidate.encode()):
            identity = owner
    return identity


def resolve_repository(path: str, user: UserConfig, settings: Settings) -> tuple[str, bool]:
    try:
        repo = Path(path).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise PolicyError("Repository does not exist") from exc
    if not repo.is_dir() or not any(
        repo.is_relative_to(Path(r).resolve()) for r in user.repo_roots
    ):
        raise PolicyError("Repository is outside your allowed roots")
    confidential = (repo / settings.confidential_marker).exists() or any(
        repo.is_relative_to(Path(r).resolve()) for r in settings.confidential_roots
    )
    return str(repo), confidential


def classify(action) -> str:
    kind = TOOL_CLASSES.get(type(action))
    if kind is None:
        raise PolicyError("Unregistered tool")
    return kind


def model_policy(settings: Settings, confidential: bool) -> tuple[str, list[str]]:
    if settings.mode == "fixture":
        return "fixture/local", ["fixture/local"]
    if confidential:
        if urlsplit(settings.gateway_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise PolicyError("Confidential sessions require a loopback Bifrost gateway")
        if not settings.local_model or settings.local_model not in settings.local_allowlist:
            raise PolicyError("No allowed local model is configured; session must pause")
        return settings.local_model, settings.local_allowlist
    if settings.standard_model not in settings.standard_allowlist:
        raise PolicyError("Standard model is not allowlisted")
    return settings.standard_model, settings.standard_allowlist


BUDGET_WARNING_PERCENT = 80


def budget_warning_active(limit: int, remaining: int) -> bool:
    """True once at least BUDGET_WARNING_PERCENT of a nonzero budget is committed."""
    return limit > 0 and remaining * 100 <= limit * (100 - BUDGET_WARNING_PERCENT)


def budget_payload(limit: int, remaining: int, options: list[str]) -> dict:
    return {
        "remaining_micro_usd": remaining,
        "limit_micro_usd": limit,
        "warning_percent": BUDGET_WARNING_PERCENT,
        "options": options,
    }


def cost_ceiling(settings: Settings, incoming: int, outgoing: int, local: bool) -> int:
    if local or settings.mode == "fixture":
        return 0
    total = (
        incoming * settings.input_micro_usd_per_million
        + outgoing * settings.output_micro_usd_per_million
    )
    return (total + 999999) // 1000000


def routed_model(
    settings: Settings, confidential: bool, inspected: bool, failures: int
) -> tuple[str, list[str]]:
    model, allowlist = model_policy(settings, confidential)
    if (
        not confidential
        and settings.mode == "bifrost"
        and settings.optimization.tiered_routing
        and settings.fast_model
        and not inspected
        and failures == 0
    ):
        if settings.fast_model not in allowlist:
            raise PolicyError("Fast model is not allowlisted")
        model = settings.fast_model
    return model, allowlist
