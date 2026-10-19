
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from living_state_engine import Event, SQLiteEventStore
from living_state_engine.state import StateEngine


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "synthetic"
BASE_TIME = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)

STATUSES = ["unknown", "healthy", "at_risk", "blocked", "completed"]
RISK_STATUSES = {"at_risk", "blocked"}

STATUS_MAP = {
    "healthy": "healthy",
    "on_track": "healthy",
    "on track": "healthy",
    "in_progress": "healthy",
    "in progress": "healthy",
    "at_risk": "at_risk",
    "at-risk": "at_risk",
    "delayed": "at_risk",
    "blocked": "blocked",
    "completed": "completed",
}


def step(
    day: int,
    minute: int,
    event_type: str,
    summary: str,
    payload: dict[str, Any],
    expected: str,
) -> dict[str, Any]:
    return {
        "day": day,
        "minute": minute,
        "event_type": event_type,
        "summary": summary,
        "payload": payload,
        "expected": expected,
    }


# Hand-authored expected states.
# Do not change these labels to match the engine's output.
SCENARIOS = {
    "completed_with_medium_residual_risk": [
        step(0, 0, "status_update", "Project is healthy",
             {"status": "healthy"}, "healthy"),
        step(0, 10, "status_update", "Project completed",
             {"status": "completed"}, "completed"),
        step(0, 20, "risk_raised", "Support handover may be delayed",
             {"risk_id": "handover", "severity": "medium",
              "description": "Support handover may be delayed"}, "at_risk"),
        step(0, 30, "risk_raised", "Handover delay reduced to minor",
             {"risk_id": "handover", "severity": "low",
              "description": "Minor handover documentation gap"},
             "completed"),
    ],
    "two_commitments_cross_deadlines": [
        step(0, 0, "status_update", "Project on track",
             {"status": "healthy"}, "healthy"),
        step(0, 10, "commitment_created", "Agreement due October 19",
             {"commitment_id": "agreement",
              "description": "Deliver signed agreement",
              "owner": "Asha", "due_at": "2026-10-19"}, "healthy"),
        step(0, 20, "commitment_created", "Checklist due October 22",
             {"commitment_id": "checklist",
              "description": "Deliver deployment checklist",
              "owner": "Ravi", "due_at": "2026-10-22"}, "healthy"),
        step(1, 60, "project_update", "Agreement is still outstanding",
             {}, "at_risk"),
        step(1, 65, "commitment_completed", "Agreement delivered",
             {"commitment_id": "agreement"}, "healthy"),
        step(4, 60, "project_update", "Checklist is still outstanding",
             {}, "at_risk"),
        step(4, 65, "commitment_completed", "Checklist delivered",
             {"commitment_id": "checklist"}, "healthy"),
    ],
    "overdue_commitment_without_status": [
        step(0, 0, "commitment_created", "A past-due item is discovered",
             {"commitment_id": "late-item",
              "description": "Deliver customer migration report",
              "owner": "Mina", "due_at": "2026-10-18"},
             "at_risk"),
    ],
    "status_requires_recovery_confirmation": [
        step(0, 0, "status_update", "Project on track",
             {"status": "healthy"}, "healthy"),
        step(0, 10, "risk_raised", "Production dependency is unstable",
             {"risk_id": "dependency", "severity": "high",
              "description": "Production dependency is unstable"},
             "at_risk"),
        step(0, 20, "status_update", "Owner reports at risk",
             {"status": "at_risk"}, "at_risk"),
        step(0, 30, "risk_resolved", "Dependency restored",
             {"risk_id": "dependency"}, "at_risk"),
        step(0, 40, "status_update", "Owner confirms recovery",
             {"status": "healthy"}, "healthy"),
    ],
    "risk_reopened_after_resolution": [
        step(0, 0, "status_update", "Project on track",
             {"status": "healthy"}, "healthy"),
        step(0, 10, "risk_raised", "Data export is blocked",
             {"risk_id": "export", "severity": "critical",
              "description": "Data export is blocked"}, "blocked"),
        step(0, 20, "risk_resolved", "Export restored",
             {"risk_id": "export"}, "healthy"),
        step(0, 30, "risk_raised", "Export becomes unreliable again",
             {"risk_id": "export", "severity": "medium",
              "description": "Export becomes unreliable again"},
             "at_risk"),
        step(0, 40, "risk_raised", "Export is now blocking delivery",
             {"risk_id": "export", "severity": "critical",
              "description": "Export is now blocking delivery"},
             "blocked"),
    ],
    "low_risk_with_medium_issue": [
        step(0, 0, "status_update", "Project on track",
             {"status": "healthy"}, "healthy"),
        step(0, 10, "risk_raised", "Minor documentation gap",
             {"risk_id": "docs", "severity": "low",
              "description": "Minor documentation gap"}, "healthy"),
        step(0, 20, "issue_raised", "Integration checks are failing",
             {"issue_id": "integration", "severity": "medium",
              "description": "Integration checks are failing"},
             "at_risk"),
        step(0, 30, "issue_resolved", "Integration checks pass",
             {"issue_id": "integration"}, "healthy"),
    ],
    "completed_with_overdue_commitment": [
        step(0, 0, "status_update", "Project completed",
             {"status": "completed"}, "completed"),
        step(0, 10, "commitment_created", "Final report is overdue",
             {"commitment_id": "final-report",
              "description": "Send final customer report",
              "owner": "Noah", "due_at": "2026-10-18"},
             "at_risk"),
        step(0, 20, "commitment_completed", "Final report sent",
             {"commitment_id": "final-report"}, "completed"),
    ],
    "invalid_status_after_completion": [
        step(0, 0, "status_update", "Project completed",
             {"status": "completed"}, "completed"),
        step(0, 10, "status_update", "Ambiguous status update",
             {"status": "mostly_done"}, "completed"),
        step(0, 20, "risk_raised", "Critical data-integrity failure",
             {"risk_id": "integrity", "severity": "critical",
              "description": "Critical data-integrity failure"},
             "blocked"),
    ],
    "wrong_risk_resolution_id": [
        step(0, 0, "status_update", "Project on track",
             {"status": "healthy"}, "healthy"),
        step(0, 10, "risk_raised", "Payment endpoint is failing",
             {"risk_id": "payment", "severity": "high",
              "description": "Payment endpoint is failing"}, "at_risk"),
        step(0, 20, "risk_resolved", "Different risk was resolved",
             {"risk_id": "unrelated"}, "at_risk"),
        step(0, 30, "risk_resolved", "Payment endpoint recovered",
             {"risk_id": "payment"}, "healthy"),
    ],
    "late_commitment_completion_arrival": [
        step(0, 0, "status_update", "Project on track",
             {"status": "healthy"}, "healthy"),
        step(0, 10, "commitment_created", "Migration report is due today",
             {"commitment_id": "migration",
              "description": "Deliver migration report",
              "owner": "Isha", "due_at": "2026-10-19"},
             "healthy"),
        step(1, 60, "project_update", "Report remains outstanding",
             {}, "at_risk"),
        step(1, 120, "commitment_completed", "Report delivered",
             {"commitment_id": "migration"}, "healthy"),
    ],
    "missing_severity_defaults_medium": [
        step(0, 0, "status_update", "Project on track",
             {"status": "healthy"}, "healthy"),
        step(0, 10, "risk_raised", "Unclassified service failure",
             {"risk_id": "service",
              "description": "Unclassified service failure"},
             "at_risk"),
        step(0, 20, "risk_resolved", "Service restored",
             {"risk_id": "service"}, "healthy"),
    ],
    "critical_issue_with_medium_risk": [
        step(0, 0, "status_update", "Project on track",
             {"status": "healthy"}, "healthy"),
        step(0, 10, "risk_raised", "Partner delivery is threatened",
             {"risk_id": "partner", "severity": "medium",
              "description": "Partner delivery is threatened"},
             "at_risk"),
        step(0, 20, "issue_raised", "Release pipeline is blocked",
             {"issue_id": "release", "severity": "critical",
              "description": "Release pipeline is blocked"},
             "blocked"),
        step(0, 30, "issue_resolved", "Release pipeline restored",
             {"issue_id": "release"}, "at_risk"),
        step(0, 40, "risk_resolved", "Partner confirms delivery",
             {"risk_id": "partner"}, "healthy"),
    ],
    "healthy_report_cannot_clear_overdue_commitment": [
        step(0, 0, "status_update", "Project on track",
             {"status": "healthy"}, "healthy"),
        step(0, 10, "commitment_created", "Customer report is overdue",
             {"commitment_id": "customer-report",
              "description": "Send customer report",
              "owner": "Arun", "due_at": "2026-10-18"},
             "at_risk"),
        step(0, 20, "status_update", "Owner reports healthy",
             {"status": "healthy"}, "at_risk"),
        step(0, 30, "commitment_completed", "Customer report delivered",
             {"commitment_id": "customer-report"}, "healthy"),
    ],
}

# Two scenarios are deliberately ingested out of event-time order.
ARRIVAL_ORDER = {
    "late_commitment_completion_arrival": [0, 1, 3, 2],
    "wrong_risk_resolution_id": [0, 2, 1, 3],
}


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    row,
                    sort_keys=True,
                    ensure_ascii=False,
                    allow_nan=False,
                ) + "\n"
            )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def build_dataset() -> tuple[list[dict], list[dict], dict]:
    event_rows = []
    label_rows = []

    for scenario, steps in SCENARIOS.items():
        project_id = f"heldout-{scenario}"
        chronological_rows = []

        for index, item in enumerate(steps, start=1):
            event_id = f"{project_id}-e{index:02d}"
            occurred_at = BASE_TIME + timedelta(
                days=item["day"],
                minutes=item["minute"],
            )

            chronological_rows.append({
                "event_id": event_id,
                "project_id": project_id,
                "occurred_at": occurred_at.isoformat(),
                "event_type": item["event_type"],
                "summary": item["summary"],
                "source": "synthetic-heldout",
                "actor": "Synthetic project owner",
                "payload": item["payload"],
            })

            label_rows.append({
                "project_id": project_id,
                "scenario": scenario,
                "as_of_event_id": event_id,
                "occurred_at": occurred_at.isoformat(),
                "true_status": item["expected"],
            })

        order = ARRIVAL_ORDER.get(
            scenario, list(range(len(chronological_rows)))
        )
        event_rows.extend(chronological_rows[index] for index in order)

    manifest = {
        "synthetic": True,
        "benchmark_type": "held-out scenario challenge",
        "scenario_count": len(SCENARIOS),
        "event_count": len(event_rows),
        "labeled_states": len(label_rows),
        "scenarios": list(SCENARIOS),
        "out_of_order_scenarios": list(ARRIVAL_ORDER),
        "label_source": (
            "Expected statuses are hand-authored in this script, "
            "independently of StateEngine output."
        ),
        "limitations": (
            "This is a small synthetic challenge, not real-world validation. "
            "It is a one-time evaluation of the existing engine rules."
        ),
    }

    return event_rows, label_rows, manifest


def latest_status_baseline(events: list[Event]) -> dict[str, str]:
    status = "unknown"
    predictions = {}

    for event in sorted(
        events,
        key=lambda item: (item.occurred_at, item.event_id),
    ):
        if event.event_type in {"status_update", "project_update"}:
            raw = event.payload.get("status")
            if isinstance(raw, str):
                status = STATUS_MAP.get(raw.strip().lower(), status)

        predictions[event.event_id] = status

    return predictions


def calculate_metrics(
    truth: list[str],
    predictions: list[str],
) -> dict[str, Any]:
    correct = sum(a == b for a, b in zip(truth, predictions))

    tp = sum(
        actual in RISK_STATUSES and predicted in RISK_STATUSES
        for actual, predicted in zip(truth, predictions)
    )
    fp = sum(
        actual not in RISK_STATUSES and predicted in RISK_STATUSES
        for actual, predicted in zip(truth, predictions)
    )
    fn = sum(
        actual in RISK_STATUSES and predicted not in RISK_STATUSES
        for actual, predicted in zip(truth, predictions)
    )
    tn = sum(
        actual not in RISK_STATUSES and predicted not in RISK_STATUSES
        for actual, predicted in zip(truth, predictions)
    )

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall else 0.0
    )

    return {
        "correct": correct,
        "total": len(truth),
        "accuracy": round(correct / len(truth), 4) if truth else 0.0,
        "risk_detection": {
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "true_negative": tn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        },
    }


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    events_path = DATA_DIR / "heldout_events.jsonl"
    labels_path = DATA_DIR / "heldout_labels.jsonl"
    manifest_path = DATA_DIR / "heldout_manifest.json"
    results_path = DATA_DIR / "heldout_results.json"

    generated_events, generated_labels, manifest = build_dataset()
    write_jsonl(events_path, generated_events)
    write_jsonl(labels_path, generated_labels)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # Ingest in recorded arrival order, then ask the real event store for
    # event-time order before replaying. No StateEngine rules are changed.
    engine_predictions = {}
    baseline_predictions = {}

    with SQLiteEventStore(":memory:") as store:
        for row in read_jsonl(events_path):
            store.ingest_event(Event.from_dict(row))

        engine = StateEngine()

        for project_id in store.list_projects():
            events = store.list_events(project_id)

            for version in engine.replay(events):
                engine_predictions[
                    (project_id, version.as_of_event_id)
                ] = version.status

            for event_id, status in latest_status_baseline(events).items():
                baseline_predictions[(project_id, event_id)] = status

    labels = read_jsonl(labels_path)
    truth_by_key = {
        (row["project_id"], row["as_of_event_id"]): row["true_status"]
        for row in labels
    }

    if set(truth_by_key) != set(engine_predictions):
        raise ValueError("Labels and engine predictions do not match.")
    if set(truth_by_key) != set(baseline_predictions):
        raise ValueError("Labels and baseline predictions do not match.")

    keys = sorted(truth_by_key)
    truth = [truth_by_key[key] for key in keys]
    engine_pred = [engine_predictions[key] for key in keys]
    baseline_pred = [baseline_predictions[key] for key in keys]

    confusion = {
        actual: {predicted: 0 for predicted in STATUSES}
        for actual in STATUSES
    }
    for actual, predicted in zip(truth, engine_pred):
        confusion[actual][predicted] += 1

    mismatches = [
        {
            "scenario": next(
                row["scenario"] for row in labels
                if row["project_id"] == project_id
                and row["as_of_event_id"] == event_id
            ),
            "project_id": project_id,
            "event_id": event_id,
            "expected": truth_by_key[(project_id, event_id)],
            "state_engine": engine_predictions[(project_id, event_id)],
            "baseline": baseline_predictions[(project_id, event_id)],
        }
        for project_id, event_id in keys
        if truth_by_key[(project_id, event_id)]
        != engine_predictions[(project_id, event_id)]
    ]

    per_scenario = {}
    for scenario in SCENARIOS:
        scenario_keys = [
            (row["project_id"], row["as_of_event_id"])
            for row in labels
            if row["scenario"] == scenario
        ]
        scenario_truth = [truth_by_key[key] for key in scenario_keys]
        scenario_pred = [engine_predictions[key] for key in scenario_keys]
        per_scenario[scenario] = {
            "correct": sum(
                a == b for a, b in zip(scenario_truth, scenario_pred)
            ),
            "total": len(scenario_keys),
        }

    report = {
        "notice": manifest["limitations"],
        "dataset": {
            "synthetic": True,
            "scenarios": len(SCENARIOS),
            "events": len(generated_events),
            "labeled_states": len(labels),
        },
        "state_accuracy": {
            "state_engine": calculate_metrics(truth, engine_pred),
            "latest_explicit_status_baseline": calculate_metrics(
                truth, baseline_pred
            ),
        },
        "confusion_matrix": confusion,
        "per_scenario": per_scenario,
        "misclassified_states": mismatches,
    }

    results_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nEvents: {events_path}")
    print(f"Hand-authored labels: {labels_path}")
    print(f"Results: {results_path}")


if __name__ == "__main__":
    main()
