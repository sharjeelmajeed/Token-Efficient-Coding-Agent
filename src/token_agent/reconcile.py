"""Reconcile the core's usage records with the gateway's own request log.

The core reserves conservative price ceilings before each model request. Multi-attempt and
interrupted requests keep that reservation as "held" or "uncertain", because only the gateway
knows what was really billed. This module replaces a held amount with the gateway's recorded
cost only when the gateway log proves it, and never releases money on a guess.

Run:  python -m token_agent.reconcile --config agent.toml --gateway-log PATH [--report FILE]
PATH is Bifrost's logs.db (read-only) or a JSON array of exported log rows.
"""

import argparse
import json
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .config import load_settings
from .store import Store

# Bifrost governance refusals happen before any provider is called, so nothing is billed.
PRE_PROVIDER_REFUSALS = {
    "budget_exceeded",
    "model_blocked",
    "provider_blocked",
    "virtual_key_required",
}
REQUIRED_COLUMNS = {
    "id",
    "parent_request_id",
    "provider",
    "model",
    "status",
    "prompt_tokens",
    "completion_tokens",
    "cost",
    "error_details",
}
OPEN_STATES = ("accounted", "held_for_reconciliation", "uncertain")


@dataclass
class Outcome:
    usage_id: str
    request_id: str
    before_state: str
    before_micro_usd: int
    result: str
    after_micro_usd: int
    note: str = ""


@dataclass
class Report:
    outcomes: list[Outcome] = field(default_factory=list)

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for item in self.outcomes:
            counts[item.result] = counts.get(item.result, 0) + 1
        return {
            "rows_checked": len(self.outcomes),
            "results": counts,
            "held_before_micro_usd": sum(o.before_micro_usd for o in self.outcomes),
            "after_micro_usd": sum(o.after_micro_usd for o in self.outcomes),
            "released_micro_usd": sum(
                max(0, o.before_micro_usd - o.after_micro_usd) for o in self.outcomes
            ),
            "added_micro_usd": sum(
                max(0, o.after_micro_usd - o.before_micro_usd) for o in self.outcomes
            ),
        }


def micro_usd(dollars: float) -> int:
    # Round up: a reconciled amount may never be below what the gateway recorded.
    return math.ceil(round(dollars * 1_000_000, 6))


def load_gateway_log(path: str | Path) -> list[dict]:
    """Read gateway log rows from Bifrost's SQLite log store or a JSON export."""
    path = Path(path)
    if path.suffix.lower() == ".json":
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError("Gateway log JSON must be an array of objects")
    else:
        db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            columns = {row[1] for row in db.execute("PRAGMA table_info(logs)")}
            if not REQUIRED_COLUMNS <= columns:
                raise ValueError("Gateway log store lacks the expected Bifrost log columns")
            rows = [
                dict(row)
                for row in db.execute(f"SELECT {', '.join(sorted(REQUIRED_COLUMNS))} FROM logs")
            ]
        finally:
            db.close()
    missing = [r for r in rows if not REQUIRED_COLUMNS <= set(r)]
    if missing:
        raise ValueError("Gateway log rows lack required fields")
    return rows


def refusal_type(row: dict) -> str:
    try:
        details = json.loads(row.get("error_details") or "{}")
    except (TypeError, ValueError):
        return ""
    return details.get("type", "") if isinstance(details, dict) else ""


def judge(usage: dict, attempts: list[dict], tolerance: int) -> tuple[str, int, str, dict]:
    """Decide one usage row: (result, amount in micro-USD, note, evidence)."""
    held = usage["amount"]
    if not attempts:
        return "no_gateway_record", held, "Gateway log has no matching request", {}
    successes = [a for a in attempts if a["status"] == "success"]
    evidence = {
        "attempts": [
            {
                "provider": a["provider"],
                "model": a["model"],
                "status": a["status"],
                "tokens": [a["prompt_tokens"], a["completion_tokens"]],
                "cost_usd": a["cost"],
            }
            for a in attempts
        ]
    }
    if not successes:
        if all(refusal_type(a) in PRE_PROVIDER_REFUSALS for a in attempts):
            return (
                "reconciled",
                0,
                "Refused by the gateway before any provider was called",
                evidence,
            )
        return (
            "unresolved",
            held,
            "No successful attempt; a provider may still have billed",
            evidence,
        )
    if len(successes) > 1:
        return "discrepancy", held, "More than one successful attempt for one request", evidence
    final = successes[0]
    if any(a["status"] == "success" and a["cost"] is None for a in attempts):
        return "discrepancy", held, "Gateway recorded no cost for a successful attempt", evidence
    cost = micro_usd(sum(a["cost"] or 0 for a in attempts))
    evidence["gateway_cost_micro_usd"] = cost
    if usage["state"] == "uncertain":
        # The core never saw the reply; the gateway's record is the only source of truth.
        evidence["gateway_tokens"] = [final["prompt_tokens"], final["completion_tokens"]]
        return "reconciled", cost, "Core never received the reply; gateway record adopted", evidence
    tokens = (final["prompt_tokens"], final["completion_tokens"])
    gaps = (abs(tokens[0] - usage["input_tokens"]), abs(tokens[1] - usage["output_tokens"]))
    if max(gaps) > tolerance:
        evidence["token_gap"] = list(gaps)
        return "discrepancy", max(held, cost), "Token counts differ beyond tolerance", evidence
    note = "Gateway cost above the configured price ceiling" if cost > held else ""
    return "reconciled", cost, note, evidence


def reconcile(store: Store, gateway_rows: list[dict], tolerance: int = 0) -> Report:
    by_request: dict[str, list[dict]] = {}
    for row in gateway_rows:
        for key in {row.get("id"), row.get("parent_request_id")} - {None, ""}:
            by_request.setdefault(key, []).append(row)
    report = Report()
    for usage in store.usage_to_reconcile(OPEN_STATES):
        request_id = usage["details"].get("request_id") or usage["id"]
        attempts = by_request.get(request_id, [])
        result, amount, note, evidence = judge(usage, attempts, tolerance)
        if result in ("reconciled", "discrepancy"):
            new_state = "reconciled" if result == "reconciled" else "discrepancy"
            changed = store.apply_reconciliation(
                usage["id"],
                usage["state"],
                amount,
                new_state,
                {"result": result, "note": note, "request_id": request_id, **evidence},
            )
            if not changed:
                result, amount, note = "skipped", usage["amount"], "Row changed during run"
        else:
            amount = usage["amount"]
        report.outcomes.append(
            Outcome(usage["id"], request_id, usage["state"], usage["amount"], result, amount, note)
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="agent.toml")
    parser.add_argument("--gateway-log", required=True)
    parser.add_argument("--report", help="Write the full report as JSON to this file")
    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    report = reconcile(
        Store(settings.database),
        load_gateway_log(args.gateway_log),
        settings.reconciliation_token_tolerance,
    )
    summary = report.summary()
    print(json.dumps(summary, indent=2))
    if args.report:
        Path(args.report).write_text(
            json.dumps(
                {"summary": summary, "outcomes": [o.__dict__ for o in report.outcomes]}, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
