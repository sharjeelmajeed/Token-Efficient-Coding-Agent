import asyncio
import json
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings, load_tokens
from .context import ATTRIBUTION_METHOD
from .engine import Engine
from .models import ApplyPatch, ApprovalDecision, SessionCreate, TaskRequest, WriteFile
from .policy import authenticate, budget_warning_active, resolve_repository
from .repo import PolicyError
from .store import Store

TERMINAL = {"completed", "cancelled", "paused"}


class BodyLimit:
    def __init__(self, app, limit: int = 128000):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.limit:
                response = JSONResponse({"detail": "Request body exceeds limit"}, status_code=413)
                return await response(scope, receive, send)
            chunks.append(message)
            if not message.get("more_body", False):
                break

        async def replay():
            return chunks.pop(0) if chunks else await receive()

        return await self.app(scope, replay, send)


def token_attribution(usage: list[dict]) -> dict:
    """Run totals of real prompt tokens by part, summed over every model request.

    Text that stays in the context, such as a research result, is counted in every request
    that resends it, so its total here is its real cost to the run.
    """
    by_part: dict[str, int] = {}
    missing = 0
    for row in usage:
        shares = row["details"].get("prompt_tokens_by_part")
        if not shares:
            missing += 1
            continue
        for part, tokens in shares.items():
            by_part[part] = by_part.get(part, 0) + tokens
    return {
        "method": ATTRIBUTION_METHOD,
        "input_tokens_total": sum(row["input_tokens"] for row in usage),
        "output_tokens_total": sum(row["output_tokens"] for row in usage),
        "input_tokens_by_part": dict(sorted(by_part.items())),
        "research_input_tokens": by_part.get("tool:web_research", 0),
        "requests_without_attribution": missing,
    }


def create_app(settings: Settings, provider=None, research=None, sandbox=None, git=None) -> FastAPI:
    tokens = load_tokens(settings)
    store = Store(settings.database)
    store.register_accounts(settings.users)
    engine = Engine(settings, store, tokens, provider, research, sandbox, git)

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        yield
        await engine.close()

    app = FastAPI(
        title="Token-Efficient Coding Agent",
        version="0.3.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(BodyLimit)
    app.state.store, app.state.engine = store, engine
    bearer = HTTPBearer(auto_error=False)

    async def owner(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
        identity = authenticate(credentials.credentials, tokens) if credentials else None
        if identity is None:
            raise HTTPException(
                401, "Authentication required", headers={"WWW-Authenticate": "Bearer"}
            )
        return identity

    def owned(session_id: str, identity: str) -> dict:
        try:
            session = store.session(session_id)
        except KeyError:
            raise HTTPException(404, "Session not found") from None
        if session["owner"] != identity:
            raise HTTPException(404, "Session not found")
        return session

    @app.exception_handler(PolicyError)
    async def policy_failure(request: Request, exc: PolicyError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def validation_failure(request: Request, exc: RequestValidationError):
        return JSONResponse(
            {"detail": [{"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()]},
            status_code=422,
        )

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.3.0", "mode": settings.mode}

    @app.post("/sessions", status_code=201)
    async def create_session(body: SessionCreate, identity: str = Depends(owner)):
        user = next(u for u in settings.users if u.id == identity)
        repo, sources = resolve_repository(body.repo_path, user, settings)
        for source in sources:
            store.mark_confidential(source)
        session = store.create_session(
            identity, repo, bool(sources), user.read_only or body.mode == "read_only"
        )
        store.event(
            session["id"],
            None,
            "session_created",
            {"mode": settings.mode, "confidential": bool(session["confidential"])},
        )
        return {
            "session_id": session["id"],
            "mode": settings.mode,
            "confidential": bool(session["confidential"]),
            "read_only": bool(session["read_only"]),
        }

    @app.post("/sessions/{session_id}/messages", status_code=202)
    async def message(session_id: str, body: TaskRequest, identity: str = Depends(owner)):
        owned(session_id, identity)
        return {"run_id": engine.start(session_id, body.text, body.allow_research)}

    @app.get("/sessions/{session_id}/status")
    async def status(session_id: str, identity: str = Depends(owner)):
        session = owned(session_id, identity)
        run = store.latest_run(session_id)
        user = next(u for u in settings.users if u.id == identity)
        remaining = store.remaining(identity)
        with store.connection() as db:
            rows = db.execute(
                "SELECT id FROM approvals WHERE session_id=? AND status='pending'", (session_id,)
            ).fetchall()
        return {
            "session_id": session_id,
            "mode": settings.mode,
            "confidential": bool(session["confidential"]),
            "run": run,
            "budget_remaining_micro_usd": remaining,
            "budget_limit_micro_usd": user.budget_micro_usd,
            "budget_warning": budget_warning_active(user.budget_micro_usd, remaining),
            "pending_approvals": [store.approval(row["id"]) for row in rows],
            "usage": store.usage(run["id"]) if run else [],
            "research_usage": store.research_usage(run["id"]) if run else [],
            "read_only": bool(session["read_only"] or user.read_only),
        }

    @app.get("/sessions/{session_id}/approvals/{approval_id}/preview")
    async def preview(session_id: str, approval_id: str, identity: str = Depends(owner)):
        session = owned(session_id, identity)
        try:
            approval = store.approval(approval_id)
        except KeyError:
            raise HTTPException(404, "Approval not found") from None
        if approval["session_id"] != session_id:
            raise HTTPException(404, "Approval not found")
        if approval["status"] != "pending" or approval["deadline"] <= time.time():
            raise PolicyError("Preview requires a current pending approval")
        repo = engine.repository(session)
        data = approval["action"]
        if data["tool"] == "apply_patch":
            action = ApplyPatch.model_validate(data)
            after, _ = repo.prepare_patch(action)
            before = repo.read(action.path)
        elif data["tool"] == "write_file":
            action = WriteFile.model_validate(data)
            after, _ = repo.prepare_write(action)
            before = "" if action.expected_sha256 == "absent" else repo.read(action.path)
        else:
            raise PolicyError("Native diff preview is only available for file changes")
        if action.expected_sha256 != "absent" and repo.digest(before) != action.expected_sha256:
            raise PolicyError("File changed while preparing preview; request a new approval")
        return {
            "approval_id": approval_id,
            "path": action.path,
            "before": before,
            "after": after,
            "expected_sha256": action.expected_sha256,
        }

    @app.post("/sessions/{session_id}/approvals/{approval_id}")
    async def approve(
        session_id: str, approval_id: str, body: ApprovalDecision, identity: str = Depends(owner)
    ):
        session = owned(session_id, identity)
        if session["read_only"] or next(u for u in settings.users if u.id == identity).read_only:
            raise HTTPException(403, "Read-only identities/sessions cannot approve gated actions")
        try:
            approval = store.approval(approval_id)
        except KeyError:
            raise HTTPException(404, "Approval not found") from None
        if approval["session_id"] != session_id:
            raise HTTPException(404, "Approval not found")
        engine.repository(session).check_secrets(body.feedback)
        store.decide(approval_id, body.decision, body.feedback)
        store.event(
            session_id,
            approval["run_id"],
            "approval_decision",
            {
                "approval_id": approval_id,
                "decision": body.decision,
                "user": identity,
                "feedback": body.feedback,
            },
        )
        return {"approval_id": approval_id, "decision": body.decision}

    @app.post("/sessions/{session_id}/cancel")
    async def cancel(session_id: str, identity: str = Depends(owner)):
        owned(session_id, identity)
        await engine.cancel(session_id)
        return {"session_id": session_id, "status": "cancelled"}

    @app.get("/sessions/{session_id}/events")
    async def events(
        session_id: str,
        request: Request,
        after: int = Query(default=0, ge=0),
        identity: str = Depends(owner),
    ):
        owned(session_id, identity)
        cursor = after
        if value := request.headers.get("Last-Event-ID"):
            try:
                cursor = max(after, int(value))
            except ValueError:
                raise HTTPException(422, "Invalid Last-Event-ID") from None
            if cursor < 0:
                raise HTTPException(422, "Invalid Last-Event-ID")

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                batch = store.events(session_id, cursor)
                for event in batch:
                    cursor = event["id"]
                    yield f"id: {cursor}\nevent: {event['type']}\ndata: {json.dumps(event)}\n\n"
                run = store.latest_run(session_id)
                if not batch and run and run["status"] in TERMINAL:
                    return
                if not batch:
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(0.3)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/metrics/runs/{run_id}")
    async def metrics(run_id: str, identity: str = Depends(owner)):
        try:
            run = store.run(run_id)
        except KeyError:
            raise HTTPException(404, "Run not found") from None
        owned(run["session_id"], identity)
        usage = store.usage(run_id)
        states: dict[str, int] = {}
        for row in usage:
            states[row["state"]] = states.get(row["state"], 0) + 1
        reconciled = bool(usage) and set(states) == {"reconciled"}
        return {
            "run": run,
            "usage": usage,
            "research_usage": store.research_usage(run_id),
            "token_attribution": token_attribution(usage),
            "cost_basis": "Reconciled against gateway log cost"
            if reconciled
            else "Conservative configured ceilings; not fully reconciled with gateway logs",
            "reconciliation": {"complete": reconciled, "usage_states": states},
            "benchmark_evidence": False,
        }

    return app
