from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from living_state_engine import Event
from living_state_engine.state import StateEngine


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "synthetic"

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

ALL_STATUSES = [
    "unknown",
    "healthy",
    "at_risk",
    "blocked",
    "completed",
]
RISK_STATUSES = {"at_risk", "blocked"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [
            json.loads(line)
            for line in handle
            if line.strip()
        ]


def baseline_predictions(
    events: list[Event],
) -> dict[str, str]:
    """Predict status using only the most recent explicit status."""
    status = "unknown"
    predictions: dict[str, str] = {}

    for event in sorted(
        events,
        key=lambda item: (item.occurred_at, item.event_id),
    ):
        if event.event_type in {"status_update", "project_update"}:
            raw_status = event.payload.get("status")
            if isinstance(raw_status, str):
                normalized = STATUS_MAP.get(raw_status.strip().lower())
                if normalized is not None:
                    status = normalized

        predictions[event.event_id] = status

    return predictions


def state_accuracy(
    truth: list[str],
    predictions: list[str],
) -> dict[str, Any]:
    if not truth:
        raise ValueError("Cannot evaluate an empty dataset")

    correct = sum(
        expected == predicted
        for expected, predicted in zip(truth, predictions)
    )
    return {
        "correct": correct,
        "total": len(truth),
        "accuracy": round(correct / len(truth), 4),
    }


def risk_metrics(
    truth: list[str],
    predictions: list[str],
) -> dict[str, Any]:
    true_positive = sum(
        expected in RISK_STATUSES and predicted in RISK_STATUSES
        for expected, predicted in zip(truth, predictions)
    )
    false_positive = sum(
        expected not in RISK_STATUSES and predicted in RISK_STATUSES
        for expected, predicted in zip(truth, predictions)
    )
    false_negative = sum(
        expected in RISK_STATUSES and predicted not in RISK_STATUSES
        for expected, predicted in zip(truth, predictions)
    )
    true_negative = sum(
        expected not in RISK_STATUSES and predicted not in RISK_STATUSES
        for expected, predicted in zip(truth, predictions)
    )

    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive else 0.0
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative else 0.0
    )
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall else 0.0
    )

    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def main() -> None:
    event_rows = read_jsonl(DATA_DIR / "events.jsonl")
    label_rows = read_jsonl(DATA_DIR / "labels.jsonl")
    manifest = json.loads(
        (DATA_DIR / "manifest.json").read_text(encoding="utf-8")
    )

    events_by_project: dict[str, list[Event]] = defaultdict(list)

    for row in event_rows:
        event = Event.from_dict(row)
        events_by_project[event.project_id].append(event)

    truth_by_key = {
        (row["project_id"], row["as_of_event_id"]): row["true_status"]
        for row in label_rows
    }

    engine_predictions: dict[tuple[str, str], str] = {}
    baseline_predictions_by_key: dict[tuple[str, str], str] = {}

    engine = StateEngine()

    for project_id, events in sorted(events_by_project.items()):
        for state in engine.replay(events):
            engine_predictions[
                (project_id, state.as_of_event_id)
            ] = state.status

        for event_id, status in baseline_predictions(events).items():
            baseline_predictions_by_key[(project_id, event_id)] = status

    if set(truth_by_key) != set(engine_predictions):
        raise ValueError(
            "Ground-truth labels and engine predictions do not match"
        )

    if set(truth_by_key) != set(baseline_predictions_by_key):
        raise ValueError(
            "Ground-truth labels and baseline predictions do not match"
        )

    keys = sorted(truth_by_key)
    truth = [truth_by_key[key] for key in keys]
    engine_pred = [engine_predictions[key] for key in keys]
    baseline_pred = [baseline_predictions_by_key[key] for key in keys]

    confusion = {
        true_status: {
            predicted_status: 0
            for predicted_status in ALL_STATUSES
        }
        for true_status in ALL_STATUSES
    }

    for expected, predicted in zip(truth, engine_pred):
        confusion[expected][predicted] += 1

    report = {
        "notice": (
            "Synthetic benchmark only. Repeated templates are not "
            "independent real-world projects."
        ),
        "dataset": {
            "synthetic": manifest["synthetic"],
            "projects": len(events_by_project),
            "events": len(event_rows),
            "labeled_states": len(label_rows),
            "scenario_templates": manifest["scenario_templates"],
        },
        "state_accuracy": {
            "state_engine": state_accuracy(truth, engine_pred),
            "latest_explicit_status_baseline": state_accuracy(
                truth, baseline_pred
            ),
        },
        "risk_detection": {
            "positive_definition": (
                "Ground-truth or predicted status is at_risk or blocked"
            ),
            "state_engine": risk_metrics(truth, engine_pred),
            "latest_explicit_status_baseline": risk_metrics(
                truth, baseline_pred
            ),
        },
        "state_engine_confusion_matrix": confusion,
    }

    output_path = DATA_DIR / "evaluation_results.json"
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()