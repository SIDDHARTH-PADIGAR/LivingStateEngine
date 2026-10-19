
import pytest

from living_state_engine import Event
from living_state_engine.state import StateEngine


@pytest.fixture
def engine():
    return StateEngine()


def make_event(
    event_id,
    occurred_at,
    event_type,
    payload,
    project_id="project-1",
):
    return Event(
        event_id=event_id,
        project_id=project_id,
        occurred_at=occurred_at,
        event_type=event_type,
        summary=f"Test event {event_id}",
        source="test",
        payload=payload,
    )


def test_empty_history_has_no_state(engine):
    assert engine.replay([]) == []
    assert engine.current_state([]) is None


def test_explicit_healthy_status_is_preserved(engine):
    event = make_event(
        "e1",
        "2026-10-17T10:00:00Z",
        "status_update",
        {"status": "on_track"},
    )

    state = engine.current_state([event])

    assert state.status == "healthy"
    assert state.evidence_event_ids == ("e1",)


def test_significant_risk_overrides_healthy_status(engine):
    events = [
        make_event(
            "e1",
            "2026-10-17T10:00:00Z",
            "status_update",
            {"status": "healthy"},
        ),
        make_event(
            "e2",
            "2026-10-17T10:10:00Z",
            "risk_raised",
            {
                "risk_id": "risk-1",
                "description": "Critical API dependency is delayed",
                "severity": "high",
            },
        ),
    ]

    state = engine.current_state(events)

    assert state.status == "at_risk"
    assert set(state.evidence_event_ids) == {"e1", "e2"}
    assert "Latest explicit status: healthy" in state.status_reason
    assert "risk-1" in state.status_reason


def test_critical_risk_produces_blocked_status(engine):
    event = make_event(
        "e1",
        "2026-10-17T10:00:00Z",
        "risk_raised",
        {
            "risk_id": "risk-critical",
            "description": "Deployment cannot proceed",
            "severity": "critical",
        },
    )

    state = engine.current_state([event])

    assert state.status == "blocked"
    assert state.status == "blocked"
    assert "e1" in state.evidence_event_ids
    assert "risk-critical" in state.status_reason


def test_resolving_risk_restores_healthy_estimate(engine):
    events = [
        make_event(
            "e1",
            "2026-10-17T10:00:00Z",
            "status_update",
            {"status": "healthy"},
        ),
        make_event(
            "e2",
            "2026-10-17T10:10:00Z",
            "risk_raised",
            {
                "risk_id": "risk-1",
                "description": "Dependency delay",
                "severity": "high",
            },
        ),
        make_event(
            "e3",
            "2026-10-17T10:20:00Z",
            "risk_resolved",
            {"risk_id": "risk-1"},
        ),
    ]

    history = engine.replay(events)

    assert [version.status for version in history] == [
        "healthy",
        "at_risk",
        "healthy",
    ]
    assert history[-1].risks[0]["status"] == "resolved"
    assert "e1" in history[-1].evidence_event_ids
    assert "e2" not in history[-1].evidence_event_ids
    assert "e3" in history[-1].evidence_event_ids


def test_overdue_commitment_changes_status(engine):
    events = [
        make_event(
            "e1",
            "2026-10-17T10:00:00Z",
            "status_update",
            {"status": "healthy"},
        ),
        make_event(
            "e2",
            "2026-10-17T10:10:00Z",
            "commitment_created",
            {
                "commitment_id": "commit-1",
                "description": "Send the signed contract",
                "owner": "Asha",
                "due_at": "2026-10-16",
            },
        ),
    ]

    state = engine.current_state(events)

    assert state.status == "at_risk"
    assert state.commitments[0]["overdue"] is True
    assert set(state.evidence_event_ids) == {"e1", "e2"}


def test_out_of_order_input_is_replayed_by_event_time(engine):
    later = make_event(
        "e2",
        "2026-10-17T10:20:00Z",
        "status_update",
        {"status": "at_risk"},
    )
    earlier = make_event(
        "e1",
        "2026-10-17T10:10:00Z",
        "status_update",
        {"status": "healthy"},
    )

    history = engine.replay([later, earlier])

    assert [version.as_of_event_id for version in history] == [
        "e1",
        "e2",
    ]
    assert [version.status for version in history] == [
        "healthy",
        "at_risk",
    ]


def test_historical_snapshots_do_not_mutate(engine):
    events = [
        make_event(
            "e1",
            "2026-10-17T10:00:00Z",
            "status_update",
            {"status": "healthy"},
        ),
        make_event(
            "e2",
            "2026-10-17T10:10:00Z",
            "issue_raised",
            {
                "issue_id": "issue-1",
                "description": "Integration test failing",
                "severity": "high",
            },
        ),
    ]

    history = engine.replay(events)

    assert history[0].status == "healthy"
    assert history[0].issues == ()
    assert history[1].status == "at_risk"
    assert len(history) == 2


def test_replay_rejects_multiple_projects(engine):
    events = [
        make_event(
            "e1",
            "2026-10-17T10:00:00Z",
            "status_update",
            {"status": "healthy"},
            project_id="project-1",
        ),
        make_event(
            "e2",
            "2026-10-17T10:10:00Z",
            "status_update",
            {"status": "blocked"},
            project_id="project-2",
        ),
    ]

    with pytest.raises(ValueError, match="one project"):
        engine.replay(events)


def test_low_severity_issue_does_not_raise_project_risk(engine):
    events = [
        make_event(
            "e1",
            "2026-10-19T09:00:00Z",
            "status_update",
            {"status": "healthy"},
        ),
        make_event(
            "e2",
            "2026-10-19T09:10:00Z",
            "issue_raised",
            {
                "issue_id": "issue-1",
                "description": "Minor documentation typo",
                "severity": "low",
            },
        ),
    ]

    state = engine.current_state(events)

    assert state.status == "healthy"
    assert state.issues[0]["status"] == "open"
    assert "e2" not in state.evidence_event_ids
    assert "No significant unresolved risk or issue" in state.status_reason