import secrets
import socket
import time
from contextlib import contextmanager
from threading import Thread

import httpx
import pytest
import uvicorn

from token_agent.api import create_app
from token_agent.config import Settings, UserConfig
from token_agent.providers import DEMO_TASK


@contextmanager
def live_client(app, headers):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    try:
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Test server failed to start")
            time.sleep(0.01)
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", headers=headers, timeout=5, trust_env=False
        ) as client:
            yield client
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        alive = thread.is_alive()
        try:
            listener.close()
        except OSError:
            pass
        if alive:
            raise RuntimeError("Test server failed to shut down")


@pytest.fixture
def environment(tmp_path, monkeypatch):
    root = tmp_path / "repos"
    repo = root / "demo"
    repo.mkdir(parents=True)
    (repo / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
    first, second = secrets.token_urlsafe(40), secrets.token_urlsafe(40)
    monkeypatch.setenv("FIRST_TOKEN", first)
    monkeypatch.setenv("SECOND_TOKEN", second)
    settings = Settings(
        database=str(tmp_path / "state.sqlite3"),
        users=[
            UserConfig(id="first", token_env="FIRST_TOKEN", repo_roots=[str(root)]),
            UserConfig(id="second", token_env="SECOND_TOKEN", repo_roots=[str(root)]),
        ],
    )
    return {
        "settings": settings,
        "repo": repo,
        "root": root,
        "first": {"Authorization": f"Bearer {first}"},
        "second": {"Authorization": f"Bearer {second}"},
    }


@pytest.fixture
def connected(environment):
    app = create_app(environment["settings"])
    with live_client(app, environment["first"]) as client:
        yield client, app, environment


def session(client, env):
    response = client.post("/sessions", json={"repo_path": str(env["repo"])})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def start(client, session_id, text=DEMO_TASK):
    response = client.post(f"/sessions/{session_id}/messages", json={"text": text})
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def wait_status(client, session_id, expected, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        response = client.get(f"/sessions/{session_id}/status")
        assert response.status_code == 200, response.text
        data = response.json()
        if data["run"] and data["run"]["status"] in expected:
            return data
        time.sleep(0.01)
    raise AssertionError(f"Did not reach {expected}; last state: {data}")
