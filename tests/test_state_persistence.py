from living_state_engine import Event, SQLiteEventStore
from living_state_engine.state import StateEngine


def make_event(event_id, occurred_at, event_type, payload):
    return Event(
        event_id=event_id,
        project_id="persist-project",
        occurred_at=occurred_at,
        event_type=event_type,
        summary=f"Event {event_id}",
        source="test",
        payload=payload,
    )


def test_snapshots_and_current_revision_are_persisted(tmp_path):
    events = [
        make_event(
            "e1",
            "2026-10-19T10:00:00Z",
            "status_update",
            {"status": "healthy"},
        ),
        make_event(
            "e2",
            "2026-10-19T10:10:00Z",
            "risk_raised",
            {
                "risk_id": "risk-1",
                "description": "Payment API failure",
                "severity": "high",
            },
        ),
    ]

    database = tmp_path / "states.db"

    with SQLiteEventStore(database) as store:
        for event in events:
            store.ingest_event(event)

        history = StateEngine().replay(
            store.list_events("persist-project")
        )

        assert store.persist_state_history(
            "persist-project", history, trigger_event_id="e2"
        ) == 2

    # Verify persistence through a fresh database connection.
    with SQLiteEventStore(database) as reopened:
        snapshots = reopened.list_state_snapshots("persist-project")
        revisions = reopened.list_state_revisions("persist-project")

    assert [item["state"]["status"] for item in snapshots] == [
        "healthy",
        "at_risk",
    ]
    assert len(revisions) == 1
    assert revisions[0]["trigger_event_id"] == "e2"
    assert revisions[0]["state"]["status"] == "at_risk"


def test_replaying_identical_state_does_not_duplicate_revision(tmp_path):
    database = tmp_path / "idempotent.db"
    event = make_event(
        "e1",
        "2026-10-19T10:00:00Z",
        "status_update",
        {"status": "healthy"},
    )

    with SQLiteEventStore(database) as store:
        store.ingest_event(event)
        history = StateEngine().replay(
            store.list_events("persist-project")
        )

        store.persist_state_history("persist-project", history, "e1")
        store.persist_state_history("persist-project", history, "e1")

        assert len(store.list_state_snapshots("persist-project")) == 1
        assert len(store.list_state_revisions("persist-project")) == 1


def test_late_event_updates_snapshots_without_erasing_prior_revision(
    tmp_path,
):
    database = tmp_path / "late-event.db"

    healthy = make_event(
        "e1",
        "2026-10-19T10:00:00Z",
        "status_update",
        {"status": "healthy"},
    )
    later_healthy = make_event(
        "e3",
        "2026-10-19T10:30:00Z",
        "status_update",
        {"status": "healthy"},
    )
    late_risk = make_event(
        "e2",
        "2026-10-19T10:10:00Z",
        "risk_raised",
        {
            "risk_id": "risk-1",
            "description": "Payment API failing",
            "severity": "high",
        },
    )

    with SQLiteEventStore(database) as store:
        store.ingest_event(healthy)
        store.ingest_event(later_healthy)

        engine = StateEngine()
        old_history = engine.replay(
            store.list_events("persist-project")
        )
        store.persist_state_history(
            "persist-project", old_history, trigger_event_id="e3"
        )

        store.ingest_event(late_risk)

        corrected_history = engine.replay(
            store.list_events("persist-project")
        )
        store.persist_state_history(
            "persist-project",
            corrected_history,
            trigger_event_id="e2",
        )

        snapshots = store.list_state_snapshots("persist-project")
        revisions = store.list_state_revisions("persist-project")

    assert [item["as_of_event_id"] for item in snapshots] == [
        "e1", "e2", "e3"
    ]
    assert [item["state"]["status"] for item in snapshots] == [
        "healthy", "at_risk", "at_risk"
    ]

    # We retain the previous estimate and append the corrected estimate.
    assert [item["state"]["status"] for item in revisions] == [
        "healthy", "at_risk"
    ]
    assert revisions[1]["trigger_event_id"] == "e2"


def test_revision_contains_explanation_and_evidence(tmp_path):
    database = tmp_path / "evidence.db"
    events = [
        make_event(
            "e1",
            "2026-10-19T10:00:00Z",
            "status_update",
            {"status": "healthy"},
        ),
        make_event(
            "e2",
            "2026-10-19T10:10:00Z",
            "risk_raised",
            {
                "risk_id": "risk-1",
                "description": "Critical service failure",
                "severity": "critical",
            },
        ),
    ]

    with SQLiteEventStore(database) as store:
        for event in events:
            store.ingest_event(event)

        history = StateEngine().replay(
            store.list_events("persist-project")
        )
        store.persist_state_history(
            "persist-project", history, trigger_event_id="e2"
        )

        revisions = store.list_state_revisions("persist-project")

    latest = revisions[-1]["state"]
    assert latest["status"] == "blocked"
    assert "e2" in latest["evidence_event_ids"]
    assert "risk-1" in latest["status_reason"]