import asyncio
import json
import logging
import os
import re
import time
from collections import Counter

from pydantic import ValidationError

from .config import Settings
from .context import (
    ATTRIBUTION_METHOD,
    attribute_tokens,
    build_context,
    chars_per_token,
    enabled_tools,
    observation,
    output_char_limit,
    prompt_parts,
)
from .git_contract import GIT_GATED_TYPES, GIT_READ_TYPES, GitExecutor, GitGated
from .models import AgentReply, ApplyPatch, Finish, ReadFile, RunShell, WebResearch, WriteFile
from .policy import (
    budget_payload,
    budget_warning_active,
    check_git_action,
    classify,
    cost_ceiling,
    model_policy,
    resolve_repository,
    routed_model,
)
from .providers import GATEWAY_BUDGET_HIT, BifrostProvider, FixtureProvider, ProviderError
from .repo import (
    ABSOLUTE_OUTPUT_CHARS,
    MAX_FILE_BYTES,
    MAX_READ_LINES,
    PolicyError,
    Repository,
    ToolInputError,
)
from .research import Research
from .sandbox import DockerSandbox
from .store import Store
from .streaming import TextStream

logger = logging.getLogger("token_agent")
# Consecutive model input mistakes allowed before the run pauses for a human.
MAX_TOOL_ERRORS = 3
ERROR_NAMES = re.compile(
    r"\b(ImportError|ModuleNotFoundError|AttributeError|TypeError|ValueError|KeyError|"
    r"RuntimeError|TimeoutError|ConnectionError|SyntaxError)\b"
)


class Engine:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        tokens: dict[str, str],
        provider=None,
        research=None,
        sandbox=None,
        git: GitExecutor | None = None,
    ):
        self.settings, self.store, self.tokens = settings, store, tokens
        # No git executor ships yet, so git tools stay off unless one is supplied.
        self.git = git
        self.provider = provider or (
            FixtureProvider() if settings.mode == "fixture" else BifrostProvider(settings)
        )
        self.research = research or Research(settings.research, store)
        self.sandbox = sandbox or DockerSandbox(settings.sandbox)
        self.tasks: dict[str, asyncio.Task] = {}

    def repository(self, session: dict) -> Repository:
        secrets = [*self.tokens.values(), os.environ.get(self.settings.research.key_env, "")]
        for user in self.settings.users:
            secrets.extend(
                [os.environ.get(user.bifrost_key_env, ""), os.environ.get(user.local_key_env, "")]
            )
        optimization = self.settings.optimization
        return Repository(
            session["repo"],
            self.settings.confidential_marker,
            secrets,
            self.settings.output_chars if optimization.output_truncation else ABSOLUTE_OUTPUT_CHARS,
            MAX_READ_LINES if optimization.targeted_reads else None,
        )

    def emit(self, session_id: str, run_id: str, kind: str, payload: dict):
        self.store.event(session_id, run_id, kind, payload)
        logger.info(json.dumps({"session_id": session_id, "run_id": run_id, "event": kind}))

    def start(self, session_id: str, text: str, allow_research: bool = False) -> str:
        session = self.refresh_session(session_id)
        self.repository(session).check_secrets(text)
        run_id = self.store.start_run(session_id, {"allow_research": allow_research})
        self.store.add_message(session_id, "user", text)
        task = asyncio.create_task(self.execute(session_id, run_id))
        self.tasks[session_id] = task
        task.add_done_callback(
            lambda done: (
                self.tasks.pop(session_id, None) if self.tasks.get(session_id) is done else None
            )
        )
        return run_id

    def refresh_session(self, session_id: str) -> dict:
        session = self.store.session(session_id)
        user = next(u for u in self.settings.users if u.id == session["owner"])
        path, sources = resolve_repository(session["repo"], user, self.settings)
        if path != session["repo"]:
            raise PolicyError("Repository location changed; create a new session")
        for source in sources:
            self.store.mark_confidential(source)
        if user.read_only:
            self.store.restrict_session(session_id)
        return self.store.session(session_id)

    async def cancel(self, session_id: str):
        run = self.store.latest_run(session_id)
        self.store.cancel(session_id)
        if task := self.tasks.get(session_id):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if run and run["status"] in {"running", "waiting_approval"}:
            self.emit(session_id, run["id"], "run_complete", {"outcome": "cancelled"})

    async def close(self):
        pending = list(self.tasks.values())
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self.store.recover()

    async def wait_for_approval(
        self,
        session_id: str,
        run_id: str,
        action: ApplyPatch | WriteFile | RunShell | GitGated,
        repository: Repository,
        step: int,
    ) -> dict:
        session = self.refresh_session(session_id)
        if session["read_only"]:
            raise PolicyError("Read-only sessions cannot request gated actions")
        binding = action.model_dump()
        if isinstance(action, GIT_GATED_TYPES):
            check_git_action(action, self.settings, bool(session["confidential"]), repository)
            preview = await self.git.prepare(repository, action)
            repository.check_secrets(preview.diff)
            binding["git"] = dict(preview.state)
            diff = preview.diff
        elif isinstance(action, RunShell):
            if not self.sandbox.available():
                raise PolicyError("Docker sandbox is unavailable; host execution is forbidden")
            _, snapshot = repository.snapshot(self.settings.sandbox.max_snapshot_bytes)
            binding["execution"] = {
                "snapshot_sha256": snapshot,
                "image": self.settings.sandbox.image,
                "timeout_seconds": self.settings.sandbox.timeout_seconds,
                "memory_mb": self.settings.sandbox.memory_mb,
                "network": "none",
                "repository_writable": False,
            }
            diff = ""
        else:
            _, diff = (
                repository.prepare_patch(action)
                if isinstance(action, ApplyPatch)
                else repository.prepare_write(action)
            )
        approval = self.store.make_approval(
            session_id, run_id, binding, diff, self.settings.approval_timeout_seconds
        )
        self.store.update_run(run_id, "waiting_approval", step)
        self.emit(
            session_id,
            run_id,
            "approval_request",
            {
                "approval_id": approval["id"],
                "action": binding,
                "diff": diff,
                "deadline": approval["deadline"],
                "tool_class": "gated",
            },
        )
        while True:
            current = self.store.approval(approval["id"])
            if self.store.run(run_id)["status"] == "cancelled":
                raise asyncio.CancelledError
            if current["status"] == "denied":
                self.store.update_run(run_id, "running", step)
                return {"denied": True, "feedback": current["feedback"]}
            if current["deadline"] <= time.time():
                self.store.expire_approval(approval["id"])
                self.emit(session_id, run_id, "approval_expired", {"approval_id": approval["id"]})
                self.store.update_run(run_id, "running", step)
                return {"denied": True, "feedback": "Approval expired"}
            if current["status"] == "approved":
                if not self.store.claim_approval(approval["id"]):
                    raise PolicyError("Approval could not be consumed safely")
                self.emit(session_id, run_id, "approval_consumed", {"approval_id": approval["id"]})
                try:
                    if isinstance(action, GIT_GATED_TYPES):
                        # The executor rechecks every bound state value before it changes anything.
                        result = await self.git.apply(repository, action, binding["git"])
                        repository.check_secrets(json.dumps(result))
                    elif isinstance(action, RunShell):
                        result = await self.sandbox.run(repository, action, snapshot)
                    else:
                        # No await between single-use approval consumption and the checked write.
                        result = (
                            repository.apply(action)
                            if isinstance(action, ApplyPatch)
                            else repository.write(action)
                        )
                except ToolInputError as exc:
                    # A human approved this exact change, so a failure now pauses for them.
                    raise PolicyError(str(exc)) from exc
                self.store.update_run(run_id, "running", step)
                return result
            await asyncio.sleep(0.1)

    def complete(self, session_id: str, run_id: str, step: int, verified: bool):
        outcome = "completed_verified" if verified else "completed_unverified"
        self.store.update_run(run_id, "completed", step, outcome)
        self.emit(
            session_id,
            run_id,
            "run_complete",
            {
                "outcome": outcome,
                "note": "All configured checks passed on the unchanged sanitized snapshot"
                if verified
                else "No complete set of configured behavior checks passed",
            },
        )

    async def run_tool(
        self, session_id, run_id, session, action, repo, step, options, failures
    ) -> dict:
        if isinstance(action, (ApplyPatch, WriteFile, RunShell, *GIT_GATED_TYPES)):
            return await self.wait_for_approval(session_id, run_id, action, repo, step)
        if isinstance(action, GIT_READ_TYPES):
            check_git_action(action, self.settings, bool(session["confidential"]), repo)
            output = await self.git.read(repo, action)
            repo.check_secrets(json.dumps(output))
            return output
        if isinstance(action, WebResearch):
            output = await self.research.search(
                action,
                session,
                run_id,
                options.get("allow_research") is True,
                failures,
                repo,
            )
            self.emit(
                session_id,
                run_id,
                "research_used",
                {
                    k: output[k]
                    for k in (
                        "query",
                        "trigger",
                        "cache_hit",
                        "credits",
                        "content_utf8_bytes",
                        "content_token_upper_bound",
                    )
                },
            )
            return output
        return repo.execute_read(action)

    async def execute(self, session_id: str, run_id: str):
        session = self.store.session(session_id)
        user = next(u for u in self.settings.users if u.id == session["owner"])
        options = json.loads(self.store.run(run_id)["options"])
        seen, failures = Counter(), Counter()
        checks: set[tuple[str, ...]] = set()
        required_checks = {tuple(cmd) for cmd in self.settings.sandbox.verification_commands}
        verified_snapshot = None
        # A file read earlier in this session still counts: hashes guard against stale edits.
        inspected = any(
            (item := observation(message)) is not None
            and item.get("tool") == "read_file"
            and "error" not in item["tool_result"]
            for message in self.store.messages(session_id)
        )
        tool_errors = 0
        warned = False
        step, reservation, previous_model = 0, None, None
        try:
            repo = self.repository(session)
            confidential = bool(session["confidential"])
            model, _ = model_policy(self.settings, confidential)
            self.emit(
                session_id,
                run_id,
                "run_started",
                {
                    "model": model,
                    "mode": self.settings.mode,
                    "confidential": confidential,
                    "budget_remaining_micro_usd": self.store.remaining(user.id),
                    # Recorded so every result can be traced to its ablation configuration.
                    "optimization": self.settings.optimization.model_dump(),
                },
            )
            for step in range(1, self.settings.max_steps + 1):
                session = self.refresh_session(session_id)
                confidential = bool(session["confidential"])
                model, allowlist = routed_model(
                    self.settings, confidential, inspected, sum(failures.values())
                )
                names = enabled_tools(
                    self.settings, inspected, bool(session["read_only"]), self.git is not None
                )
                messages, context_metrics = build_context(
                    self.store.messages(session_id), self.settings, names
                )
                self.emit(session_id, run_id, "context_prepared", context_metrics)
                parts = prompt_parts(messages)
                request_context = {"session_id": session_id, "run_id": run_id}
                if self.settings.optimization.gateway_direct_cache and not confidential:
                    _, request_context["snapshot_sha256"] = repo.snapshot(
                        self.settings.sandbox.max_snapshot_bytes
                    )
                reserve_cost = (
                    cost_ceiling(
                        self.settings,
                        len(json.dumps(messages, ensure_ascii=False).encode()) + 2048,
                        self.settings.max_output_tokens,
                        confidential,
                    )
                    * self.settings.gateway_attempt_ceiling
                )
                reservation = self.store.reserve(
                    user.id,
                    run_id,
                    reserve_cost,
                    model,
                    "fixture"
                    if self.settings.mode == "fixture"
                    else "pricing_ceiling_pending_reconciliation",
                )
                request_context["request_id"] = reservation
                stream, stream_options = None, {}
                if self.settings.streaming:
                    stream = TextStream(
                        repo.check_secrets,
                        lambda text: self.emit(
                            session_id, run_id, "message_delta", {"text": text, "partial": True}
                        ),
                        repo.secrets,
                    )
                    stream_options["on_text"] = stream.feed
                result = await self.provider.complete(
                    messages,
                    model,
                    user,
                    confidential,
                    request_context=request_context,
                    **stream_options,
                )
                if self.refresh_session(session_id)["confidential"] and not confidential:
                    raise PolicyError(
                        "Repository became confidential during request; inspect and resume locally"
                    )
                if result.model not in allowlist:
                    raise PolicyError("Gateway returned a model outside the configured allowlist")
                if (
                    self.settings.optimization.output_truncation
                    and self.settings.tool_output_tokens
                ):
                    # Size this step's tool output from the model's measured token density.
                    repo.output_chars = output_char_limit(
                        self.settings.tool_output_tokens,
                        chars_per_token(messages, result.input_tokens),
                        MAX_FILE_BYTES,
                    )
                actual_cost = cost_ceiling(
                    self.settings, result.input_tokens, result.output_tokens, confidential
                )
                self.store.settle(
                    reservation,
                    actual_cost,
                    result.input_tokens,
                    result.output_tokens,
                    result.model,
                    details={
                        "cached_tokens": result.cached_tokens,
                        "cache_hit": result.cache_hit,
                        "latency_ms": result.latency_ms,
                        "request_id": result.request_id,
                        "usage_source": result.source,
                        "attempt_ceiling": self.settings.gateway_attempt_ceiling,
                        "tool_output_char_limit": repo.output_chars,
                        # Where the request's real prompt tokens went (FR-62, ablation analysis).
                        "prompt_chars_by_part": parts,
                        "prompt_tokens_by_part": attribute_tokens(parts, result.input_tokens),
                        "attribution": ATTRIBUTION_METHOD,
                    },
                    retain_reservation=(
                        self.settings.mode != "fixture"
                        and (
                            self.settings.gateway_attempt_ceiling > 1
                            or result.is_fallback
                            or result.model != model
                        )
                    ),
                )
                reservation = None
                if previous_model and model != previous_model:
                    self.emit(
                        session_id,
                        run_id,
                        "model_switched",
                        {"from": previous_model, "to": model, "reason": "configured_step_routing"},
                    )
                if result.model != model or result.is_fallback:
                    self.emit(
                        session_id,
                        run_id,
                        "model_switched",
                        {
                            "from": model,
                            "to": result.model,
                            "reason": "gateway_fallback"
                            if result.is_fallback
                            else "gateway_returned_different_allowed_model",
                        },
                    )
                previous_model = result.model
                remaining = self.store.remaining(user.id)
                if not warned and budget_warning_active(user.budget_micro_usd, remaining):
                    warned = True
                    self.emit(
                        session_id,
                        run_id,
                        "budget_warning",
                        budget_payload(user.budget_micro_usd, remaining, ["continue", "cancel"]),
                    )
                repo.check_secrets(result.content)
                reply = AgentReply.model_validate_json(result.content)
                action = reply.action
                kind = classify(action)
                if session["read_only"] and kind == "gated":
                    raise PolicyError("Read-only sessions cannot execute gated actions")
                read_first = None
                if action.tool not in names:
                    later = enabled_tools(
                        self.settings, True, bool(session["read_only"]), self.git is not None
                    )
                    if action.tool not in later:
                        raise PolicyError("Tool is disabled or requires a file inspection first")
                    read_first = "Read the file with read_file before editing it"
                fingerprint = action.model_dump_json()
                seen[fingerprint] += 1
                if seen[fingerprint] >= 3:
                    raise PolicyError("Repeated action detected; paused without progress")
                self.store.add_message(session_id, "assistant", reply.model_dump_json(), "action")
                if isinstance(action, Finish):
                    verified = bool(
                        required_checks
                        and checks == required_checks
                        and repo.snapshot(self.settings.sandbox.max_snapshot_bytes)[1]
                        == verified_snapshot
                    )
                    final = (
                        {"text": stream.remainder(action.summary), "partial": False}
                        if stream
                        else {"text": action.summary}
                    )
                    self.emit(session_id, run_id, "message_delta", final)
                    self.complete(session_id, run_id, step, verified)
                    return
                self.emit(
                    session_id, run_id, "tool_call", {"action": action.model_dump(), "class": kind}
                )
                try:
                    if read_first:
                        raise ToolInputError(read_first)
                    output = await self.run_tool(
                        session_id, run_id, session, action, repo, step, options, failures
                    )
                except ToolInputError as exc:
                    tool_errors += 1
                    if tool_errors >= MAX_TOOL_ERRORS:
                        raise PolicyError(f"{exc} (repeated tool input errors)") from exc
                    # Nothing changed; tell the model so it can correct its own input.
                    output = {"error": str(exc), "recoverable": True, "changed": False}
                else:
                    tool_errors = 0
                if isinstance(action, ReadFile) and "error" not in output:
                    inspected = True
                if output.get("applied"):
                    checks.clear()
                    verified_snapshot = None
                if action.tool in {"syntax_check", "run_shell"} and (
                    output.get("passed") is False or output.get("exit_code", 0) != 0
                ):
                    observed = set(ERROR_NAMES.findall(json.dumps(output)))
                    failures.update(observed)
                if isinstance(action, RunShell) and output.get("verification_passed"):
                    if verified_snapshot != output["snapshot_sha256"]:
                        checks.clear()
                    verified_snapshot = output["snapshot_sha256"]
                    checks.add(tuple(action.argv))
                elif isinstance(action, RunShell) and action.purpose == "verification":
                    checks.discard(tuple(action.argv))
                self.store.add_message(
                    session_id,
                    "user",
                    json.dumps({"tool": action.tool, "tool_result": output}),
                    "observation",
                )
                self.emit(
                    session_id, run_id, "tool_result", {"tool": action.tool, "result": output}
                )
                self.store.update_run(run_id, "running", step)
                if (
                    self.settings.optimization.early_termination
                    and required_checks
                    and checks == required_checks
                    and repo.snapshot(self.settings.sandbox.max_snapshot_bytes)[1]
                    == verified_snapshot
                ):
                    self.complete(session_id, run_id, step, True)
                    return
            raise PolicyError("Maximum step count reached")
        except asyncio.CancelledError:
            raise
        except (PolicyError, ProviderError, ValidationError) as exc:
            reason = (
                "Model action did not match the permitted schema"
                if isinstance(exc, ValidationError)
                else str(exc)
            )
            self.store.update_run(run_id, "paused", step, reason)
            if reason in ("budget_hit", GATEWAY_BUDGET_HIT):
                self.emit(
                    session_id,
                    run_id,
                    "budget_hit",
                    {
                        "reason": reason,
                        **budget_payload(
                            user.budget_micro_usd,
                            self.store.remaining(user.id),
                            ["raise_budget_then_resubmit"]
                            if reason == "budget_hit"
                            else ["raise_gateway_budget_then_resubmit"],
                        ),
                    },
                )
            else:
                self.emit(session_id, run_id, "policy_block", {"reason": reason})
            self.emit(
                session_id,
                run_id,
                "session_paused",
                {"reason": reason, "resume": "Resolve the cause, then submit a new instruction"},
            )
        except Exception as exc:
            self.store.update_run(run_id, "paused", step, "internal_error")
            logger.error(
                json.dumps(
                    {
                        "event": "internal_error",
                        "run_id": run_id,
                        "exception_type": type(exc).__name__,
                    }
                )
            )
            self.emit(
                session_id,
                run_id,
                "session_paused",
                {
                    "reason": "internal_error",
                    "resume": "Inspect changes, then submit a new instruction",
                },
            )
        finally:
            if reservation:
                self.store.uncertain(reservation)
