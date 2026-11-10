"""Gateway log reconciliation: release held money only when the gateway log proves the cost.

Gateway rows here are hand-written in Bifrost's log shape. A live run against real Bifrost
logs is recorded separately and is not part of the automated suite.
"""

import asyncio
import json
import sqlite3

import httpx
import pytest

from token_agent.api import create_app
from token_agent.providers import GATEWAY_BUDGET_HIT, BifrostProvider, ProviderError
from token_agent.reconcile import (
    REQUIRED_COLUMNS,
    load_gateway_log,
    main,
    micro_usd,
    reconcile,
)
from token_agent.store import Store

from .conftest import live_client, session, start, wait_status
from .test_providers import CountingProvider, live_settings


@pytest.fixture
def store(environment):
    store = Store(environment["settings"].database)
    store.register_accounts(environment["settings"].users)
    sid = store.create_session("first", str(environment["repo"]), False)["id"]
    store.run_id = store.start_run(sid)
    return store


def held(store, amount=50000, tokens=(100, 20), request_id=None, retain=True):
    reservation = store.reserve("first", store.run_id, amount, "cloud/primary", "test")
    store.settle(
        reservation,
        min(1000, amount),
        tokens[0],
        tokens[1],
        "cloud/primary",
        details={"request_id": request_id or reservation},
        retain_reservation=retain,
    )
    return reservation


def log(row_id, status="success", tokens=(100, 20), cost=0.0004, parent=None, error_type=""):
    return {
        "id": row_id,
        "parent_request_id": parent,
        "provider": "gemini",
        "model": "gemini-3.6-flash",
        "status": status,
        "prompt_tokens": tokens[0] if status == "success" else 0,
        "completion_tokens": tokens[1] if status == "success" else 0,
        "cost": cost if status == "success" else None,
        "error_details": json.dumps({"type": error_type}) if error_type else "",
    }


def row(store, usage_id):
    return next(u for u in store.usage(store.run_id) if u["id"] == usage_id)


def test_held_amount_is_replaced_by_the_proven_gateway_cost(store):
    rid = held(store, amount=50000)
    before = store.remaining("first")
    report = reconcile(store, [log(rid, cost=0.000401)])
    after = row(store, rid)
    assert after["state"] == "reconciled"
    assert after["amount"] == 401  # Rounded up to whole micro-USD.
    assert store.remaining("first") == before + 50000 - 401
    assert after["details"]["reconciliation"]["gateway_cost_micro_usd"] == 401
    assert report.summary()["released_micro_usd"] == 50000 - 401


def test_fallback_attempts_are_matched_through_the_parent_request(store):
    rid = held(store, amount=80000, tokens=(130, 133))
    rows = [
        log(rid, status="error", error_type="UNAVAILABLE"),
        log("child-1", tokens=(130, 133), cost=0.0000993, parent=rid),
    ]
    reconcile(store, rows)
    after = row(store, rid)
    assert (after["state"], after["amount"]) == ("reconciled", 100)
    assert len(after["details"]["reconciliation"]["attempts"]) == 2


def test_token_mismatch_keeps_the_money_held(store):
    rid = held(store, amount=50000, tokens=(100, 20))
    report = reconcile(store, [log(rid, tokens=(100, 25))])
    after = row(store, rid)
    assert (after["state"], after["amount"]) == ("discrepancy", 50000)
    assert after["details"]["reconciliation"]["token_gap"] == [0, 5]
    assert report.summary()["released_micro_usd"] == 0


def test_tolerance_accepts_small_token_gaps(store):
    rid = held(store, tokens=(100, 20))
    reconcile(store, [log(rid, tokens=(101, 22))], tolerance=2)
    assert row(store, rid)["state"] == "reconciled"


def test_missing_gateway_record_changes_nothing(store):
    rid = held(store, amount=50000)
    report = reconcile(store, [log("someone-else")])
    assert (row(store, rid)["state"], row(store, rid)["amount"]) == (
        "held_for_reconciliation",
        50000,
    )
    assert report.outcomes[0].result == "no_gateway_record"


def test_uncertain_request_adopts_the_gateway_record_when_it_finished(store):
    rid = store.reserve("first", store.run_id, 60000, "cloud/primary", "test")
    store.uncertain(rid)
    reconcile(store, [log(rid, tokens=(900, 50), cost=0.002)])
    after = row(store, rid)
    assert (after["state"], after["amount"]) == ("reconciled", 2000)
    assert after["details"]["reconciliation"]["gateway_tokens"] == [900, 50]


def test_uncertain_request_without_a_finished_attempt_stays_held(store):
    rid = store.reserve("first", store.run_id, 60000, "cloud/primary", "test")
    store.uncertain(rid)
    report = reconcile(store, [log(rid, status="cancelled")])
    assert (row(store, rid)["state"], row(store, rid)["amount"]) == ("uncertain", 60000)
    assert report.outcomes[0].result == "unresolved"


@pytest.mark.parametrize(
    "error_type", ["budget_exceeded", "model_blocked", "provider_blocked", "virtual_key_required"]
)
def test_gateway_refusal_before_any_provider_releases_everything(store, error_type):
    rid = store.reserve("first", store.run_id, 60000, "cloud/primary", "test")
    store.uncertain(rid)
    reconcile(store, [log(rid, status="error", error_type=error_type)])
    assert (row(store, rid)["state"], row(store, rid)["amount"]) == ("reconciled", 0)


def test_provider_error_is_not_assumed_free(store):
    rid = store.reserve("first", store.run_id, 60000, "cloud/primary", "test")
    store.uncertain(rid)
    reconcile(store, [log(rid, status="error", error_type="UNAVAILABLE")])
    assert row(store, rid)["state"] == "uncertain"


def test_gateway_cost_above_the_ceiling_is_charged_and_flagged(store):
    rid = held(store, amount=300)
    report = reconcile(store, [log(rid, cost=0.0009)])
    after = row(store, rid)
    assert (after["state"], after["amount"]) == ("reconciled", 900)
    assert "above the configured price ceiling" in after["details"]["reconciliation"]["note"]
    assert report.summary()["added_micro_usd"] == 600


@pytest.mark.parametrize(
    ("rows", "note"),
    [
        (lambda rid: [log(rid, cost=None)], "no cost"),
        (lambda rid: [log(rid), log("c", parent=rid)], "more than one"),
    ],
)
def test_unprovable_success_is_a_discrepancy(store, rows, note):
    rid = held(store, amount=50000)
    reconcile(store, rows(rid))
    after = row(store, rid)
    assert (after["state"], after["amount"]) == ("discrepancy", 50000)
    assert note in after["details"]["reconciliation"]["note"].lower()


def test_reconciliation_is_idempotent(store):
    rid = held(store)
    reconcile(store, [log(rid)])
    second = reconcile(store, [log(rid, cost=0.5)])
    assert second.outcomes == []
    assert row(store, rid)["amount"] == 400


def test_fixture_rows_are_never_reconciled(store):
    reservation = store.reserve("first", store.run_id, 0, "fixture/local", "fixture")
    store.settle(reservation, 0, 10, 5, "fixture/local", details={"request_id": reservation})
    assert reconcile(store, [log(reservation)]).outcomes == []


def test_row_changed_during_reconciliation_is_skipped(store):
    rid = held(store)
    assert not store.apply_reconciliation(rid, "uncertain", 0, "reconciled", {})
    assert row(store, rid)["state"] == "held_for_reconciliation"


def test_micro_usd_rounds_up():
    assert micro_usd(0.0000993) == 100
    assert micro_usd(0.000401) == 401
    assert micro_usd(0.0) == 0


# --- Loading gateway logs ---------------------------------------------------------------


def test_bifrost_sqlite_log_store_is_read_without_writing(tmp_path, store):
    rid = held(store)
    path = tmp_path / "logs.db"
    db = sqlite3.connect(path)
    db.execute(f"CREATE TABLE logs ({', '.join(sorted(REQUIRED_COLUMNS))}, extra TEXT)")
    sample = log(rid)
    db.execute(
        f"INSERT INTO logs ({', '.join(sorted(REQUIRED_COLUMNS))}) VALUES "
        f"({', '.join('?' for _ in REQUIRED_COLUMNS)})",
        [sample[c] for c in sorted(REQUIRED_COLUMNS)],
    )
    db.commit()
    db.close()
    rows = load_gateway_log(path)
    assert rows == [sample]
    reconcile(store, rows)
    assert row(store, rid)["state"] == "reconciled"


def test_unexpected_log_schema_fails_closed(tmp_path):
    path = tmp_path / "logs.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE logs (id TEXT, status TEXT)")
    db.close()
    with pytest.raises(ValueError, match="columns"):
        load_gateway_log(path)
    bad = tmp_path / "rows.json"
    bad.write_text(json.dumps([{"id": "x"}]))
    with pytest.raises(ValueError, match="required fields"):
        load_gateway_log(bad)
    bad.write_text(json.dumps({"id": "x"}))
    with pytest.raises(ValueError, match="array"):
        load_gateway_log(bad)


def test_command_line_reconciles_and_writes_a_report(tmp_path, environment):
    root = environment["root"]
    database = tmp_path / "state.sqlite3"
    config = tmp_path / "agent.toml"
    config.write_text(
        f'database = "{database.as_posix()}"\n\n[[users]]\nid = "first"\n'
        f'token_env = "FIRST_TOKEN"\nrepo_roots = ["{root.as_posix()}"]\n'
    )
    store = Store(str(database))
    store.register_accounts(environment["settings"].users[:1])
    sid = store.create_session("first", str(environment["repo"]), False)["id"]
    store.run_id = store.start_run(sid)
    rid = held(store, amount=50000)
    gateway = tmp_path / "gateway.json"
    gateway.write_text(json.dumps([log(rid, cost=0.0001)]))
    report = tmp_path / "report.json"
    assert (
        main(["--config", str(config), "--gateway-log", str(gateway), "--report", str(report)]) == 0
    )
    saved = json.loads(report.read_text())
    assert saved["summary"]["results"] == {"reconciled": 1}
    assert saved["summary"]["released_micro_usd"] == 50000 - 100


# --- Run metrics and the gateway's own budget -------------------------------------------


def test_metrics_report_whether_a_run_is_fully_reconciled(environment):
    settings = live_settings(
        environment, gateway_attempt_ceiling=2, standard_fallbacks=["cloud/fallback"]
    )
    app = create_app(settings, CountingProvider())
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        run = start(client, sid)
        wait_status(client, sid, {"completed"})
        before = client.get(f"/metrics/runs/{run}").json()
        assert before["reconciliation"] == {
            "complete": False,
            "usage_states": {"held_for_reconciliation": 1},
        }
        usage = before["usage"][0]
        rows = [log(usage["id"], tokens=(usage["input_tokens"], usage["output_tokens"]))]
        reconcile(app.state.store, rows)
        after = client.get(f"/metrics/runs/{run}").json()
    assert after["reconciliation"]["complete"] is True
    assert after["cost_basis"] == "Reconciled against gateway log cost"
    assert after["benchmark_evidence"] is False


def bifrost_402(environment, monkeypatch, body):
    monkeypatch.setenv("BIFROST_VIRTUAL_KEY", "virtual-key-for-tests")

    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(402, json=body))
        async with httpx.AsyncClient(transport=transport) as client:
            provider = BifrostProvider(live_settings(environment), client)
            await provider.complete([], "cloud/primary", environment["settings"].users[0], False)

    with pytest.raises(ProviderError) as error:
        asyncio.run(run())
    return str(error.value)


def test_gateway_budget_refusal_is_reported_as_a_budget_hit(environment, monkeypatch):
    body = {"type": "budget_exceeded", "error": {"message": "Budget exceeded: 0.0002 >= 0.0002"}}
    assert bifrost_402(environment, monkeypatch, body) == GATEWAY_BUDGET_HIT
    other = bifrost_402(environment, monkeypatch, {"type": "payment_required"})
    assert other == "Gateway returned HTTP 402; session paused"


def test_engine_emits_budget_hit_with_gateway_options(environment):
    class BudgetRefused:
        async def complete(self, messages, model, user, confidential, request_context=None):
            raise ProviderError(GATEWAY_BUDGET_HIT)

    app = create_app(live_settings(environment), BudgetRefused())
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        data = wait_status(client, sid, {"paused"})
        hits = [e["payload"] for e in app.state.store.events(sid) if e["type"] == "budget_hit"]
    assert data["run"]["outcome"] == GATEWAY_BUDGET_HIT
    assert hits[0]["reason"] == GATEWAY_BUDGET_HIT
    assert hits[0]["options"] == ["raise_gateway_budget_then_resubmit"]
