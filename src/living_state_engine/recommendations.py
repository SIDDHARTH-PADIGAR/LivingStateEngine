## This file chooses one action from the current state.
## It does not call an LLM or invent advice independently
## a recommendation carries its evidence references and a testable expectation
## The recommendation ID is deterministic for the same recommendation content
# Replaying the state won't create new logical recommendation ID


from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from living_state_engine.state import StateVersion


_SEVERITY_RANK = {
    "blocker": 5,
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


@dataclass(frozen=True, slots=True)
class Recommendation:
    recommendation_id: str
    project_id: str
    state_version: int
    state_as_of_event_id: str
    action: str
    rationale: str
    evidence_event_ids: tuple[str, ...]
    target_type: str | None
    target_id: str | None
    expected_metric: str
    expected_operator: str
    expected_value: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "project_id": self.project_id,
            "state_version": self.state_version,
            "state_as_of_event_id": self.state_as_of_event_id,
            "action": self.action,
            "rationale": self.rationale,
            "evidence_event_ids": list(self.evidence_event_ids),
            "target_type": self.target_type,
            "target_id": self.target_id,
            "expected_metric": self.expected_metric,
            "expected_operator": self.expected_operator,
            "expected_value": self.expected_value,
        }


class RecommendationEngine:
    """Generate one deterministic, evidence-linked next action."""

    def recommend(self, state: StateVersion) -> Recommendation:
        risks = [
            item for item in state.risks
            if item["status"] == "open"
        ]
        issues = [
            item for item in state.issues
            if item["status"] == "open"
        ]
        commitments = [
            item for item in state.commitments
            if item["status"] == "open"
        ]

        target = None
        target_type = None

        if state.status == "blocked":
            candidates = [
                ("risk", item) for item in risks
                if item["severity"] in {"critical", "blocker"}
            ] + [
                ("issue", item) for item in issues
                if item["severity"] in {"critical", "blocker"}
            ]

            if candidates:
                candidates.sort(
                    key=lambda pair: (
                        -_SEVERITY_RANK.get(pair[1]["severity"], 0),
                        pair[0],
                        pair[1]["id"],
                    )
                )
                target_type, target = candidates[0]
                action = (
                    f"Resolve the blocking {target_type}: "
                    f"{target['description']}."
                )
                metric = "target_status"
                operator = "eq"
                expected = "resolved"
            else:
                action = (
                    "Identify the concrete blocker, its owner, "
                    "and the condition required to unblock the project."
                )
                metric = "blocker_recorded"
                operator = "eq"
                expected = True

        elif state.status == "at_risk":
            overdue = [
                item for item in commitments
                if item["overdue"]
            ]

            if overdue:
                overdue.sort(
                    key=lambda item: (
                        item["due_at"] or "9999",
                        item["id"],
                    )
                )
                target_type = "commitment"
                target = overdue[0]
                action = (
                    f"Complete the overdue commitment: "
                    f"{target['description']}."
                )
                metric = "target_status"
                operator = "eq"
                expected = "completed"

            elif risks:
                risks.sort(
                    key=lambda item: (
                        -_SEVERITY_RANK.get(item["severity"], 0),
                        item["id"],
                    )
                )
                target_type = "risk"
                target = risks[0]
                action = (
                    f"Mitigate and resolve the risk: "
                    f"{target['description']}."
                )
                metric = "target_status"
                operator = "eq"
                expected = "resolved"

            elif issues:
                issues.sort(
                    key=lambda item: (
                        -_SEVERITY_RANK.get(item["severity"], 0),
                        item["id"],
                    )
                )
                target_type = "issue"
                target = issues[0]
                action = (
                    f"Resolve the outstanding issue: "
                    f"{target['description']}."
                )
                metric = "target_status"
                operator = "eq"
                expected = "resolved"

            else:
                action = (
                    "Ask the project owner to confirm the current "
                    "status, next milestone, and delivery confidence."
                )
                metric = "status_confirmed"
                operator = "eq"
                expected = True

        elif state.status == "healthy":
            missing_details = [
                item for item in commitments
                if not item["owner"] or not item["due_at"]
            ]

            if missing_details:
                target = sorted(
                    missing_details,
                    key=lambda item: item["id"],
                )[0]
                target_type = "commitment"
                action = (
                    f"Assign an owner and deadline to the commitment: "
                    f"{target['description']}."
                )
                metric = "commitment_ready"
                operator = "eq"
                expected = True
            else:
                action = (
                    "Confirm with the project owner that the next "
                    "milestone remains on track."
                )
                metric = "status_confirmed"
                operator = "eq"
                expected = True

        elif state.status == "completed":
            action = (
                "Confirm stakeholder acceptance of the completed "
                "project deliverables."
            )
            metric = "acceptance_confirmed"
            operator = "eq"
            expected = True

        else:
            action = (
                "Request a current update from the project owner "
                "and confirm the next milestone."
            )
            metric = "status_confirmed"
            operator = "eq"
            expected = True

        target_id = target["id"] if target else None

        evidence = list(state.evidence_event_ids)
        if target:
            evidence.extend(target["evidence_event_ids"])
        evidence.append(state.as_of_event_id)
        evidence_ids = tuple(dict.fromkeys(evidence))

        if target:
            target_event_ids = ", ".join(
                target["evidence_event_ids"]
            ) or "none recorded"
            rationale = (
                f"Current estimated status: {state.status}. "
                f"{state.status_reason} "
                f"This action targets {target_type} {target_id!r}. "
                f"Target evidence events: {target_event_ids}."
            )
        else:
            rationale = (
                f"Current estimated status: {state.status}. "
                f"{state.status_reason} "
                "The recommendation seeks explicit confirmation "
                "or additional evidence before further decisions."
            )

        body = {
            "project_id": state.project_id,
            "state_version": state.version,
            "state_as_of_event_id": state.as_of_event_id,
            "action": action,
            "rationale": rationale,
            "evidence_event_ids": list(evidence_ids),
            "target_type": target_type,
            "target_id": target_id,
            "expected_metric": metric,
            "expected_operator": operator,
            "expected_value": expected,
        }

        recommendation_id = "rec_" + hashlib.sha256(
            _canonical_json(body).encode("utf-8")
        ).hexdigest()[:20]

        return Recommendation(
            recommendation_id=recommendation_id,
            **body,
        )