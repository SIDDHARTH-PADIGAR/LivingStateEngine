
import pytest

from living_state_engine import (
    DuplicateEventConflict,
    Event,
    SQLiteEventStore,
)
from living_state_engine.state import StateEngine


def make_event(
    event_id,
    occurred_at,
    event_type,
    payload,
    project_id="integration-project",
):
    return Event(
        event_id=event_id,
        project_id=project_id,
        occurred_at=occurred_at,
        event_type=event_type,
        summary=f"Integration event {event_id}",
        source="test",
        payload=payload,
    )


def test_late_arrival_reconstructs_correct_timeline(tmp_path):
    database = tmp_path / "events.db"
    engine = StateEngine()

    healthy = make_event(
        "e1",
        "2026-10-17T10:00:00Z",
        "status_update",
        {"status": "healthy"},
    )
    later_status = make_event(
        "e3",
        "2026-10-17T10:20:00Z",
        "status_update",
        {"status": "healthy"},
    )
    late_risk = make_event(
        "e2",
        "2026-10-17T10:10:00Z",
        "risk_raised",
        {
            "risk_id": "risk-1",
            "description": "Payment API dependency is failing",
            "severity": "high",
        },
    )

    with SQLiteEventStore(database) as store:
        store.ingest_event(healthy)
        store.ingest_event(later_status)

        before = engine.replay(store.list_events("integration-project"))
        assert [version.status for version in before] == [
            "healthy",
            "healthy",
        ]

        # The risk arrives late, but its event time is earlier.
        assert store.ingest_event(late_risk) == "inserted"

        events = store.list_events("integration-project")
        history = engine.replay(events)

        assert [event.event_id for event in events] == [
            "e1",
            "e2",
            "e3",
        ]
        assert [version.status for version in history] == [
            "healthy",
            "at_risk",
            "at_risk",
        ]
        assert "e2" in history[-1].evidence_event_ids
        assert "e3" in history[-1].evidence_event_ids


def test_event_ledger_survives_database_reopen(tmp_path):
    database = tmp_path / "persistent.db"

    with SQLiteEventStore(database) as store:
        store.ingest_event(
            make_event(
                "e1",
                "2026-10-17T10:00:00Z",
                "status_update",
                {"status": "healthy"},
            )
        )
        store.ingest_event(
            make_event(
                "e2",
                "2026-10-17T10:10:00Z",
                "risk_raised",
                {
                    "risk_id": "risk-1",
                    "description": "Critical deployment blocker",
                    "severity": "critical",
                },
            )
        )

    # Open a new connection to the same database file.
    with SQLiteEventStore(database) as reopened:
        events = reopened.list_events("integration-project")
        state = StateEngine().current_state(events)

        assert reopened.count_events() == 2
        assert state is not None
        assert state.status == "blocked"
        assert "e2" in state.evidence_event_ids


def test_repeated_ingestion_does_not_duplicate_events(tmp_path):
    event = make_event(
        "e1",
        "2026-10-17T10:00:00Z",
        "status_update",
        {"status": "healthy"},
    )

    with SQLiteEventStore(tmp_path / "duplicates.db") as store:
        assert store.ingest_event(event) == "inserted"
        assert store.ingest_event(event) == "duplicate"
        assert store.ingest_event(event) == "duplicate"
        assert store.count_events() == 1


def test_reusing_event_id_with_different_content_fails(tmp_path):
    original = make_event(
        "e1",
        "2026-10-17T10:00:00Z",
        "status_update",
        {"status": "healthy"},
    )
    changed = make_event(
        "e1",
        "2026-10-17T10:00:00Z",
        "status_update",
        {"status": "blocked"},
    )

    with SQLiteEventStore(tmp_path / "conflict.db") as store:
        store.ingest_event(original)

        with pytest.raises(DuplicateEventConflict):
            store.ingest_event(changed)

        assert store.get_event("e1") == original


def test_equal_timestamps_have_deterministic_order():
    # Resolution sorts first, then risk creation, then healthy status.
    events = [
        make_event(
            "z-status",
            "2026-10-17T10:00:00Z",
            "status_update",
            {"status": "healthy"},
        ),
        make_event(
            "b-risk",
            "2026-10-17T10:00:00Z",
            "risk_raised",
            {
                "risk_id": "risk-1",
                "description": "Unresolved payment failure",
                "severity": "high",
            },
        ),
        make_event(
            "a-resolution",
            "2026-10-17T10:00:00Z",
            "risk_resolved",
            {"risk_id": "risk-1"},
        ),
    ]

    history = StateEngine().replay(events)

    assert [version.as_of_event_id for version in history] == [
        "a-resolution",
        "b-risk",
        "z-status",
    ]
    assert history[-1].status == "at_risk"
    assert history[-1].risks[0]["status"] == "open"
