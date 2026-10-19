import pytest

from living_state_engine import (
    Event,
    OutcomeConflict,
    RecommendationEngine,
    SQLiteEventStore,
)
from living_state_engine.state import StateEngine


def make_event(event_id, event_type, payload, timestamp):
    return Event(
        event_id=event_id,
        project_id="project-1",
        occurred_at=timestamp,
        event_type=event_type,
        summary=f"Test event {event_id}",
        source="test",
        payload=payload,
    )


def get_recommendation(events):
    state = StateEngine().current_state(events)
    assert state is not None
    return RecommendationEngine().recommend(state)


def test_critical_risk_recommendation_links_its_evidence():
    events = [
        make_event(
            "e1",
            "status_update",
            {"status": "healthy"},
            "2026-10-17T10:00:00Z",
        ),
        make_event(
            "e2",
            "risk_raised",
            {
                "risk_id": "risk-1",
                "description": "Critical vendor outage",
                "severity": "critical",
            },
            "2026-10-17T10:10:00Z",
        ),
    ]

    recommendation = get_recommendation(events)

    assert recommendation.target_type == "risk"
    assert recommendation.target_id == "risk-1"
    assert "e2" in recommendation.evidence_event_ids
    assert recommendation.expected_metric == "target_status"
    assert recommendation.expected_value == "resolved"


def test_overdue_commitment_recommends_completion():
    events = [
        make_event(
            "e1",
            "status_update",
            {"status": "healthy"},
            "2026-10-17T10:00:00Z",
        ),
        make_event(
            "e2",
            "commitment_created",
            {
                "commitment_id": "commit-1",
                "description": "Deliver signed agreement",
                "owner": "Asha",
                "due_at": "2026-10-16",
            },
            "2026-10-17T10:10:00Z",
        ),
    ]

    recommendation = get_recommendation(events)

    assert recommendation.target_type == "commitment"
    assert recommendation.target_id == "commit-1"
    assert recommendation.expected_value == "completed"


def test_healthy_project_recommends_status_confirmation():
    events = [
        make_event(
            "e1",
            "status_update",
            {"status": "healthy"},
            "2026-10-17T10:00:00Z",
        )
    ]

    recommendation = get_recommendation(events)

    assert recommendation.expected_metric == "status_confirmed"
    assert recommendation.expected_value is True
    assert "e1" in recommendation.evidence_event_ids


def test_recommendation_and_successful_outcome_are_persisted(tmp_path):
    events = [
        make_event(
            "e1",
            "risk_raised",
            {
                "risk_id": "risk-1",
                "description": "API outage",
                "severity": "critical",
            },
            "2026-10-17T10:00:00Z",
        )
    ]
    recommendation = get_recommendation(events)

    with SQLiteEventStore(tmp_path / "test.db") as store:
        assert store.save_recommendation(recommendation) == "inserted"
        assert store.save_recommendation(recommendation) == "duplicate"

        assert store.record_outcome(
            recommendation.recommendation_id,
            actual_outcome="Risk resolved",
            actual_value="resolved",
            notes="Verified by project owner",
            observed_at="2026-10-17T12:00:00Z",
        ) == "recorded"

        saved = store.get_recommendation(
            recommendation.recommendation_id
        )

    assert saved is not None
    assert saved["outcome"]["worked"] is True
    assert saved["outcome"]["actual_value"] == "resolved"
    assert saved["outcome"]["actual_outcome"] == "Risk resolved"


def test_failed_intervention_is_recorded_as_not_working(tmp_path):
    recommendation = get_recommendation([
        make_event(
            "e1",
            "risk_raised",
            {
                "risk_id": "risk-1",
                "description": "API outage",
                "severity": "critical",
            },
            "2026-10-17T10:00:00Z",
        )
    ])

    with SQLiteEventStore(tmp_path / "test.db") as store:
        store.save_recommendation(recommendation)
        store.record_outcome(
            recommendation.recommendation_id,
            actual_outcome="Risk remains open",
            actual_value="open",
        )
        saved = store.get_recommendation(
            recommendation.recommendation_id
        )

    assert saved["outcome"]["worked"] is False


def test_conflicting_second_outcome_is_rejected(tmp_path):
    recommendation = get_recommendation([
        make_event(
            "e1",
            "risk_raised",
            {
                "risk_id": "risk-1",
                "description": "API outage",
                "severity": "critical",
            },
            "2026-10-17T10:00:00Z",
        )
    ])

    with SQLiteEventStore(tmp_path / "test.db") as store:
        store.save_recommendation(recommendation)
        store.record_outcome(
            recommendation.recommendation_id,
            actual_outcome="Still open",
            actual_value="open",
        )

        # Repeating the same observation is safe.
        assert store.record_outcome(
            recommendation.recommendation_id,
            actual_outcome="Still open",
            actual_value="open",
        ) == "duplicate"

        with pytest.raises(OutcomeConflict):
            store.record_outcome(
                recommendation.recommendation_id,
                actual_outcome="Resolved",
                actual_value="resolved",
            )