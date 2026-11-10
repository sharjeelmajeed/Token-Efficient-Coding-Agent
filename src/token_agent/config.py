import os
import re
import tomllib
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResearchPackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
    version: str = Field(default="", pattern=r"^[0-9A-Za-z.+_-]{0,40}$")
    domains: list[str] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def domains_are_hostnames(self):
        if any(not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", d) for d in self.domains):
            raise ValueError("Research domains must be plain lowercase hostnames")
        return self


class ResearchSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    allow_confidential: bool = False
    key_env: str = "TAVILY_API_KEY"
    max_sources: int = Field(default=3, ge=1, le=5)
    max_calls_per_run: int = Field(default=3, ge=1, le=10)
    cache_seconds: int = Field(default=3600, ge=0, le=86400)
    result_chars: int = Field(default=3000, ge=1000, le=8000)
    packages: dict[str, ResearchPackage] = Field(default_factory=dict)


class SandboxSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    image: str = ""
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    memory_mb: int = Field(default=256, ge=64, le=2048)
    max_snapshot_bytes: int = Field(default=8000000, ge=100000, le=32000000)
    verification_commands: list[list[str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def pinned_image(self):
        if self.enabled and not re.fullmatch(r"[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}", self.image):
            raise ValueError("Enabled sandbox requires a trusted image pinned by SHA-256 digest")
        if any(
            not cmd or any(not arg or "\x00" in arg for arg in cmd)
            for cmd in self.verification_commands
        ):
            raise ValueError("Verification commands must be nonempty argv arrays")
        return self


class GitSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    # Remote name -> the exact URL it must have. An empty map means push is never allowed.
    push_remotes: dict[str, str] = Field(default_factory=dict)
    # Remotes a confidential repository may push to; they must also be in push_remotes.
    confidential_push_remotes: list[str] = Field(default_factory=list)
    protected_branches: list[str] = Field(default_factory=lambda: ["main", "master"])

    @model_validator(mode="after")
    def trusted_remotes(self):
        for name, url in self.push_remotes.items():
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
                raise ValueError("Git remote names must be plain identifiers")
            parts = urlsplit(url)
            if parts.scheme not in {"https", "ssh"} or not parts.hostname:
                raise ValueError("Git push remotes need an https:// or ssh:// URL")
            if parts.password or parts.query or parts.fragment:
                raise ValueError("Git remote URLs may not contain secrets, query or fragment")
            if parts.scheme == "https" and parts.username:
                raise ValueError("Git remote URLs may not contain credentials")
        if any(name not in self.push_remotes for name in self.confidential_push_remotes):
            raise ValueError("Confidential push remotes must also be push remotes")
        return self


class OptimizationSettings(BaseModel):
    """Independent switches for the nine FSD section 8 techniques, for ablation runs.

    Each switch changes only how efficiently the agent works. Safety limits (repository
    boundaries, secrets, approvals, budget and locality) are never switched off.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    repo_map: bool = True  # TO-01: offer the symbol outline tool
    targeted_reads: bool = True  # TO-02: line-range reads and grep instead of whole files
    output_truncation: bool = True  # TO-03: cut tool output to output_chars
    diff_edits: bool = True  # TO-04: exact patches instead of whole-file writes
    history_compaction: bool = True
    tool_pruning: bool = True
    tiered_routing: bool = False
    gateway_direct_cache: bool = False
    early_termination: bool = True


class UserConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    token_env: str
    repo_roots: list[str]
    budget_micro_usd: int = Field(default=1000000, ge=0)
    bifrost_key_env: str = "BIFROST_VIRTUAL_KEY"
    local_key_env: str = "BIFROST_LOCAL_VIRTUAL_KEY"
    read_only: bool = False


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    database: str = ".agent/state.sqlite3"
    mode: Literal["fixture", "bifrost"] = "fixture"
    gateway_url: str = "http://127.0.0.1:8080/v1/chat/completions"
    standard_model: str = ""
    local_model: str = ""
    standard_allowlist: list[str] = Field(default_factory=list)
    local_allowlist: list[str] = Field(default_factory=list)
    standard_fallbacks: list[str] = Field(default_factory=list)
    local_fallbacks: list[str] = Field(default_factory=list)
    fast_model: str = ""
    gateway_attempt_ceiling: int = Field(default=1, ge=1, le=20)
    # Longest wait for the gateway's next bytes. Raise it for slow local models on CPU,
    # which can take minutes before the first token of a long prompt.
    gateway_timeout_seconds: int = Field(default=60, ge=5, le=900)
    streaming: bool = False
    research: ResearchSettings = Field(default_factory=ResearchSettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    git: GitSettings = Field(default_factory=GitSettings)
    optimization: OptimizationSettings = Field(default_factory=OptimizationSettings)
    confidential_roots: list[str] = Field(default_factory=list)
    confidential_marker: str = ".agent-confidential"
    max_steps: int = Field(default=12, ge=1, le=100)
    max_output_tokens: int = Field(default=2048, ge=128, le=16384)
    output_chars: int = Field(default=8000, ge=1000, le=20000)
    # FR-12: when set, tool output is cut to this many tokens instead of output_chars, using
    # the characters-per-token measured from the model's own count on the current request.
    tool_output_tokens: int | None = Field(default=None, ge=100, le=20000)
    context_chars: int = Field(default=40000, ge=4000, le=100000)
    approval_timeout_seconds: int = Field(default=120, ge=1, le=3600)
    # Conservative ceilings for every model in the configured fallback chain.
    input_micro_usd_per_million: int = Field(default=0, ge=0)
    output_micro_usd_per_million: int = Field(default=0, ge=0)
    users: list[UserConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_policy(self):
        if len({u.id for u in self.users}) != len(self.users):
            raise ValueError("User IDs must be unique")
        if any(not u.repo_roots for u in self.users):
            raise ValueError("Each user needs at least one repository root")
        if self.confidential_marker != Path(self.confidential_marker).name:
            raise ValueError("Confidential marker must be a filename")
        url = urlsplit(self.gateway_url)
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("Gateway URL may not contain credentials, query or fragment")
        if url.scheme != "https" and not (
            url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("Gateway must use HTTPS or loopback HTTP")
        if self.mode == "bifrost":
            if not self.standard_model or self.standard_model not in self.standard_allowlist:
                raise ValueError("Configure the standard model and allowlist")
            if self.input_micro_usd_per_million == 0 or self.output_micro_usd_per_million == 0:
                raise ValueError("Live mode needs conservative nonzero pricing ceilings")
            if self.local_model and self.local_model not in self.local_allowlist:
                raise ValueError("Local model must be explicitly allowlisted")
            if self.fast_model and self.fast_model not in self.standard_allowlist:
                raise ValueError("Fast model must be explicitly allowlisted")
            if any(m not in self.standard_allowlist for m in self.standard_fallbacks):
                raise ValueError("Standard fallbacks must be allowlisted")
            if any(m not in self.local_allowlist for m in self.local_fallbacks):
                raise ValueError("Local fallbacks must be locally allowlisted")
            if self.gateway_attempt_ceiling < 1 + max(
                len(self.standard_fallbacks), len(self.local_fallbacks)
            ):
                raise ValueError("Attempt ceiling must cover all configured fallback attempts")
        return self


def load_settings(path: str | Path) -> Settings:
    path = Path(path).resolve()
    with path.open("rb") as stream:
        settings = Settings.model_validate(tomllib.load(stream))

    # Config-relative paths keep CLI and server behavior independent of cwd.
    def absolute(value: str) -> str:
        return str((path.parent / value).resolve())

    users = [
        u.model_copy(update={"repo_roots": [absolute(p) for p in u.repo_roots]})
        for u in settings.users
    ]
    return settings.model_copy(
        update={
            "database": absolute(settings.database),
            "confidential_roots": [absolute(p) for p in settings.confidential_roots],
            "users": users,
        }
    )


def load_tokens(settings: Settings) -> dict[str, str]:
    tokens = {u.id: os.environ.get(u.token_env, "") for u in settings.users}
    if any(len(token) < 32 for token in tokens.values()):
        raise ValueError(
            "Every API token must be supplied via its environment variable (32+ chars)"
        )
    if len(set(tokens.values())) != len(tokens):
        raise ValueError("Users must have different API tokens")
    return tokens
