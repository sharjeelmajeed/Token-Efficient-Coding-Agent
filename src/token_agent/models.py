from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


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
