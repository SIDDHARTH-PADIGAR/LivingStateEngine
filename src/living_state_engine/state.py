
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable

from living_state_engine.events import Event


_STATUS_MAP = {
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

_BLOCKING_SEVERITIES = {"critical", "blocker"}
_SIGNIFICANT_SEVERITIES = {"medium", "high", "critical", "blocker"}


@dataclass(frozen=True, slots=True)
class StateVersion:
    version: int
    project_id: str
    as_of_event_id: str
    occurred_at: str
    status: str
    status_reason: str
    commitments: tuple[dict[str, Any], ...]
    risks: tuple[dict[str, Any], ...]
    issues: tuple[dict[str, Any], ...]
    evidence_event_ids: tuple[str, ...]
    changes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible copy of this historical snapshot."""
        return {
            "version": self.version,
            "project_id": self.project_id,
            "as_of_event_id": self.as_of_event_id,
            "occurred_at": self.occurred_at,
            "status": self.status,
            "status_reason": self.status_reason,
            "commitments": deepcopy(list(self.commitments)),
            "risks": deepcopy(list(self.risks)),
            "issues": deepcopy(list(self.issues)),
            "evidence_event_ids": list(self.evidence_event_ids),
            "changes": list(self.changes),
        }


def _canonical_status(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return _STATUS_MAP.get(value.strip().lower())


def _severity(value: Any) -> str:
    if not isinstance(value, str):
        return "medium"

    value = value.strip().lower()
    if value in {"low", "medium", "high", "critical", "blocker"}:
        return value

    return "medium"


def _is_overdue(due_at: Any, as_of: datetime) -> bool:
    """Treat date-only deadlines as due at the end of that calendar day."""
    if not isinstance(due_at, str) or not due_at.strip():
        return False

    try:
        if len(due_at) == 10:
            due_date = date.fromisoformat(due_at)
            return due_date < as_of.date()

        deadline = datetime.fromisoformat(
            due_at.replace("Z", "+00:00")
        )
    except ValueError:
        return False

    # A datetime deadline without timezone information is ambiguous.
    if deadline.tzinfo is None or deadline.utcoffset() is None:
        return False

    return deadline.astimezone(timezone.utc) < as_of


def _add_evidence(item: dict[str, Any], event_id: str) -> None:
    if event_id not in item["evidence_event_ids"]:
        item["evidence_event_ids"].append(event_id)


class StateEngine:
    """Reconstruct project state deterministically from its event history."""

    def replay(self, events: Iterable[Event]) -> list[StateVersion]:
        ordered = sorted(
            events,
            key=lambda event: (event.occurred_at, event.event_id),
        )

        if not ordered:
            return []

        project_ids = {event.project_id for event in ordered}
        if len(project_ids) != 1:
            raise ValueError("Replay accepts events from one project only")

        project_id = ordered[0].project_id
        event_order = {
            event.event_id: index
            for index, event in enumerate(ordered)
        }

        explicit_status: dict[str, str] | None = None
        commitments: dict[str, dict[str, Any]] = {}
        risks: dict[str, dict[str, Any]] = {}
        issues: dict[str, dict[str, Any]] = {}
        history: list[StateVersion] = []

        for event in ordered:
            previous_status = (
                history[-1].status if history else "unknown"
            )
            notes: list[str] = []
            payload = event.payload

            if event.event_type in {"status_update", "project_update"}:
                raw_status = payload.get("status")
                normalized = _canonical_status(raw_status)

                if normalized is not None:
                    explicit_status = {
                        "status": normalized,
                        "event_id": event.event_id,
                    }
                    notes.append(
                        f"Status evidence: {raw_status!r} "
                        f"interpreted as {normalized!r}."
                    )

            if event.event_type == "commitment_created":
                item_id = str(
                    payload.get("commitment_id") or event.event_id
                ).strip()

                item = commitments.setdefault(
                    item_id,
                    {
                        "id": item_id,
                        "description": "",
                        "owner": None,
                        "due_at": None,
                        "status": "open",
                        "evidence_event_ids": [],
                    },
                )
                item["description"] = str(
                    payload.get("description") or event.summary
                )
                item["owner"] = payload.get("owner")
                item["due_at"] = payload.get("due_at")
                item["status"] = "open"
                _add_evidence(item, event.event_id)
                notes.append(
                    f"Commitment opened: {item['description']}."
                )

            elif event.event_type == "commitment_completed":
                item_id = str(
                    payload.get("commitment_id") or ""
                ).strip()
                item = commitments.get(item_id)

                if item is None:
                    notes.append(
                        f"Completion references unknown commitment "
                        f"{item_id!r}; no commitment was changed."
                    )
                else:
                    item["status"] = "completed"
                    _add_evidence(item, event.event_id)
                    notes.append(
                        f"Commitment completed: {item['description']}."
                    )

            if event.event_type == "risk_raised":
                item_id = str(
                    payload.get("risk_id") or event.event_id
                ).strip()

                item = risks.setdefault(
                    item_id,
                    {
                        "id": item_id,
                        "description": "",
                        "severity": "medium",
                        "status": "open",
                        "evidence_event_ids": [],
                    },
                )
                item["description"] = str(
                    payload.get("description") or event.summary
                )
                item["severity"] = _severity(payload.get("severity"))
                item["status"] = "open"
                _add_evidence(item, event.event_id)
                notes.append(
                    f"Risk raised: {item['description']} "
                    f"(severity: {item['severity']})."
                )

            elif event.event_type == "risk_resolved":
                item_id = str(payload.get("risk_id") or "").strip()
                item = risks.get(item_id)

                if item is None:
                    notes.append(
                        f"Resolution references unknown risk {item_id!r}; "
                        "no risk was changed."
                    )
                else:
                    item["status"] = "resolved"
                    _add_evidence(item, event.event_id)
                    notes.append(f"Risk resolved: {item['description']}.")

            if event.event_type == "issue_raised":
                item_id = str(
                    payload.get("issue_id") or event.event_id
                ).strip()

                item = issues.setdefault(
                    item_id,
                    {
                        "id": item_id,
                        "description": "",
                        "severity": "medium",
                        "status": "open",
                        "evidence_event_ids": [],
                    },
                )
                item["description"] = str(
                    payload.get("description") or event.summary
                )
                item["severity"] = _severity(payload.get("severity"))
                item["status"] = "open"
                _add_evidence(item, event.event_id)
                notes.append(
                    f"Issue raised: {item['description']} "
                    f"(severity: {item['severity']})."
                )

            elif event.event_type == "issue_resolved":
                item_id = str(payload.get("issue_id") or "").strip()
                item = issues.get(item_id)

                if item is None:
                    notes.append(
                        f"Resolution references unknown issue {item_id!r}; "
                        "no issue was changed."
                    )
                else:
                    item["status"] = "resolved"
                    _add_evidence(item, event.event_id)
                    notes.append(
                        f"Issue resolved: {item['description']}."
                    )

            as_of = datetime.fromisoformat(
                event.occurred_at
            ).astimezone(timezone.utc)

            snapshot_commitments = []
            for item in commitments.values():
                row = deepcopy(item)
                row["overdue"] = (
                    row["status"] == "open"
                    and _is_overdue(row["due_at"], as_of)
                )
                snapshot_commitments.append(row)

            snapshot_risks = deepcopy(list(risks.values()))
            snapshot_issues = deepcopy(list(issues.values()))

            active_commitments = [
                item for item in snapshot_commitments
                if item["status"] == "open"
            ]
            overdue_commitments = [
                item for item in active_commitments
                if item["overdue"]
            ]
            active_risks = [
                item for item in snapshot_risks
                if item["status"] == "open"
            ]
            significant_risks = [
                item for item in active_risks
                if item["severity"] in _SIGNIFICANT_SEVERITIES
            ]
            blocking_risks = [
                item for item in active_risks
                if item["severity"] in _BLOCKING_SEVERITIES
            ]
            active_issues = [
                item for item in snapshot_issues
                if item["status"] == "open"
            ]
            significant_issues = [
                item for item in active_issues
                if item["severity"] in _SIGNIFICANT_SEVERITIES
            ]
            blocking_issues = [
                item for item in active_issues
                if item["severity"] in _BLOCKING_SEVERITIES
            ]

            explicit_value = (
                explicit_status["status"] if explicit_status else None
            )

            # Blocking evidence overrides a healthy or completed claim.
            if explicit_value == "blocked" or blocking_risks or blocking_issues:
                status = "blocked"
            elif (
                explicit_value == "at_risk"
                or significant_risks
                or overdue_commitments
                or significant_issues
            ):
                status = "at_risk"
            elif explicit_value in {"healthy", "completed"}:
                status = explicit_value
            else:
                status = "unknown"

            reasons: list[str] = []
            if explicit_status is not None:
                reasons.append(
                    f"Latest explicit status: {explicit_value} "
                    f"(event {explicit_status['event_id']})."
                )

            if blocking_risks:
                reasons.append(
                    "Critical/blocking risks remain open: "
                    + ", ".join(item["id"] for item in blocking_risks)
                    + "."
                )
            if blocking_issues:
                reasons.append(
                    "Critical/blocking issues remain open: "
                    + ", ".join(item["id"] for item in blocking_issues)
                    + "."
                )
            if significant_risks:
                reasons.append(
                    "Significant unresolved risks: "
                    + ", ".join(item["id"] for item in significant_risks)
                    + "."
                )
            if overdue_commitments:
                reasons.append(
                    "Overdue commitments: "
                    + ", ".join(
                        item["id"] for item in overdue_commitments
                    )
                    + "."
                )
            if significant_issues:
                reasons.append(
                    "Significant unresolved issues: "
                    + ", ".join(
                        item["id"] for item in significant_issues
                    )
                    + "."
                )

            if status == "healthy":
                reason = (
                    "; ".join(reasons)
                    if reasons
                    else "No explicit healthy status or active risk evidence."
                )
                reason += (
                    " No significant unresolved risk or issue, "
                    "and no overdue commitment was detected "
                    "in the recorded history."
                )
            elif status == "completed":
                reason = (
                    "; ".join(reasons)
                    or "The latest explicit status marks the project completed."
                )
            elif status == "blocked":
                reason = "; ".join(reasons) or "Blocking evidence is present."
            elif status == "at_risk":
                reason = "; ".join(reasons) or (
                    "The latest explicit status marks the project at risk."
                )
            else:
                reason = "Insufficient explicit evidence to estimate project status."

            # Link the estimate to the exact events that currently support
            # or contradict it, in deterministic event-time order.
            evidence_ids: set[str] = set()
            if explicit_status is not None:
                evidence_ids.add(explicit_status["event_id"])

            for item in significant_risks + blocking_risks:
                evidence_ids.update(item["evidence_event_ids"])
            for item in significant_issues:
                evidence_ids.update(item["evidence_event_ids"])
            for item in overdue_commitments:
                evidence_ids.update(item["evidence_event_ids"])

            if status != previous_status:
                # Include the event that caused this state transition.
                evidence_ids.add(event.event_id)
                notes.append(
                    f"Estimated status changed from {previous_status} "
                    f"to {status}."
                )

            ordered_evidence = tuple(
                sorted(
                    evidence_ids,
                    key=lambda event_id: event_order.get(event_id, -1),
                )
            )

            if not notes:
                notes.append(f"Recorded evidence: {event.summary}")

            history.append(
                StateVersion(
                    version=len(history) + 1,
                    project_id=project_id,
                    as_of_event_id=event.event_id,
                    occurred_at=event.occurred_at,
                    status=status,
                    status_reason=reason,
                    commitments=tuple(snapshot_commitments),
                    risks=tuple(snapshot_risks),
                    issues=tuple(snapshot_issues),
                    evidence_event_ids=ordered_evidence,
                    changes=tuple(notes),
                )
            )

        return history

    def current_state(self, events: Iterable[Event]) -> StateVersion | None:
        history = self.replay(events)
        return history[-1] if history else None
