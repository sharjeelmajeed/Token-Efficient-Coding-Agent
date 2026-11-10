import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.syntax import Syntax
from rich.table import Table

from .api import create_app
from .config import load_settings
from .diagnostics import diagnose

app = typer.Typer(
    no_args_is_help=True, help="Token-efficient coding agent. All policy lives in core."
)
console = Console(highlight=False)
# The core sends these after a reply's streamed pieces but before its final piece
# (right after the model call is settled). They do not end the reply.
MID_REPLY_EVENTS = {"model_switched", "budget_warning"}


def clean(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", text)


def state_file() -> Path:
    return Path(os.environ.get("AGENT_CLIENT_STATE", ".agent/client.json"))


def read_state() -> dict:
    try:
        return json.loads(state_file().read_text())
    except (OSError, json.JSONDecodeError):
        raise typer.BadParameter("Run agent init --repo PATH first") from None


def save_state(state: dict):
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def client() -> httpx.Client:
    token = os.environ.get("AGENT_API_TOKEN", "")
    if not token:
        raise typer.BadParameter("Set AGENT_API_TOKEN in your environment")
    url = os.environ.get("AGENT_CORE_URL", "http://127.0.0.1:8765")
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise typer.BadParameter("Core URL may not include credentials or query strings")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        raise typer.BadParameter("Core URL must use HTTPS or loopback HTTP")
    return httpx.Client(
        base_url=url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(10, read=30),
        trust_env=False,
        follow_redirects=False,
    )


def request(http: httpx.Client, method: str, path: str, **kwargs):
    try:
        response = http.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        try:
            reason = clean(str(exc.response.json().get("detail", "Request rejected")))
        except ValueError:
            reason = "Request rejected"
        raise typer.BadParameter(f"Core HTTP {exc.response.status_code}: {reason}") from None
    except httpx.HTTPError:
        raise typer.BadParameter(
            "Core is unavailable. Check agent serve and the configured URL"
        ) from None


def usd(micro: int) -> str:
    return f"${micro / 1000000:.4f}"


def budget_line(payload: dict) -> str:
    """Readable budget event text built only from the core's event payload."""
    parts = [
        f"{usd(payload.get('remaining_micro_usd', 0))} remaining"
        f" of {usd(payload.get('limit_micro_usd', 0))}"
    ]
    if payload.get("options"):
        parts.append("options: " + ", ".join(str(o) for o in payload["options"]))
    return clean("; ".join(parts))


def show_status(data: dict):
    run = data.get("run") or {}
    usage = data.get("usage", [])
    table = Table(title="Agent status", show_header=False)
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    for key, value in [
        ("Mode", data["mode"]),
        ("Repository", "CONFIDENTIAL / LOCAL ONLY" if data["confidential"] else "standard"),
        ("Run", run.get("status", "idle")),
        ("Model", usage[-1]["model"] if usage else "not called"),
        ("Steps", str(run.get("step", 0))),
        ("Budget remaining", f"${data['budget_remaining_micro_usd'] / 1000000:.4f}"),
        (
            "Input / output tokens",
            f"{sum(u['input_tokens'] for u in usage)} / {sum(u['output_tokens'] for u in usage)}",
        ),
    ]:
        table.add_row(key, clean(value))
    console.print(table)
    if data["mode"] == "fixture":
        console.print(
            "OFFLINE FIXTURE: token counts are synthetic, not benchmark evidence.", style="yellow"
        )
    # The core works out the warning for the user's budget on every request; the
    # client never stores it. An older core without the field shows no warning.
    limit = data.get("budget_limit_micro_usd")
    if data.get("budget_warning") is True and isinstance(limit, int) and limit > 0:
        remaining = data["budget_remaining_micro_usd"]
        used = (limit - remaining) * 100 // limit
        console.print(
            f"BUDGET WARNING: {used}% of budget used ({usd(remaining)} remaining of {usd(limit)})",
            style="bold red",
        )


def decide(http: httpx.Client, session_id: str, approval: dict):
    if approval["action"]["tool"] == "run_shell":
        console.print("Exact isolated command requires your approval", style="bold yellow")
        console.print(clean(json.dumps(approval["action"], indent=2)), markup=False)
    else:
        console.print("Exact file change requires your approval", style="bold yellow")
        console.print(Syntax(clean(approval["diff"]), "diff", theme="ansi_dark", word_wrap=True))
    choice = Prompt.ask(
        "Approve once / deny / deny with feedback", choices=["a", "d", "f"], default="d"
    )
    feedback = Prompt.ask("Feedback") if choice == "f" else ""
    request(
        http,
        "POST",
        f"/sessions/{session_id}/approvals/{approval['id']}",
        json={
            "decision": {"a": "approve_once", "d": "deny", "f": "deny_with_feedback"}[choice],
            "feedback": feedback,
        },
    )


def watch(http: httpx.Client, state: dict):
    session_id = state["session_id"]
    status = request(http, "GET", f"/sessions/{session_id}/status")
    show_status(status)
    for approval in status["pending_approvals"]:
        decide(http, session_id, approval)
    # True while a streamed reply has printed partial pieces but not its final piece.
    reply_open = False
    # Mid-reply notices, shown once the reply's line is finished.
    held: list[tuple[str, dict]] = []

    def end_reply(note: str = ""):
        nonlocal reply_open
        if reply_open:
            console.print()
            if note:
                console.print(note, markup=False, style="yellow")
            reply_open = False
        while held:
            show_event(*held.pop(0))

    def show_event(kind: str, payload: dict):
        if kind == "approval_request":
            current = request(http, "GET", f"/sessions/{session_id}/status")
            for approval in current["pending_approvals"]:
                if approval["id"] == payload["approval_id"]:
                    decide(http, session_id, approval)
        elif kind == "message_delta":
            console.print(clean(payload["text"]), markup=False)
        elif kind == "tool_call":
            console.print(f"Tool: {payload['action']['tool']} ({payload['class']})", markup=False)
        elif kind == "budget_warning":
            console.print(f"Budget warning: {budget_line(payload)}", markup=False, style="bold red")
            show_status(request(http, "GET", f"/sessions/{session_id}/status"))
        elif kind == "budget_hit":
            reason = clean(str(payload.get("reason", "budget limit reached")))
            console.print(
                f"Budget limit reached: {reason}; {budget_line(payload)}",
                markup=False,
                style="bold red",
            )
            show_status(request(http, "GET", f"/sessions/{session_id}/status"))
        elif kind in {"model_switched", "session_paused", "run_complete"}:
            console.print(clean(f"{kind}: {json.dumps(payload)}"), markup=False, style="yellow")
            show_status(request(http, "GET", f"/sessions/{session_id}/status"))
        elif kind in {"policy_block", "approval_expired", "research_used"}:
            console.print(clean(f"{kind}: {json.dumps(payload)}"), markup=False, style="yellow")

    try:
        with http.stream(
            "GET", f"/sessions/{session_id}/events", params={"after": state.get("last_event_id", 0)}
        ) as stream:
            stream.raise_for_status()
            for line in stream.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                state["last_event_id"] = event["id"]
                save_state(state)
                kind, payload = event["type"], event["payload"]
                if kind == "message_delta" and payload.get("partial") is True:
                    # Streamed piece: keep building the reply on the same line.
                    console.print(clean(payload["text"]), markup=False, end="", soft_wrap=True)
                    reply_open = True
                    continue
                if kind == "message_delta" and reply_open:
                    # Final piece (partial: false) closes the streamed reply.
                    console.print(clean(payload["text"]), markup=False, soft_wrap=True)
                    reply_open = False
                    end_reply()  # now show any notices held back during the reply
                    continue
                if reply_open and kind in MID_REPLY_EVENTS:
                    # Sent before the final piece; hold it so the reply text stays together.
                    held.append((kind, payload))
                    continue
                # Only the final piece completes a reply. Any other event first (the core
                # sends policy_block, then session_paused) means the text is incomplete.
                end_reply("(reply incomplete)")
                show_event(kind, payload)
        end_reply()
        show_status(request(http, "GET", f"/sessions/{session_id}/status"))
    except KeyboardInterrupt:
        end_reply()
        request(http, "POST", f"/sessions/{session_id}/cancel")
        console.print("Run cancelled.")
    except httpx.HTTPError:
        held.clear()  # their status lookups would fail too; agent status shows the warning
        end_reply()
        raise typer.BadParameter("Stream disconnected. Use agent resume to reconnect") from None


@app.command()
def serve(
    config: Path = typer.Option(Path("agent.toml")),
    port: int = typer.Option(8765, min=1024, max=65535),
):
    """Run the authenticated single-process core on loopback."""
    import uvicorn

    settings = load_settings(config)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=port, access_log=False)


@app.command()
def init(repo: Path = typer.Option(..., exists=True, file_okay=False)):
    """Create a session for an allowed repository."""
    with client() as http:
        data = request(http, "POST", "/sessions", json={"repo_path": str(repo.resolve())})
    save_state({**data, "last_event_id": 0})
    console.print(f"Session created: {data['session_id']}", markup=False)
    console.print("Mode: " + data["mode"], markup=False)


@app.command()
def run(task: str = typer.Argument(...), research: bool = typer.Option(False, "--research")):
    """Submit a task and interact with the core's approval requests."""
    state = read_state()
    with client() as http:
        result = request(
            http,
            "POST",
            f"/sessions/{state['session_id']}/messages",
            json={"text": task, "allow_research": research},
        )
        state["run_id"] = result["run_id"]
        save_state(state)
        watch(http, state)


@app.command()
def doctor(config: Path = typer.Option(Path("agent.toml"))):
    """Check configuration and local prerequisites without model or research requests."""
    try:
        data = diagnose(load_settings(config))
    except (ValueError, OSError):
        console.print("Configuration could not be loaded; check the trusted TOML file.")
        raise typer.Exit(1) from None
    typer.echo(json.dumps(data, indent=2))
    if not data["ready"]:
        raise typer.Exit(1)


@app.command()
def status():
    """Show persisted run, budget and usage state."""
    state = read_state()
    with client() as http:
        show_status(request(http, "GET", f"/sessions/{state['session_id']}/status"))


@app.command()
def resume():
    """Reconnect to the last session using its durable event cursor."""
    state = read_state()
    with client() as http:
        watch(http, state)


@app.command()
def cancel():
    """Cancel the active run and invalidate its pending approvals."""
    state = read_state()
    with client() as http:
        request(http, "POST", f"/sessions/{state['session_id']}/cancel")
    console.print("Cancellation recorded.")


if __name__ == "__main__":
    app()
