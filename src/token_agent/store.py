import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import Event
from .repo import PolicyError


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if Path(path).is_symlink():
            raise ValueError("Database cannot be a symbolic link")
        descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(descriptor)
        Path(path).chmod(0o600)
        with self.connection() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS accounts (
              owner TEXT PRIMARY KEY, budget INTEGER NOT NULL CHECK(budget>=0));
            CREATE TABLE IF NOT EXISTS sessions (
              id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES accounts(owner),
              repo TEXT NOT NULL, confidential INTEGER NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
              status TEXT NOT NULL, step INTEGER NOT NULL DEFAULT 0,
              created TEXT NOT NULL, outcome TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(session_id)
              WHERE status IN ('running','waiting_approval');
            CREATE TABLE IF NOT EXISTS messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL REFERENCES sessions(id), role TEXT NOT NULL,
              content TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL REFERENCES sessions(id), run_id TEXT,
              type TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
              BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
              BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
            CREATE TABLE IF NOT EXISTS approvals (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
              session_id TEXT NOT NULL REFERENCES sessions(id), action TEXT NOT NULL,
              diff TEXT NOT NULL, status TEXT NOT NULL, deadline REAL NOT NULL,
              feedback TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS usage (
              id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES accounts(owner),
              run_id TEXT NOT NULL REFERENCES runs(id), amount INTEGER NOT NULL CHECK(amount>=0),
              state TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0,
              output_tokens INTEGER NOT NULL DEFAULT 0, model TEXT NOT NULL,
              source TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_session ON events(session_id,id);
            CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id,id);
            CREATE TABLE IF NOT EXISTS research_cache (
              session_id TEXT NOT NULL REFERENCES sessions(id), key TEXT NOT NULL,
              result TEXT NOT NULL, expires REAL NOT NULL, PRIMARY KEY(session_id,key));
            CREATE TABLE IF NOT EXISTS research_calls (
              id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
              run_id TEXT NOT NULL REFERENCES runs(id), query TEXT NOT NULL,
              status TEXT NOT NULL, credits INTEGER);
            """)
            for table, column, definition in [
                ("runs", "options", "TEXT NOT NULL DEFAULT '{}'"),
                ("usage", "details", "TEXT NOT NULL DEFAULT '{}'"),
                ("messages", "kind", "TEXT NOT NULL DEFAULT 'instruction'"),
                ("sessions", "read_only", "INTEGER NOT NULL DEFAULT 0"),
            ]:
                columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                if column not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @contextmanager
    def connection(self, transaction: bool = False):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if transaction:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if transaction:
                db.commit()
        except Exception:
            if transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def register_accounts(self, users):
        with self.connection(True) as db:
            db.executemany(
                "INSERT INTO accounts VALUES (?,?) ON CONFLICT(owner) "
                "DO UPDATE SET budget=excluded.budget",
                [(u.id, u.budget_micro_usd) for u in users],
            )

    def create_session(
        self, owner: str, repo: str, confidential: bool, read_only: bool = False
    ) -> dict:
        session_id = str(uuid4())
        with self.connection(True) as db:
            # Once observed confidential, a repository cannot be silently downgraded.
            prior = db.execute(
                "SELECT 1 FROM sessions WHERE repo=? AND confidential=1", (repo,)
            ).fetchone()
            db.execute(
                "INSERT INTO sessions(id,owner,repo,confidential,created,read_only) "
                "VALUES (?,?,?,?,?,?)",
                (
                    session_id,
                    owner,
                    repo,
                    int(confidential or bool(prior)),
                    timestamp(),
                    int(read_only),
                ),
            )
            if confidential or prior:
                db.execute("UPDATE sessions SET confidential=1 WHERE repo=?", (repo,))
        return self.session(session_id)

    def session(self, session_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise KeyError(session_id)
        return dict(row)

    def mark_confidential(self, repo: str):
        with self.connection(True) as db:
            db.execute("UPDATE sessions SET confidential=1 WHERE repo=?", (repo,))

    def restrict_session(self, session_id: str):
        with self.connection(True) as db:
            db.execute("UPDATE sessions SET read_only=1 WHERE id=?", (session_id,))

    def start_run(self, session_id: str, options: dict | None = None) -> str:
        run_id = str(uuid4())
        try:
            with self.connection(True) as db:
                db.execute(
                    "INSERT INTO runs(id,session_id,status,created,options) VALUES (?,?,?,?,?)",
                    (run_id, session_id, "running", timestamp(), json.dumps(options or {})),
                )
        except sqlite3.IntegrityError as exc:
            raise PolicyError("Session already has an active run") from exc
        return run_id

    def run(self, run_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        return dict(row)

    def latest_run(self, session_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM runs WHERE session_id=? ORDER BY rowid DESC LIMIT 1", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def update_run(self, run_id: str, status: str, step: int, outcome: str | None = None):
        with self.connection(True) as db:
            db.execute(
                "UPDATE runs SET status=?,step=?,outcome=? WHERE id=?",
                (status, step, outcome, run_id),
            )

    def add_message(self, session_id: str, role: str, content: str, kind: str = "instruction"):
        with self.connection(True) as db:
            db.execute(
                "INSERT INTO messages(session_id,role,content,kind) VALUES (?,?,?,?)",
                (session_id, role, content, kind),
            )

    def messages(self, session_id: str) -> list[dict]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT role,content,kind FROM messages WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def event(self, session_id: str, run_id: str | None, kind: str, payload: dict) -> dict:
        at = timestamp()
        with self.connection(True) as db:
            event_id = db.execute(
                "INSERT INTO events(session_id,run_id,type,timestamp,payload) VALUES (?,?,?,?,?)",
                (session_id, run_id, kind, at, json.dumps(payload)),
            ).lastrowid
        return Event(
            id=event_id,
            session_id=session_id,
            run_id=run_id,
            type=kind,
            timestamp=at,
            payload=payload,
        ).model_dump()

    def events(self, session_id: str, after: int = 0) -> list[dict]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE session_id=? AND id>? ORDER BY id LIMIT 200",
                (session_id, after),
            ).fetchall()
        return [
            Event(**{**dict(row), "payload": json.loads(row["payload"])}).model_dump()
            for row in rows
        ]

    def make_approval(
        self, session_id: str, run_id: str, action: dict, diff: str, timeout: int
    ) -> dict:
        approval_id = str(uuid4())
        with self.connection(True) as db:
            db.execute(
                "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?)",
                (
                    approval_id,
                    run_id,
                    session_id,
                    json.dumps(action),
                    diff,
                    "pending",
                    time.time() + timeout,
                    "",
                ),
            )
        return self.approval(approval_id)

    def approval(self, approval_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if not row:
            raise KeyError(approval_id)
        return {**dict(row), "action": json.loads(row["action"])}

    def decide(self, approval_id: str, decision: str, feedback: str):
        status = "approved" if decision == "approve_once" else "denied"
        with self.connection(True) as db:
            changed = db.execute(
                "UPDATE approvals SET status=?,feedback=? WHERE id=? AND status='pending' "
                "AND deadline>? AND run_id IN "
                "(SELECT id FROM runs WHERE status='waiting_approval')",
                (status, feedback, approval_id, time.time()),
            ).rowcount
        if not changed:
            raise PolicyError("Approval expired, was decided already, or run is no longer active")

    def claim_approval(self, approval_id: str) -> bool:
        with self.connection(True) as db:
            changed = db.execute(
                "UPDATE approvals SET status='consumed' WHERE id=? AND status='approved' "
                "AND deadline>? AND run_id IN "
                "(SELECT id FROM runs WHERE status='waiting_approval')",
                (approval_id, time.time()),
            ).rowcount
        return changed == 1

    def expire_approval(self, approval_id: str):
        with self.connection(True) as db:
            db.execute(
                "UPDATE approvals SET status='expired' WHERE id=? "
                "AND status IN ('pending','approved')",
                (approval_id,),
            )

    def cancel(self, session_id: str):
        with self.connection(True) as db:
            db.execute(
                "UPDATE approvals SET status='cancelled' WHERE session_id=? "
                "AND status IN ('pending','approved')",
                (session_id,),
            )
            db.execute(
                "UPDATE runs SET status='cancelled',outcome='user_cancelled' "
                "WHERE session_id=? AND status IN ('running','waiting_approval')",
                (session_id,),
            )

    def recover(self):
        with self.connection(True) as db:
            rows = db.execute(
                "SELECT id,session_id FROM runs WHERE status IN ('running','waiting_approval')"
            ).fetchall()
            db.execute(
                "UPDATE runs SET status='paused',outcome='server_restart' "
                "WHERE status IN ('running','waiting_approval')"
            )
            db.execute(
                "UPDATE approvals SET status='expired' WHERE status IN ('pending','approved')"
            )
            db.execute("UPDATE usage SET state='uncertain' WHERE state='reserved'")
        for row in rows:
            self.event(
                row["session_id"],
                row["id"],
                "session_paused",
                {
                    "reason": "server_restart",
                    "resume": "Submit a new instruction; inspect changes first",
                },
            )

    def remaining(self, owner: str) -> int:
        with self.connection() as db:
            limit = db.execute("SELECT budget FROM accounts WHERE owner=?", (owner,)).fetchone()[0]
            used = db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM usage WHERE owner=?", (owner,)
            ).fetchone()[0]
        return max(0, limit - used)

    def reserve(self, owner: str, run_id: str, amount: int, model: str, source: str) -> str:
        reservation = str(uuid4())
        with self.connection(True) as db:
            limit = db.execute("SELECT budget FROM accounts WHERE owner=?", (owner,)).fetchone()[0]
            used = db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM usage WHERE owner=?", (owner,)
            ).fetchone()[0]
            if amount < 0 or used + amount > limit:
                raise PolicyError("budget_hit")
            db.execute(
                "INSERT INTO usage(id,owner,run_id,amount,state,model,source) "
                "VALUES (?,?,?,?,?,?,?)",
                (reservation, owner, run_id, amount, "reserved", model, source),
            )
        return reservation

    def settle(
        self,
        reservation: str,
        amount: int,
        incoming: int,
        outgoing: int,
        model: str,
        details: dict | None = None,
        retain_reservation: bool = False,
    ):
        with self.connection(True) as db:
            held = db.execute("SELECT amount FROM usage WHERE id=?", (reservation,)).fetchone()[0]
            if amount > held:
                raise PolicyError("Actual usage exceeded reserved ceiling; pricing needs review")
            db.execute(
                "UPDATE usage SET amount=?,state=?,input_tokens=?,"
                "output_tokens=?,model=?,details=? WHERE id=? AND state='reserved'",
                (
                    held if retain_reservation else amount,
                    "held_for_reconciliation" if retain_reservation else "accounted",
                    incoming,
                    outgoing,
                    model,
                    json.dumps(details or {}),
                    reservation,
                ),
            )

    def uncertain(self, reservation: str):
        with self.connection(True) as db:
            db.execute(
                "UPDATE usage SET state='uncertain' WHERE id=? AND state='reserved'", (reservation,)
            )

    def usage(self, run_id: str) -> list[dict]:
        with self.connection() as db:
            return [
                {**dict(row), "details": json.loads(row["details"])}
                for row in db.execute("SELECT * FROM usage WHERE run_id=?", (run_id,))
            ]

    def cached_research(self, session_id: str, key: str) -> dict | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT result FROM research_cache WHERE session_id=? AND key=? AND expires>?",
                (session_id, key, time.time()),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def cache_research(self, session_id: str, key: str, result: dict, seconds: int):
        with self.connection(True) as db:
            db.execute(
                "INSERT INTO research_cache VALUES (?,?,?,?) ON CONFLICT(session_id,key) "
                "DO UPDATE SET result=excluded.result, expires=excluded.expires",
                (session_id, key, json.dumps(result), time.time() + seconds),
            )

    def reserve_research(self, session_id: str, run_id: str, query: str, limit: int) -> str:
        call_id = str(uuid4())
        with self.connection(True) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM research_calls WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            if count >= limit:
                raise PolicyError("Research call limit reached")
            db.execute(
                "INSERT INTO research_calls VALUES (?,?,?,?,?,NULL)",
                (call_id, session_id, run_id, query, "uncertain"),
            )
        return call_id

    def finish_research(self, call_id: str, credits: int | None):
        with self.connection(True) as db:
            db.execute(
                "UPDATE research_calls SET status='completed', credits=? WHERE id=?",
                (credits, call_id),
            )

    def research_usage(self, run_id: str) -> list[dict]:
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute("SELECT * FROM research_calls WHERE run_id=?", (run_id,))
            ]
