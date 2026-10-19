
import pytest

from living_state_engine import (
    DuplicateEventConflict,
    Event,
    EventValidationError,
    SQLiteEventStore,
)


@pytest.fixture
def store(tmp_path):
    database = tmp_path / "events.db"
    instance = SQLiteEventStore(database)
    yield instance
    instance.close()


def make_event(
    event_id="event-1",
    occurred_at="2026-10-17T10:00:00+05:30",
    summary="Project update",
    project_id="project-1",
):
    return Event(
        event_id=event_id,
        project_id=project_id,
        occurred_at=occurred_at,
        event_type="project_update",
        summary=summary,
        source="test",
        payload={"status": "on_track"},
    )


def test_insert_and_read_event(store):
    event = make_event()

    assert store.ingest_event(event) == "inserted"
    assert store.get_event(event.event_id) == event
    assert store.count_events() == 1


def test_identical_duplicate_is_ignored(store):
    event = make_event()

    assert store.ingest_event(event) == "inserted"
    assert store.ingest_event(event) == "duplicate"
    assert store.count_events() == 1


def test_conflicting_duplicate_id_is_rejected(store):
    original = make_event()
    conflicting = make_event(summary="Different evidence")

    store.ingest_event(original)

    with pytest.raises(DuplicateEventConflict):
        store.ingest_event(conflicting)

    assert store.get_event(original.event_id) == original
    assert store.count_events() == 1


def test_events_are_returned_in_event_time_order(store):
    later = make_event(
        event_id="event-later",
        occurred_at="2026-10-17T10:30:00+05:30",
    )
    earlier = make_event(
        event_id="event-earlier",
        occurred_at="2026-10-17T10:10:00+05:30",
    )

    # Deliberately ingest the later event first.
    store.ingest_event(later)
    store.ingest_event(earlier)

    events = store.list_events("project-1")

    assert [event.event_id for event in events] == [
        "event-earlier",
        "event-later",
    ]


def test_projects_are_isolated(store):
    store.ingest_event(make_event(project_id="project-a"))
    store.ingest_event(
        make_event(event_id="event-2", project_id="project-b")
    )

    assert store.count_events("project-a") == 1
    assert store.count_events("project-b") == 1
    assert store.list_projects() == ["project-a", "project-b"]


def test_timezone_is_normalized_to_utc():
    event = make_event(
        occurred_at="2026-10-17T10:00:00+05:30"
    )

    assert event.occurred_at == "2026-10-17T04:30:00.000000+00:00"


def test_timezone_is_required():
    with pytest.raises(EventValidationError):
        make_event(occurred_at="2026-10-17T10:00:00")
