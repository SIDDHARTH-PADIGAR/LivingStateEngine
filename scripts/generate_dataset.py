from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "synthetic"
REPLICATES = 8
BASE_TIME = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)


def step(
    day: int,
    minute: int,
    event_type: str,
    summary: str,
    payload: dict[str, Any],
    truth_status: str,
) -> dict[str, Any]:
    return {
        "day": day,
        "minute": minute,
        "event_type": event_type,
        "summary": summary,
        "payload": payload,
        "truth_status": truth_status,
    }


SCENARIOS = {
    "healthy_control": [
        step(0, 0, "status_update", "Project is on track",
             {"status": "healthy"}, "healthy"),
        step(0, 10, "commitment_created", "Design delivery committed",
             {"commitment_id": "commit-1",
              "description": "Deliver approved design",
              "owner": "Mira", "due_at": "2026-11-01"}, "healthy"),
        step(0, 20, "decision", "Design approach approved",
             {"decision_id": "decision-1",
              "description": "Use the approved design"}, "healthy"),
        step(0, 30, "project_update", "Milestone remains on track",
             {"milestone": "design"}, "healthy"),
    ],
    "high_risk_contradiction": [
        step(0, 0, "status_update", "Project is on track",
             {"status": "healthy"}, "healthy"),
        step(0, 10, "risk_raised", "API vendor delivery is threatened",
             {"risk_id": "risk-1", "severity": "high",
              "description": "API vendor delivery is threatened"},
             "at_risk"),
        step(0, 20, "status_update", "Owner still reports on track",
             {"status": "healthy"}, "at_risk"),
        step(0, 30, "risk_resolved", "Vendor confirms delivery",
             {"risk_id": "risk-1"}, "healthy"),
    ],
    "critical_blocker": [
        step(0, 0, "status_update", "Project is on track",
             {"status": "healthy"}, "healthy"),
        step(0, 10, "risk_raised", "Deployment dependency is unavailable",
             {"risk_id": "risk-1", "severity": "critical",
              "description": "Deployment dependency is unavailable"},
             "blocked"),
        step(0, 20, "status_update", "Owner reports on track",
             {"status": "healthy"}, "blocked"),
        step(0, 30, "risk_resolved", "Dependency restored",
             {"risk_id": "risk-1"}, "healthy"),
    ],
    "overdue_commitment": [
        step(0, 0, "status_update", "Project is on track",
             {"status": "healthy"}, "healthy"),
        step(0, 10, "commitment_created", "Signed agreement due tomorrow",
             {"commitment_id": "commit-1",
              "description": "Deliver signed agreement",
              "owner": "Asha", "due_at": "2026-10-02"}, "healthy"),
        step(2, 20, "project_update", "Agreement is still outstanding",
             {"milestone": "contract"}, "at_risk"),
        step(2, 30, "commitment_completed", "Signed agreement delivered",
             {"commitment_id": "commit-1"}, "healthy"),
    ],
    "unresolved_issue": [
        step(0, 0, "status_update", "Project is on track",
             {"status": "healthy"}, "healthy"),
        step(0, 10, "issue_raised", "Integration tests are failing",
             {"issue_id": "issue-1", "severity": "high",
              "description": "Integration tests are failing"},
             "at_risk"),
        step(0, 20, "status_update", "Owner reports on track",
             {"status": "healthy"}, "at_risk"),
        step(0, 30, "issue_resolved", "Integration tests pass",
             {"issue_id": "issue-1"}, "healthy"),
    ],
    "low_severity_risk": [
        step(0, 0, "status_update", "Project is on track",
             {"status": "healthy"}, "healthy"),
        step(0, 10, "risk_raised", "Minor documentation gap",
             {"risk_id": "risk-1", "severity": "low",
              "description": "Minor documentation gap"}, "healthy"),
        step(0, 20, "status_update", "Delivery remains on track",
             {"status": "healthy"}, "healthy"),
        step(0, 30, "risk_resolved", "Documentation updated",
             {"risk_id": "risk-1"}, "healthy"),
    ],
    "completed_project": [
        step(0, 0, "status_update", "Project is on track",
             {"status": "healthy"}, "healthy"),
        step(0, 10, "status_update", "All work is complete",
             {"status": "completed"}, "completed"),
        step(0, 20, "outcome", "Stakeholder accepts deliverables",
             {"outcome_id": "outcome-1", "result": "accepted"},
             "completed"),
        step(0, 30, "project_update", "Acceptance recorded",
             {"status": "completed"}, "completed"),
    ],
    "unknown_then_risk": [
        step(0, 0, "project_update", "A milestone was mentioned",
             {"milestone": "implementation"}, "unknown"),
        step(0, 10, "risk_raised", "Customer dependency is threatened",
             {"risk_id": "risk-1", "severity": "high",
              "description": "Customer dependency is threatened"},
             "at_risk"),
        step(0, 20, "risk_resolved", "Customer dependency confirmed",
             {"risk_id": "risk-1"}, "unknown"),
        step(0, 30, "status_update", "Owner confirms project is healthy",
             {"status": "healthy"}, "healthy"),
    ],
}


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(
                json.dumps(
                    record,
                    sort_keys=True,
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            )


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    events: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []

    for scenario_name, steps in SCENARIOS.items():
        for replicate in range(1, REPLICATES + 1):
            project_id = f"{scenario_name}-{replicate:02d}"

            for index, item in enumerate(steps, start=1):
                event_id = f"{project_id}-e{index:02d}"
                occurred_at = BASE_TIME + timedelta(
                    days=item["day"],
                    minutes=item["minute"],
                )

                events.append({
                    "event_id": event_id,
                    "project_id": project_id,
                    "occurred_at": occurred_at.isoformat(),
                    "event_type": item["event_type"],
                    "summary": item["summary"],
                    "source": "synthetic",
                    "actor": "Synthetic project owner",
                    "payload": deepcopy(item["payload"]),
                })

                labels.append({
                    "project_id": project_id,
                    "scenario": scenario_name,
                    "as_of_event_id": event_id,
                    "occurred_at": occurred_at.isoformat(),
                    "true_status": item["truth_status"],
                })

    write_jsonl(DATA_DIR / "events.jsonl", events)
    write_jsonl(DATA_DIR / "labels.jsonl", labels)

    manifest = {
        "synthetic": True,
        "scenario_templates": len(SCENARIOS),
        "replicates_per_template": REPLICATES,
        "project_count": len(SCENARIOS) * REPLICATES,
        "event_count": len(events),
        "labeled_state_count": len(labels),
        "label_source": (
            "Hand-authored expected state after each event. "
            "Labels are defined in scenario templates, not generated "
            "by StateEngine."
        ),
        "limitations": (
            "Repeated templates test deterministic behaviour and "
            "controlled failure cases. They are not independent "
            "real-world projects and do not establish real-world accuracy."
        ),
    }

    (DATA_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"\nDataset written to: {DATA_DIR}")


if __name__ == "__main__":
    main()