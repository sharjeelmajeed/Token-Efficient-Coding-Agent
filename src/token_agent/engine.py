import asyncio
import json
import logging
import os
import re
import time
from collections import Counter

from pydantic import ValidationError

from .config import Settings
from .context import build_context, enabled_tools
from .models import AgentReply, ApplyPatch, Finish, ReadFile, RunShell, WebResearch, WriteFile
from .policy import (
    budget_payload,
    budget_warning_active,
    classify,
    cost_ceiling,
    model_policy,
    resolve_repository,
    routed_model,
)
from .providers import BifrostProvider, FixtureProvider, ProviderError
from .repo import PolicyError, Repository
from .research import Research
from .sandbox import DockerSandbox
from .store import Store

logger = logging.getLogger("token_agent")
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
    ):
        self.settings, self.store, self.tokens = settings, store, tokens
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
        return Repository(
            session["repo"], self.settings.confidential_marker, secrets, self.settings.output_chars
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
        path, confidential = resolve_repository(session["repo"], user, self.settings)
        if path != session["repo"]:
            raise PolicyError("Repository location changed; create a new session")
        if confidential:
            self.store.mark_confidential(session["repo"])
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
        action: ApplyPatch | WriteFile | RunShell,
        repository: Repository,
        step: int,
    ) -> dict:
        if self.refresh_session(session_id)["read_only"]:
            raise PolicyError("Read-only sessions cannot request gated actions")
        binding = action.model_dump()
        if isinstance(action, RunShell):
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
                if isinstance(action, RunShell):
                    result = await self.sandbox.run(repository, action, snapshot)
                else:
                    # No await between single-use approval consumption and the checked write.
                    result = (
                        repository.apply(action)
                        if isinstance(action, ApplyPatch)
                        else repository.write(action)
                    )
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

    async def execute(self, session_id: str, run_id: str):
        session = self.store.session(session_id)
        user = next(u for u in self.settings.users if u.id == session["owner"])
        options = json.loads(self.store.run(run_id)["options"])
        seen, failures = Counter(), Counter()
        checks: set[tuple[str, ...]] = set()
        required_checks = {tuple(cmd) for cmd in self.settings.sandbox.verification_commands}
        verified_snapshot = None
        inspected = False
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
                },
            )
            for step in range(1, self.settings.max_steps + 1):
                session = self.refresh_session(session_id)
                confidential = bool(session["confidential"])
                model, allowlist = routed_model(
                    self.settings, confidential, inspected, sum(failures.values())
                )
                names = enabled_tools(self.settings, inspected, bool(session["read_only"]))
                messages, context_metrics = build_context(
                    self.store.messages(session_id), self.settings, names
                )
                self.emit(session_id, run_id, "context_prepared", context_metrics)
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
                result = await self.provider.complete(
                    messages, model, user, confidential, request_context=request_context
                )
                if self.refresh_session(session_id)["confidential"] and not confidential:
                    raise PolicyError(
                        "Repository became confidential during request; inspect and resume locally"
                    )
                if result.model not in allowlist:
                    raise PolicyError("Gateway returned a model outside the configured allowlist")
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
                if action.tool not in names:
                    raise PolicyError("Tool is disabled or requires a file inspection first")
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
                    self.emit(session_id, run_id, "message_delta", {"text": action.summary})
                    self.complete(session_id, run_id, step, verified)
                    return
                self.emit(
                    session_id, run_id, "tool_call", {"action": action.model_dump(), "class": kind}
                )
                if isinstance(action, (ApplyPatch, WriteFile, RunShell)):
                    output = await self.wait_for_approval(session_id, run_id, action, repo, step)
                elif isinstance(action, WebResearch):
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
                                "cache_hit",
                                "credits",
                                "content_utf8_bytes",
                                "content_token_upper_bound",
                            )
                        },
                    )
                else:
                    output = repo.execute_read(action)
                if isinstance(action, ReadFile):
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
            if reason == "budget_hit":
                self.emit(
                    session_id,
                    run_id,
                    "budget_hit",
                    {
                        "reason": reason,
                        **budget_payload(
                            user.budget_micro_usd,
                            self.store.remaining(user.id),
                            ["raise_budget_then_resubmit"],
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
            self.emit(session_id, run_id, "session_paused", {"reason": "internal_error"})
        finally:
            if reservation:
                self.store.uncertain(reservation)
