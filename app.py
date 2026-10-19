from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from living_state_engine import (
    Event,
    EventValidationError,
    RecommendationEngine,
    SQLiteEventStore,
    SUPPORTED_EVENT_TYPES,
)
from living_state_engine.state import StateEngine


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "synthetic"
EVENTS_FILE = DATA_DIR / "events.jsonl"
MANIFEST_FILE = DATA_DIR / "manifest.json"
RESULTS_FILE = DATA_DIR / "evaluation_results.json"
DATABASE_FILE = ROOT / "data" / "living_state_engine.db"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [
            json.loads(line)
            for line in handle
            if line.strip()
        ]


def load_dataset(store: SQLiteEventStore) -> tuple[int, int]:
    if not EVENTS_FILE.exists():
        raise FileNotFoundError(
            "Synthetic events are missing. Run "
            "scripts\\generate_dataset.py first."
        )

    inserted = 0
    duplicates = 0

    for row in read_jsonl(EVENTS_FILE):
        result = store.ingest_event(Event.from_dict(row))
        if result == "inserted":
            inserted += 1
        else:
            duplicates += 1

    return inserted, duplicates


def render_items(title: str, items: tuple[dict, ...]) -> None:
    st.subheader(title)
    if items:
        st.dataframe(
            [dict(item) for item in items],
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("None recorded.")


st.set_page_config(
    page_title="Living State Engine",
    page_icon="L",
    layout="wide",
)

st.title("Living State Engine")
st.caption(
    "Evidence-backed project state reconstruction, "
    "recommendations, and outcome tracking."
)
st.caption(f"Local SQLite database: `{DATABASE_FILE}`")

with SQLiteEventStore(DATABASE_FILE) as store:
    with st.sidebar:
        st.header("Event stream")

        if st.button("Load synthetic dataset", width="stretch"):
            try:
                inserted, duplicates = load_dataset(store)
                st.success(
                    f"Loaded {inserted} new events; "
                    f"ignored {duplicates} duplicates."
                )
            except (OSError, ValueError) as exc:
                st.error(str(exc))

        st.caption(
            "The dataset is synthetic. Loading it again is safe: "
            "identical event IDs are deduplicated."
        )

        st.divider()
        st.write(f"Stored events: **{store.count_events()}**")
        st.write(f"Projects: **{len(store.list_projects())}**")

    project_options = store.list_projects()

    if not project_options:
        st.info(
            "No events are loaded yet. Use **Load synthetic dataset** "
            "in the sidebar, or generate the dataset first with "
            "`python scripts/generate_dataset.py`."
        )
        st.stop()

    preferred_project = "high_risk_contradiction-01"
    default_index = (
        project_options.index(preferred_project)
        if preferred_project in project_options
        else 0
    )

    project_id = st.selectbox(
        "Project",
        options=project_options,
        index=default_index,
        key="selected_project",
    )

    # Manual event ingestion makes the interface useful beyond the
    # prebuilt examples. Events are always re-sorted by event time.
    with st.expander("Ingest a new event"):
        with st.form("ingest_event_form"):
            event_id = st.text_input("Unique event ID")
            occurred_at = st.text_input(
                "Event time (ISO 8601, including timezone)",
                value=datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
            )
            event_type = st.selectbox(
                "Event type",
                options=sorted(SUPPORTED_EVENT_TYPES),
            )
            summary = st.text_input("Summary")
            source = st.text_input("Source", value="manual")
            actor = st.text_input("Actor", value="project owner")
            payload_text = st.text_area(
                "Payload JSON object",
                value="{}",
                height=120,
            )
            ingest_clicked = st.form_submit_button(
                "Add event",
                width="stretch",
            )

        if ingest_clicked:
            try:
                payload = json.loads(payload_text)
                if not isinstance(payload, dict):
                    raise ValueError("Payload must be a JSON object.")

                event = Event(
                    event_id=event_id,
                    project_id=project_id,
                    occurred_at=occurred_at,
                    event_type=event_type,
                    summary=summary,
                    source=source,
                    actor=actor,
                    payload=payload,
                )
                result = store.ingest_event(event)

                if result == "inserted":
                    st.success(
                        "Event stored. The project history will be "
                        "reconstructed using event time."
                    )
                    st.session_state["pending_state_trigger"] = (
                        project_id,
                        event.event_id,
                    )
                else:
                    st.info("This exact event already exists.")

            except (
                json.JSONDecodeError,
                EventValidationError,
                ValueError,
            ) as exc:
                st.error(f"Event not accepted: {exc}")

    # Refresh after ingestion so newly added or late-arriving events
    # immediately appear in chronological order.
    project_events = store.list_events(project_id)
    
    full_history = StateEngine().replay(project_events)

    pending_trigger = st.session_state.get("pending_state_trigger")
    trigger_event_id = (
        pending_trigger[1]
        if pending_trigger and pending_trigger[0] == project_id
        else project_events[-1].event_id if project_events else None
    )

    if full_history:
        store.persist_state_history(
            project_id,
            full_history,
            trigger_event_id=trigger_event_id,
        )

        if pending_trigger and pending_trigger[0] == project_id:
            del st.session_state["pending_state_trigger"]

    st.subheader("Replay controls")
    st.write(
        f"**{len(project_events)} events** stored for "
        f"`{project_id}`."
    )

    replay_count = st.slider(
        "Events replayed",
        min_value=0,
        max_value=len(project_events),
        value=min(2, len(project_events)),
        step=1,
        key=f"replay_count_{project_id}",
        help=(
            "Reconstruct the project state using the first N events "
            "in event-time order."
        ),
    )

    replayed_events = project_events[:replay_count]
    history = StateEngine().replay(replayed_events)
    current_state = history[-1] if history else None

    tabs = st.tabs(
        [
            "Current state & evidence",
            "History",
            "Commitments, risks & issues",
            "Recommendations & outcomes",
            "Evaluation",
        ]
    )

    with tabs[0]:
        if current_state is None:
            st.info(
                "No events replayed. Move the slider forward "
                "to reconstruct a state."
            )
        else:
            col1, col2, col3 = st.columns(3)
            col1.metric("Estimated status", current_state.status)
            col2.metric("Events replayed", replay_count)
            col3.metric("State version", current_state.version)

            st.subheader("Why the engine believes this")
            st.write(current_state.status_reason)

            st.subheader("Evidence supporting the current estimate")
            event_lookup = {
                event.event_id: event
                for event in replayed_events
            }

            if current_state.evidence_event_ids:
                evidence_rows = []
                for event_id in current_state.evidence_event_ids:
                    event = event_lookup.get(event_id)
                    if event is None:
                        continue

                    evidence_rows.append({
                        "event_id": event.event_id,
                        "occurred_at": event.occurred_at,
                        "event_type": event.event_type,
                        "summary": event.summary,
                        "source": event.source,
                        "payload": json.dumps(
                            event.payload,
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    })

                st.dataframe(
                    evidence_rows,
                    hide_index=True,
                    width="stretch",
                )
            else:
                st.caption(
                    "No explicit evidence references have been recorded."
                )

            st.subheader("Events in this replay")
            st.dataframe(
                [
                    {
                        "event_id": event.event_id,
                        "occurred_at": event.occurred_at,
                        "type": event.event_type,
                        "summary": event.summary,
                    }
                    for event in project_events
                ],
                hide_index=True,
                width="stretch",
            )

    with tabs[1]:
        st.subheader("Reconstructed state history")

        if not history:
            st.info("Replay at least one event to see history.")
        else:
            st.dataframe(
                [
                    {
                        "version": version.version,
                        "event_id": version.as_of_event_id,
                        "occurred_at": version.occurred_at,
                        "status": version.status,
                        "changes": " ".join(version.changes),
                        "reason": version.status_reason,
                    }
                    for version in history
                ],
                hide_index=True,
                width="stretch",
            )

            selected_version = st.selectbox(
                "Inspect a historical version",
                options=list(range(1, len(history) + 1)),
                index=len(history) - 1,
                format_func=lambda version: (
                    f"Version {version}: {history[version - 1].status}"
                ),
                key=f"history_version_{project_id}",
            )

            st.json(history[selected_version - 1].to_dict())
            st.divider()
            st.subheader("Persisted event-time snapshots")

            snapshots = store.list_state_snapshots(project_id)

            if snapshots:
                st.dataframe(
                    [
                        {
                            "version": item["version"],
                            "event_id": item["as_of_event_id"],
                            "occurred_at": item["occurred_at"],
                            "status": item["state"]["status"],
                            "evidence": ", ".join(
                                item["state"]["evidence_event_ids"]
                            ),
                        }
                        for item in snapshots
                    ],
                    hide_index=True,
                    width="stretch",
                )
            else:
                st.caption("No persisted snapshots yet.")

            st.subheader("Recorded state revisions")

            revisions = store.list_state_revisions(project_id)

            if revisions:
                st.dataframe(
                    [
                        {
                            "revision": item["revision_number"],
                            "trigger_event": item["trigger_event_id"],
                            "state_as_of": item["state_as_of_event_id"],
                            "status": item["state"]["status"],
                            "recorded_at": item["generated_at"],
                        }
                        for item in revisions
                    ],
                    hide_index=True,
                    width="stretch",
                )
            else:
                st.caption("No state revisions recorded yet.")

            st.caption(
                "History is reconstructed from the event ledger. "
                "It is deterministic for a given event set, but late-arriving "
                "events can change earlier reconstructed versions."
            )

    with tabs[2]:
        if current_state is None:
            st.info("Replay at least one event to inspect project details.")
        else:
            render_items(
                "Commitments",
                current_state.commitments,
            )
            render_items(
                "Risks",
                current_state.risks,
            )
            render_items(
                "Unresolved issues",
                current_state.issues,
            )

    with tabs[3]:
        if current_state is None:
            st.info("Replay at least one event before generating advice.")
        else:
            recommendation = RecommendationEngine().recommend(
                current_state
            )

            st.subheader("Recommended next action")
            st.write(recommendation.action)

            st.subheader("Reasoning")
            st.write(recommendation.rationale)

            st.write(
                "**Evidence event IDs:** "
                + (
                    ", ".join(recommendation.evidence_event_ids)
                    or "None"
                )
            )

            st.write(
                "**Expected outcome:** "
                f"`{recommendation.expected_metric} "
                f"{recommendation.expected_operator} "
                f"{json.dumps(recommendation.expected_value)}`"
            )

            st.caption(
                "The recommendation is rule-based. Its expected outcome "
                "is a measurable criterion, not a guarantee."
            )

            if st.button(
                "Save recommendation",
                key=f"save_rec_{project_id}_{current_state.version}",
            ):
                try:
                    result = store.save_recommendation(recommendation)
                    st.success(f"Recommendation: {result}.")
                except ValueError as exc:
                    st.error(str(exc))

            saved_recommendations = store.list_recommendations(project_id)

            st.divider()
            st.subheader("Record an actual outcome")

            if not saved_recommendations:
                st.info(
                    "Save a recommendation first. Record an outcome "
                    "only after the result has actually been observed."
                )
            else:
                recommendation_lookup = {
                    item["recommendation_id"]: item
                    for item in saved_recommendations
                }

                selected_recommendation_id = st.selectbox(
                    "Saved recommendation",
                    options=list(recommendation_lookup),
                    format_func=lambda recommendation_id: (
                        f"{recommendation_id}: "
                        f"{recommendation_lookup[recommendation_id]['action']}"
                    ),
                    key=f"outcome_rec_{project_id}",
                )
                selected_recommendation = recommendation_lookup[
                    selected_recommendation_id
                ]

                st.write(
                    "**Expected value:** "
                    f"`{json.dumps(selected_recommendation['expected_value'])}`"
                )

                with st.form(
                    f"record_outcome_{selected_recommendation_id}"
                ):
                    actual_outcome = st.text_input(
                        "What actually happened?"
                    )
                    actual_value_text = st.text_input(
                        "Actual value (valid JSON)",
                        placeholder='"resolved", "open", or true',
                    )
                    observed_at = st.text_input(
                        "Observation time (optional ISO 8601)",
                        placeholder="Leave blank to use current UTC time",
                    )
                    outcome_notes = st.text_area(
                        "Verification notes",
                        placeholder=(
                            "Where the result was verified and "
                            "any relevant context."
                        ),
                    )
                    outcome_submitted = st.form_submit_button(
                        "Record outcome",
                    )

                if outcome_submitted:
                    try:
                        if not actual_value_text.strip():
                            raise ValueError(
                                "Provide the observed value as valid JSON."
                            )

                        actual_value = json.loads(actual_value_text)

                        result = store.record_outcome(
                            recommendation_id=selected_recommendation_id,
                            actual_outcome=actual_outcome,
                            actual_value=actual_value,
                            notes=outcome_notes,
                            observed_at=observed_at.strip() or None,
                        )
                        st.success(f"Outcome: {result}.")

                    except (
                        json.JSONDecodeError,
                        ValueError,
                    ) as exc:
                        st.error(f"Outcome not recorded: {exc}")

                st.subheader("Recorded recommendations")
                st.dataframe(
                    [
                        {
                            "recommendation_id": item["recommendation_id"],
                            "action": item["action"],
                            "expected_metric": item["expected_metric"],
                            "expected_value": json.dumps(
                                item["expected_value"]
                            ),
                            "actual_outcome": (
                                item["outcome"]["actual_outcome"]
                                if item["outcome"]
                                else "Not recorded"
                            ),
                            "worked": (
                                item["outcome"]["worked"]
                                if item["outcome"]
                                else None
                            ),
                        }
                        for item in store.list_recommendations(project_id)
                    ],
                    hide_index=True,
                    width="stretch",
                )

    with tabs[4]:
        st.subheader("Synthetic evaluation")

        if MANIFEST_FILE.exists():
            manifest = json.loads(
                MANIFEST_FILE.read_text(encoding="utf-8")
            )
            st.warning(
                "Synthetic data only. Repeated templates and "
                "rule-aligned labels do not establish real-world accuracy."
            )
            st.json(manifest)
        else:
            st.info(
                "Generate the synthetic dataset before running evaluation."
            )

        if st.button("Run benchmark", key="run_benchmark"):
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "scripts" / "evaluate_dataset.py"),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )

                if completed.returncode != 0:
                    st.error("Benchmark failed.")
                    st.code(completed.stderr or completed.stdout)
                else:
                    st.success("Benchmark completed.")
            except (OSError, subprocess.TimeoutExpired) as exc:
                st.error(f"Could not run benchmark: {exc}")

        if RESULTS_FILE.exists():
            report = json.loads(
                RESULTS_FILE.read_text(encoding="utf-8")
            )

            engine_accuracy = report["state_accuracy"]["state_engine"]
            baseline_accuracy = report["state_accuracy"][
                "latest_explicit_status_baseline"
            ]
            engine_risk = report["risk_detection"]["state_engine"]
            baseline_risk = report["risk_detection"][
                "latest_explicit_status_baseline"
            ]

            cols = st.columns(3)
            cols[0].metric(
                "Engine accuracy",
                f"{engine_accuracy['accuracy']:.1%}",
                f"{engine_accuracy['correct']}/{engine_accuracy['total']}",
            )
            cols[1].metric(
                "Baseline accuracy",
                f"{baseline_accuracy['accuracy']:.1%}",
                f"{baseline_accuracy['correct']}/{baseline_accuracy['total']}",
            )
            cols[2].metric(
                "Engine risk F1",
                f"{engine_risk['f1']:.2f}",
                f"Baseline: {baseline_risk['f1']:.2f}",
            )

            st.subheader("Full evaluation report")
            st.json(report)
        else:
            st.info(
                "No evaluation results yet. Run the benchmark above."
            )