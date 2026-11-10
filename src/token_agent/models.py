from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SessionCreate(StrictModel):
    repo_path: str = Field(min_length=1, max_length=4096)
    mode: Literal["interactive", "read_only"] = "interactive"


class TaskRequest(StrictModel):
    text: str = Field(min_length=1, max_length=12000)
    allow_research: bool = False


class ApprovalDecision(StrictModel):
    decision: Literal["approve_once", "deny", "deny_with_feedback"]
    feedback: str = Field(default="", max_length=2000)


class ListTree(StrictModel):
    tool: Literal["list_tree"]
    depth: int = Field(default=3, ge=1, le=5)


class ReadFile(StrictModel):
    tool: Literal["read_file"]
    path: str = Field(min_length=1, max_length=1024)
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=100, ge=1, le=100000)


class Grep(StrictModel):
    tool: Literal["grep"]
    pattern: str = Field(min_length=1, max_length=200)


class RepoMap(StrictModel):
    tool: Literal["repo_map"]


class ApplyPatch(StrictModel):
    tool: Literal["apply_patch"]
    path: str = Field(min_length=1, max_length=1024)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    old: str = Field(min_length=1, max_length=12000)
    new: str = Field(max_length=12000)


class SyntaxCheck(StrictModel):
    tool: Literal["syntax_check"]
    path: str = Field(min_length=1, max_length=1024)


class WriteFile(StrictModel):
    tool: Literal["write_file"]
    path: str = Field(min_length=1, max_length=1024)
    expected_sha256: str = Field(pattern=r"^(?:absent|[0-9a-f]{64})$")
    content: str = Field(max_length=12000)


class RunShell(StrictModel):
    tool: Literal["run_shell"]
    argv: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        min_length=1, max_length=32
    )
    purpose: Literal["inspection", "verification"] = "inspection"


class WebResearch(StrictModel):
    tool: Literal["web_research"]
    package: str = Field(min_length=1, max_length=64)
    topic: Literal["usage", "migration", "error"] = "usage"
    error_type: Literal[
        "",
        "ImportError",
        "ModuleNotFoundError",
        "AttributeError",
        "TypeError",
        "ValueError",
        "KeyError",
        "RuntimeError",
        "TimeoutError",
        "ConnectionError",
        "SyntaxError",
    ] = ""


class Finish(StrictModel):
    tool: Literal["finish"]
    summary: str = Field(min_length=1, max_length=4000)


COMMIT_ID = r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$"
GitPath = Annotated[str, Field(min_length=1, max_length=1024)]


def branch_name(value: str) -> str:
    """No option prefixes, "..", "//", "@{", ".lock" parts or trailing "/" and "."."""
    parts = value.split("/")
    if (
        value[0] in "-/."
        or value[-1] in "/."
        or ".." in value
        or "//" in value
        or "@{" in value
        or any(part.endswith(".lock") or part.startswith(".") for part in parts)
    ):
        raise ValueError("Unsafe branch name")
    return value


Branch = Annotated[str, Field(pattern=r"^[A-Za-z0-9._/-]{1,200}$"), AfterValidator(branch_name)]


class GitStatus(StrictModel):
    tool: Literal["git_status"]


class GitDiff(StrictModel):
    tool: Literal["git_diff"]
    staged: bool = False
    paths: list[GitPath] = Field(default_factory=list, max_length=20)


class GitLog(StrictModel):
    tool: Literal["git_log"]
    max_count: int = Field(default=10, ge=1, le=50)
    paths: list[GitPath] = Field(default_factory=list, max_length=20)


class GitCommit(StrictModel):
    tool: Literal["git_commit"]
    message: str = Field(min_length=1, max_length=2000)
    paths: list[GitPath] = Field(min_length=1, max_length=50)
    expected_head: str = Field(pattern=COMMIT_ID)


class GitCheckout(StrictModel):
    tool: Literal["git_checkout"]
    branch: Branch
    create: bool = False
    expected_head: str = Field(pattern=COMMIT_ID)


class GitPush(StrictModel):
    tool: Literal["git_push"]
    remote: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    branch: Branch
    expected_head: str = Field(pattern=COMMIT_ID)


Action = Annotated[
    ListTree
    | ReadFile
    | Grep
    | RepoMap
    | ApplyPatch
    | WriteFile
    | RunShell
    | WebResearch
    | SyntaxCheck
    | GitStatus
    | GitDiff
    | GitLog
    | GitCommit
    | GitCheckout
    | GitPush
    | Finish,
    Field(discriminator="tool"),
]


class AgentReply(StrictModel):
    action: Action


class Event(StrictModel):
    schema_version: Literal[1] = 1
    id: int
    session_id: str
    run_id: str | None
    type: str
    timestamp: str
    payload: dict
