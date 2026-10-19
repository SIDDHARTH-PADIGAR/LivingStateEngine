## This module persists events and provides the chronological query the state engine will need.
## ingested_at and occured_at serve different purposes.
## The former records when the underlying event happened. That distinction is essential when we test out of order arrivals.

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Literal
import hashlib
import hashlib
from collections.abc import Iterable

from living_state_engine.events import Event, normalize_timestamp
from living_state_engine.recommendations import Recommendation




class DuplicateEventConflict(ValueError):
    """An existing event ID was reused with different content."""

class RecommendationConflict(ValueError):
    """A recommendation ID was reused with different content."""


class OutcomeConflict(ValueError):
    """A recorded outcome conflicts with an existing outcome."""

class SQLiteEventStore:
    """SQLite-backed event ledger with idempotent ingestion."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)

        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(
                parents=True, exist_ok=True
            )

        self._lock = RLock()
        self._connection = sqlite3.connect(
            self.database_path,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")

        if self.database_path != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")

        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    source TEXT NOT NULL,
                    actor TEXT,
                    payload_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    ingested_at TEXT NOT NULL
                )
                """
            )

            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_project_time
                ON events(project_id, occurred_at, event_id)
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS state_snapshots (
                    project_id TEXT NOT NULL,
                    as_of_event_id TEXT NOT NULL,
                    event_occurred_at TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    state_hash TEXT NOT NULL,
                    computed_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, as_of_event_id)
                )
                """
            )

            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_state_snapshots_project_time
                ON state_snapshots(
                    project_id, event_occurred_at, as_of_event_id
                )
                """
            )

            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS state_revisions (
                    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    revision_number INTEGER NOT NULL,
                    trigger_event_id TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    state_as_of_event_id TEXT NOT NULL,
                    state_hash TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    UNIQUE(project_id, revision_number)
                )
                """
            )
            self._connection.commit()
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recommendations (
                    recommendation_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    recommendation_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_recommendations_project
                ON recommendations(project_id, created_at)
                """
            )

            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recommendation_outcomes (
                    recommendation_id TEXT PRIMARY KEY,
                    observed_at TEXT NOT NULL,
                    actual_outcome TEXT NOT NULL,
                    actual_value_json TEXT NOT NULL,
                    worked INTEGER NOT NULL CHECK (worked IN (0, 1)),
                    notes TEXT NOT NULL,
                    outcome_hash TEXT NOT NULL,
                    FOREIGN KEY (recommendation_id)
                        REFERENCES recommendations(recommendation_id)
                )
                """
            )

    def ingest_event(
        self, event: Event
    ) -> Literal["inserted", "duplicate"]:
        """Insert an event, ignore exact replays, reject ID conflicts."""
        with self._lock:
            try:
                existing = self._connection.execute(
                    """
                    SELECT content_hash
                    FROM events
                    WHERE event_id = ?
                    """,
                    (event.event_id,),
                ).fetchone()

                if existing is not None:
                    if existing["content_hash"] == event.content_hash:
                        return "duplicate"

                    raise DuplicateEventConflict(
                        f"Event ID {event.event_id!r} already exists "
                        "with different content"
                    )

                ingested_at = datetime.now(
                    timezone.utc
                ).isoformat(timespec="microseconds")

                self._connection.execute(
                    """
                    INSERT INTO events (
                        event_id,
                        project_id,
                        occurred_at,
                        event_type,
                        summary,
                        source,
                        actor,
                        payload_json,
                        content_hash,
                        ingested_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.project_id,
                        event.occurred_at,
                        event.event_type,
                        event.summary,
                        event.source,
                        event.actor,
                        json.dumps(
                            event.payload,
                            sort_keys=True,
                            ensure_ascii=False,
                            allow_nan=False,
                        ),
                        event.content_hash,
                        ingested_at,
                    ),
                )

                self._connection.commit()
                return "inserted"

            except Exception:
                self._connection.rollback()
                raise

    def list_events(self, project_id: str) -> list[Event]:
        """Return events in event-time order, with a stable tie-break."""
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT *
                FROM events
                WHERE project_id = ?
                ORDER BY occurred_at ASC, event_id ASC
                """,
                (project_id,),
            ).fetchall()

        return [self._row_to_event(row) for row in rows]

    def get_event(self, event_id: str) -> Event | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()

        return None if row is None else self._row_to_event(row)

    def list_projects(self) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT DISTINCT project_id
                FROM events
                ORDER BY project_id
                """
            ).fetchall()

        return [row["project_id"] for row in rows]

    def count_events(self, project_id: str | None = None) -> int:
        with self._lock:
            if project_id is None:
                row = self._connection.execute(
                    "SELECT COUNT(*) AS total FROM events"
                ).fetchone()
            else:
                row = self._connection.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM events
                    WHERE project_id = ?
                    """,
                    (project_id,),
                ).fetchone()

        return int(row["total"])

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        return Event(
            event_id=row["event_id"],
            project_id=row["project_id"],
            occurred_at=row["occurred_at"],
            event_type=row["event_type"],
            summary=row["summary"],
            source=row["source"],
            actor=row["actor"],
            payload=json.loads(row["payload_json"]),
        )
    def save_recommendation(
        self,
        recommendation: Recommendation,
    ) -> Literal["inserted", "duplicate"]:
        """Persist a recommendation idempotently."""
        serialized = json.dumps(
            recommendation.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

        with self._lock:
            try:
                existing = self._connection.execute(
                    """
                    SELECT recommendation_json
                    FROM recommendations
                    WHERE recommendation_id = ?
                    """,
                    (recommendation.recommendation_id,),
                ).fetchone()

                if existing is not None:
                    if existing["recommendation_json"] == serialized:
                        return "duplicate"
                    raise RecommendationConflict(
                        f"Recommendation ID "
                        f"{recommendation.recommendation_id!r} "
                        "already exists with different content"
                    )

                created_at = datetime.now(
                    timezone.utc
                ).isoformat(timespec="microseconds")

                self._connection.execute(
                    """
                    INSERT INTO recommendations (
                        recommendation_id,
                        project_id,
                        recommendation_json,
                        created_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        recommendation.recommendation_id,
                        recommendation.project_id,
                        serialized,
                        created_at,
                    ),
                )
                self._connection.commit()
                return "inserted"

            except Exception:
                self._connection.rollback()
                raise

    def record_outcome(
        self,
        recommendation_id: str,
        actual_outcome: str,
        actual_value: Any,
        notes: str = "",
        observed_at: str | None = None,
    ) -> Literal["recorded", "duplicate"]:
        """Record an outcome and derive whether the intervention worked."""
        if not actual_outcome.strip():
            raise ValueError("actual_outcome must not be empty")

        if not isinstance(notes, str):
            raise ValueError("notes must be a string")

        try:
            actual_json = json.dumps(
                actual_value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "actual_value must be JSON-compatible"
            ) from exc

        with self._lock:
            try:
                row = self._connection.execute(
                    """
                    SELECT recommendation_json
                    FROM recommendations
                    WHERE recommendation_id = ?
                    """,
                    (recommendation_id,),
                ).fetchone()

                if row is None:
                    raise ValueError(
                        f"Unknown recommendation: {recommendation_id}"
                    )

                recommendation = json.loads(
                    row["recommendation_json"]
                )
                expected = recommendation["expected_value"]
                operator = recommendation["expected_operator"]

                if operator == "eq":
                    worked = actual_json == json.dumps(
                        expected,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                elif operator == "in" and isinstance(expected, list):
                    worked = any(
                        actual_json == json.dumps(
                            candidate,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                            allow_nan=False,
                        )
                        for candidate in expected
                    )
                else:
                    raise ValueError(
                        f"Unsupported outcome operator: {operator!r}"
                    )

                outcome_content = {
                    "actual_outcome": actual_outcome,
                    "actual_value": json.loads(actual_json),
                    "notes": notes,
                }
                outcome_hash = hashlib.sha256(
                    json.dumps(
                        outcome_content,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    ).encode("utf-8")
                ).hexdigest()

                existing = self._connection.execute(
                    """
                    SELECT outcome_hash
                    FROM recommendation_outcomes
                    WHERE recommendation_id = ?
                    """,
                    (recommendation_id,),
                ).fetchone()

                if existing is not None:
                    if existing["outcome_hash"] == outcome_hash:
                        return "duplicate"
                    raise OutcomeConflict(
                        f"An outcome is already recorded for "
                        f"{recommendation_id!r}"
                    )

                timestamp = normalize_timestamp(
                    observed_at
                    or datetime.now(timezone.utc)
                )

                self._connection.execute(
                    """
                    INSERT INTO recommendation_outcomes (
                        recommendation_id,
                        observed_at,
                        actual_outcome,
                        actual_value_json,
                        worked,
                        notes,
                        outcome_hash
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        recommendation_id,
                        timestamp,
                        actual_outcome,
                        actual_json,
                        int(worked),
                        notes,
                        outcome_hash,
                    ),
                )
                self._connection.commit()
                return "recorded"

            except Exception:
                self._connection.rollback()
                raise

    def get_recommendation(
        self, recommendation_id: str
    ) -> dict[str, Any] | None:
        """Fetch a recommendation and its optional outcome."""
        with self._lock:
            row = self._connection.execute(
                """
                SELECT
                    r.recommendation_json,
                    r.created_at,
                    o.observed_at,
                    o.actual_outcome,
                    o.actual_value_json,
                    o.worked,
                    o.notes
                FROM recommendations AS r
                LEFT JOIN recommendation_outcomes AS o
                    ON o.recommendation_id = r.recommendation_id
                WHERE r.recommendation_id = ?
                """,
                (recommendation_id,),
            ).fetchone()

        if row is None:
            return None

        result = json.loads(row["recommendation_json"])
        result["created_at"] = row["created_at"]

        result["outcome"] = (
            None
            if row["observed_at"] is None
            else {
                "observed_at": row["observed_at"],
                "actual_outcome": row["actual_outcome"],
                "actual_value": json.loads(row["actual_value_json"]),
                "worked": bool(row["worked"]),
                "notes": row["notes"],
            }
        )
        return result

    def list_recommendations(
        self, project_id: str
    ) -> list[dict[str, Any]]:
        """List recommendations and outcomes for a project."""
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT recommendation_id
                FROM recommendations
                WHERE project_id = ?
                ORDER BY created_at, recommendation_id
                """,
                (project_id,),
            ).fetchall()

        return [
            result
            for row in rows
            if (
                result := self.get_recommendation(
                    row["recommendation_id"]
                )
            ) is not None
        ]      

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> SQLiteEventStore:
        return self

    def persist_state_history(
        self,
        project_id: str,
        history: Iterable[Any],
        trigger_event_id: str | None = None,
    ) -> int:
        """Persist event-time snapshots and append changed current states."""
        versions = list(history)

        if any(version.project_id != project_id for version in versions):
            raise ValueError("All state versions must belong to this project")

        if versions and (
            len({version.as_of_event_id for version in versions})
            != len(versions)
        ):
            raise ValueError("State history contains duplicate event IDs")

        prepared = []
        for version in versions:
            serialized = json.dumps(
                version.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            state_hash = hashlib.sha256(
                serialized.encode("utf-8")
            ).hexdigest()
            prepared.append((version, serialized, state_hash))

        now = datetime.now(timezone.utc).isoformat(
            timespec="microseconds"
        )

        with self._lock:
            try:
                # Refresh the event-time snapshots in place.
                for version, serialized, state_hash in prepared:
                    self._connection.execute(
                        """
                        INSERT INTO state_snapshots (
                            project_id,
                            as_of_event_id,
                            event_occurred_at,
                            version,
                            state_json,
                            state_hash,
                            computed_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(project_id, as_of_event_id)
                        DO UPDATE SET
                            event_occurred_at = excluded.event_occurred_at,
                            version = excluded.version,
                            state_json = excluded.state_json,
                            state_hash = excluded.state_hash,
                            computed_at = excluded.computed_at
                        """,
                        (
                            project_id,
                            version.as_of_event_id,
                            version.occurred_at,
                            version.version,
                            serialized,
                            state_hash,
                            now,
                        ),
                    )

                if not prepared:
                    self._connection.execute(
                        """
                        DELETE FROM state_snapshots
                        WHERE project_id = ?
                        """,
                        (project_id,),
                    )
                    self._connection.commit()
                    return 0

                # Only append a current-state revision when its content
                # differs from the last recorded revision.
                latest, serialized, state_hash = prepared[-1]

                previous = self._connection.execute(
                    """
                    SELECT revision_number, state_hash
                    FROM state_revisions
                    WHERE project_id = ?
                    ORDER BY revision_number DESC
                    LIMIT 1
                    """,
                    (project_id,),
                ).fetchone()

                if previous is None or previous["state_hash"] != state_hash:
                    revision_number = (
                        1 if previous is None
                        else previous["revision_number"] + 1
                    )

                    self._connection.execute(
                        """
                        INSERT INTO state_revisions (
                            project_id,
                            revision_number,
                            trigger_event_id,
                            generated_at,
                            state_as_of_event_id,
                            state_hash,
                            state_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            project_id,
                            revision_number,
                            trigger_event_id or latest.as_of_event_id,
                            now,
                            latest.as_of_event_id,
                            state_hash,
                            serialized,
                        ),
                    )

                self._connection.commit()
                return len(prepared)

            except Exception:
                self._connection.rollback()
                raise

    def list_state_snapshots(
        self, project_id: str
    ) -> list[dict[str, Any]]:
        """Return the latest reconstructed state at every event time."""
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT *
                FROM state_snapshots
                WHERE project_id = ?
                ORDER BY event_occurred_at, as_of_event_id
                """,
                (project_id,),
            ).fetchall()

        return [
            {
                "version": row["version"],
                "as_of_event_id": row["as_of_event_id"],
                "occurred_at": row["event_occurred_at"],
                "computed_at": row["computed_at"],
                "state_hash": row["state_hash"],
                "state": json.loads(row["state_json"]),
            }
            for row in rows
        ]

    def list_state_revisions(
        self, project_id: str
    ) -> list[dict[str, Any]]:
        """Return the append-only record of previously stored estimates."""
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT *
                FROM state_revisions
                WHERE project_id = ?
                ORDER BY revision_number
                """,
                (project_id,),
            ).fetchall()

        return [
            {
                "revision_number": row["revision_number"],
                "trigger_event_id": row["trigger_event_id"],
                "generated_at": row["generated_at"],
                "state_as_of_event_id": row["state_as_of_event_id"],
                "state_hash": row["state_hash"],
                "state": json.loads(row["state_json"]),
            }
            for row in rows
        ]
    
    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
